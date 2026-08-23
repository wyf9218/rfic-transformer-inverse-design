#!/usr/bin/env python3
"""Build a runbook packet for physical-feature inverse design to .s8p EMX data.

This script does not run Cadence, EMX, HFSS, or ADS. It writes an auditable
commands file that connects the current pipeline pieces:

1. Build Lp/Ls/Q/K -> geometry inverse training table.
2. Predict candidate geometries from target physical features.
3. Run one candidate through create-only layout generation and 8-port layout
   audit before any expensive EMX queue starts.
4. Run the candidate queue through the 8-worker EMX dataset runner.
5. Run .s8p physical-feature quality gates and select a validation sample.
6. Package lightweight MARS artifacts for local review.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.core import load_run_config  # noqa: E402
from rfic_transformer_inverse_design.paths import bundled_proc_dir, resolve_local_path  # noqa: E402


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _repo_script(script_name: str) -> str:
    return f"${{REPO_ROOT}}/scripts/{script_name}"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = REPO_ROOT
    dataset_dir = Path(args.dataset_dir).expanduser().resolve() if args.dataset_dir else None
    config = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else out_dir / "s8p_emx_candidate_run"
    quality_dir = run_dir / "dataset_quality_gates_s8p_physical_feature"
    out_dir.mkdir(parents=True, exist_ok=True)

    targets, target_errors = _parse_targets(args.inverse_target, args.inverse_target_json)
    feature_columns = _resolved_feature_columns(args)
    missing_target_features = _missing_target_features(targets, feature_columns)
    feature_contract_checks = _feature_contract_checks(feature_columns)
    expected_bridge_width_um = _optional_float(args.expected_bridge_width_um)
    config_status, config_detail = _config_status(config, expected_bridge_width_um, float(args.bridge_width_tolerance_um))
    approval_status, approval_detail, approval_summary = _port_map_approval_status(
        args.port_map_approval_summary,
        args.validation_port_pairs,
    )
    geometry_status, geometry_detail, geometry_summary = _geometry_contract_approval_status(
        args.geometry_contract_approval_summary,
        args.validation_port_pairs,
        expected_bridge_width_um,
        float(args.expected_ground_frame_width_um),
    )
    checks = [
        _check(
            "dataset_dir_exists_or_bootstrap_mode",
            bool(args.bootstrap_geometry_candidate_queue) or (dataset_dir is not None and dataset_dir.is_dir()),
            "bootstrap geometry mode does not require an existing physical-feature dataset"
            if args.bootstrap_geometry_candidate_queue
            else str(dataset_dir),
        ),
        _check("config_path_exists", config.is_file(), str(config)),
        _check("config_loads", config_status == "PASS", config_detail),
        _check("port_map_approval_summary_approved", approval_status == "PASS", approval_detail),
        _check("geometry_contract_approval_summary_approved", geometry_status == "PASS", geometry_detail),
        _check(
            "target_features_present_or_bootstrap_mode",
            bool(args.bootstrap_geometry_candidate_queue) or bool(targets),
            "bootstrap geometry mode uses config bounds instead of inverse target features"
            if args.bootstrap_geometry_candidate_queue
            else f"targets={len(targets)}",
        ),
        _check("target_features_parse", not target_errors, "; ".join(target_errors)),
        _check(
            "target_features_match_columns_or_bootstrap_mode",
            bool(args.bootstrap_geometry_candidate_queue) or not missing_target_features,
            "bootstrap geometry mode has no target feature vector"
            if args.bootstrap_geometry_candidate_queue
            else str(missing_target_features),
        ),
        *feature_contract_checks,
        _check("candidate_count_positive", int(args.inverse_candidate_count) > 0, args.inverse_candidate_count),
        _check("emx_max_count_positive", int(args.emx_max_count) > 0, args.emx_max_count),
        _check("jobs_positive", int(args.jobs) > 0, args.jobs),
        _check(
            "emx_sample_count_matches_goal",
            int(args.emx_max_count) == int(args.expected_emx_count),
            f"emx_max_count={args.emx_max_count}, expected_emx_count={args.expected_emx_count}",
        ),
        _check(
            "parallel_worker_count_matches_goal",
            int(args.jobs) == int(args.expected_jobs),
            f"jobs={args.jobs}, expected_jobs={args.expected_jobs}",
        ),
    ]
    overall_status = "PASS" if all(item["pass"] for item in checks) else "NOT_READY"
    commands_path = out_dir / "physical_feature_s8p_launch.commands.sh"
    target_json = out_dir / "physical_feature_inverse_targets.json"
    commands, inverse_artifacts = _build_commands(
        repo_root,
        dataset_dir,
        config,
        out_dir,
        run_dir,
        quality_dir,
        target_json,
        args,
        has_inverse_targets=bool(targets),
    )
    summary_path = out_dir / "physical_feature_s8p_launch_packet_summary.json"
    report_path = out_dir / "physical_feature_s8p_launch_packet_report.md"
    target_json.write_text(json.dumps(targets, indent=2, ensure_ascii=False), encoding="utf-8")
    commands_path.write_text(_render_commands(commands, repo_root), encoding="utf-8")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "READY_TO_REVIEW_AND_RUN_ON_MARS" if overall_status == "PASS" else "DO_NOT_RUN_UNTIL_CHECKS_PASS",
        "dataset_dir": "" if dataset_dir is None else str(dataset_dir),
        "candidate_source_mode": "bootstrap_geometry_queue" if args.bootstrap_geometry_candidate_queue else "physical_feature_inverse_prediction",
        "config": str(config),
        "port_map_approval_summary": approval_summary,
        "geometry_contract_approval_summary": geometry_summary,
        "combined_approval_summary": args.combined_approval_summary or "",
        "out_dir": str(out_dir),
        "run_dir": str(run_dir),
        "quality_dir": str(quality_dir),
        "inverse_model_artifacts": inverse_artifacts,
        "commands_path": str(commands_path),
        "target_json": str(target_json),
        "feature_columns": feature_columns,
        "input_feature_contract": _input_feature_contract(feature_columns),
        "targets": targets,
        "parallel_emx_contract": {
            "expected_emx_count": int(args.expected_emx_count),
            "emx_max_count": int(args.emx_max_count),
            "expected_jobs": int(args.expected_jobs),
            "jobs": int(args.jobs),
            "wideband_frequency_grid": "5-60 GHz inclusive, 0.5 GHz step, 111 points",
            "max_touchstone_files_checked": int(args.s8p_touchstone_checks),
            "touchstone_check_scope": "checks all available successful .s8p files up to this limit; default covers a full 500-row run",
        },
        "checks": checks,
        "commands": commands,
        "arguments": vars(args),
        "limitations": [
            "This packet only writes commands; it does not run EMX/HFSS/ADS.",
            "Bootstrap geometry mode creates no physical-feature labels; labels still come only from the later EMX `.s8p` run.",
            "The generated command file includes a one-sample create-only layout smoke/audit before the 500-sample EMX queue.",
            "The config and --port-map-approval-summary must contain the final approved P001-P008 port map and differential port pairs before MARS execution.",
            "The config and --geometry-contract-approval-summary must contain the final approved S8P geometry contract before MARS execution.",
            "The final dataset is accepted only after .s8p quality gates plus random-sample EMX/HFSS/ADS Lp/Ls/Q/K/Kw curve comparison.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"commands={commands_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", help="Existing real-label dataset used for inverse training")
    parser.add_argument(
        "--bootstrap-geometry-candidate-queue",
        action="store_true",
        help="Cold-start mode: build candidates directly from config geometry bounds instead of an existing physical-feature dataset",
    )
    parser.add_argument("--bootstrap-sampler", choices=["lhs", "lhs_optimized", "sobol"], default="lhs_optimized")
    parser.add_argument("--bootstrap-seed", type=int, default=20260616)
    parser.add_argument("--bootstrap-oversample-factor", type=float, default=4.0)
    parser.add_argument("--bootstrap-max-sampling-rounds", type=int, default=20)
    parser.add_argument("--config", required=True, help="Final S8P run config for EMX candidate queue")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run-dir", help="Output directory for the new EMX candidate run")
    parser.add_argument("--physical-feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--scalar-q-definition", default="min", choices=["min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary"])
    parser.add_argument("--scalar-q-output-column", default="q_center")
    parser.add_argument("--inverse-target", action="append", default=[], help="Target physical feature as name=value")
    parser.add_argument("--inverse-target-json", help="JSON dict/list of target physical features")
    parser.add_argument("--inverse-candidate-count", type=int, default=500)
    parser.add_argument("--inverse-k-neighbors", type=int, default=8)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--emx-max-count", type=int, default=500)
    parser.add_argument("--expected-emx-count", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument(
        "--expected-bridge-width-um",
        type=float,
        default=None,
        help=(
            "Optional legacy fixed bridge-width check. Omit for current shared-line-width runs; "
            "layout/HFSS handoff audits then use each sample's recorded line_width_um."
        ),
    )
    parser.add_argument("--bridge-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument("--expected-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--ground-frame-width-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument(
        "--expected-ground-frame-policy",
        default="power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
    )
    parser.add_argument(
        "--port-map-approval-summary",
        help="Approved s8p_port_map_approval_summary.json. Candidate/unapproved summaries keep this launch packet NOT_READY.",
    )
    parser.add_argument(
        "--geometry-contract-approval-summary",
        help="Approved s8p_geometry_contract_approval_summary.json. Candidate/unapproved summaries keep this launch packet NOT_READY.",
    )
    parser.add_argument(
        "--combined-approval-summary",
        default="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json",
        help="Approved s8p_combined_approval_readiness_summary.json used by the final objective-level acceptance audit.",
    )
    parser.add_argument(
        "--s8p-touchstone-checks",
        type=int,
        default=500,
        help="Maximum successful .s8p files to parse in MARS run-status and dataset gates; default covers the full 500-row run.",
    )
    parser.add_argument("--validation-sample-count", type=int, default=1)
    parser.add_argument("--validation-port-pairs", default="1,4:5,6", help="Differential S8P port pairs for selected-sample HFSS/ADS validation")
    parser.add_argument("--coverage-plan-bins", type=int, default=4)
    parser.add_argument("--coverage-plan-next-count", type=int, default=100)
    parser.add_argument(
        "--coverage-plan-desired-total-count",
        type=int,
        help="Desired total count for physical-feature bin coverage; defaults to --emx-max-count.",
    )
    parser.add_argument("--no-package", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _parse_targets(items: list[str], json_path_raw: str | None) -> tuple[list[dict[str, float]], list[str]]:
    errors = []
    targets: list[dict[str, float]] = []
    if json_path_raw:
        path = Path(json_path_raw).expanduser().resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_targets = data if isinstance(data, list) else [data]
            if not all(isinstance(item, dict) for item in raw_targets):
                errors.append("--inverse-target-json must contain a dict or list of dicts")
            else:
                targets.extend(_coerce_target_dict(item, errors) for item in raw_targets)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"target json error: {type(exc).__name__}: {exc}")
    if items:
        raw: dict[str, Any] = {}
        for item in items:
            if "=" not in item:
                errors.append(f"target must be name=value, got {item!r}")
                continue
            key, value = item.split("=", 1)
            raw[key.strip()] = value.strip()
        targets.append(_coerce_target_dict(raw, errors))
    return [target for target in targets if target], errors


def _coerce_target_dict(raw: dict[str, Any], errors: list[str]) -> dict[str, float]:
    target = {}
    for key, value in raw.items():
        try:
            out = float(value)
        except (TypeError, ValueError):
            errors.append(f"target {key} is not numeric: {value!r}")
            return {}
        target[str(key)] = out
    return target


def _resolved_feature_columns(args: argparse.Namespace) -> list[str]:
    if str(args.physical_feature_columns) == DEFAULT_FEATURE_COLUMNS:
        return ["lp_nh_center", "ls_nh_center", str(args.scalar_q_output_column), "k_center"]
    return _split_columns(args.physical_feature_columns)


def _missing_target_features(targets: list[dict[str, float]], feature_columns: list[str]) -> list[dict[str, Any]]:
    missing = []
    for idx, target in enumerate(targets):
        absent = [column for column in feature_columns if column not in target]
        if absent:
            missing.append({"target_index": idx, "missing": absent})
    return missing


def _feature_contract_checks(feature_columns: list[str]) -> list[dict[str, Any]]:
    zin_columns = [column for column in feature_columns if _is_zin_column(column)]
    tokens = _physical_feature_tokens(feature_columns)
    required = {
        "lp": bool(tokens["lp"]),
        "ls": bool(tokens["ls"]),
        "q": bool(tokens["q"]),
        "k": bool(tokens["k"]),
    }
    return [
        _check("inverse_inputs_do_not_use_zin", not zin_columns, f"zin_columns={zin_columns}"),
        _check(
            "inverse_inputs_include_lp_ls_q_k",
            all(required.values()),
            f"required={required}, feature_columns={feature_columns}",
        ),
    ]


def _input_feature_contract(feature_columns: list[str]) -> dict[str, Any]:
    tokens = _physical_feature_tokens(feature_columns)
    return {
        "zin_columns": [column for column in feature_columns if _is_zin_column(column)],
        "lp_columns": tokens["lp"],
        "ls_columns": tokens["ls"],
        "q_columns": tokens["q"],
        "k_columns": tokens["k"],
        "feature_columns": list(feature_columns),
    }


def _physical_feature_tokens(columns: list[str]) -> dict[str, list[str]]:
    return {
        "lp": [column for column in columns if _normalized_feature_name(column).startswith("lp")],
        "ls": [column for column in columns if _normalized_feature_name(column).startswith("ls")],
        "q": [column for column in columns if _normalized_feature_name(column).startswith(("q", "qp", "qs"))],
        "k": [column for column in columns if _normalized_feature_name(column).startswith(("k", "kw"))],
    }


def _is_zin_column(column: str) -> bool:
    name = _normalized_feature_name(column)
    return "zin" in name or name.startswith(("re_z", "im_z", "z_real", "z_imag"))


def _normalized_feature_name(column: str) -> str:
    text = str(column).strip().lower()
    for prefix in ("input__", "target__", "pred_", "candidate__"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def _config_status(path: Path, expected_bridge_width_um: float | None, bridge_width_tolerance_um: float) -> tuple[str, str]:
    if not path.is_file():
        return "FAIL", f"missing config: {path}"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    if _has_unresolved_placeholder(raw_text):
        return "FAIL", "config still contains TODO/REPLACE/TBD/PLACEHOLDER markers"
    try:
        cfg = load_run_config(path)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"{type(exc).__name__}: {exc}"
    proc_path = resolve_local_path(cfg.emx.emx_process_file, extra_roots=(REPO_ROOT, bundled_proc_dir(), path.parent))
    if not proc_path.is_file():
        return "FAIL", f"emx_process_file does not resolve to an existing .proc file: {cfg.emx.emx_process_file} -> {proc_path}"
    s8p = cfg.emx.power_line_8port
    if not s8p.enabled:
        return "FAIL", "emx.power_line_8port.enabled is false"
    if cfg.emx.differential_port_pairs is None:
        return "FAIL", "emx.differential_port_pairs is missing"
    if s8p.bridge_width_um is None or float(s8p.bridge_width_um) <= 0.0:
        return "FAIL", "bridge_width_um must remain explicit and positive as a legacy config fallback"
    if abs(float(s8p.bridge_width_um) - 0.01) <= bridge_width_tolerance_um:
        return "FAIL", "bridge_width_um must not use the superseded literal 10nm/0.01um interpretation"
    if expected_bridge_width_um is not None and abs(float(s8p.bridge_width_um) - expected_bridge_width_um) > bridge_width_tolerance_um:
        return "FAIL", f"bridge_width_um must be {expected_bridge_width_um:.12g} um for this explicit fixed-width launch"
    for role_name, inductor in (("primary", cfg.bounds.primary), ("secondary", cfg.bounds.secondary)):
        if not bool(inductor.center_tap):
            return "FAIL", f"{role_name}_center_tap must be true for power_line_8port"
        vdd = inductor.vdd_bar
        if vdd is None or not bool(vdd.enabled) or vdd.bar_layer is None:
            return "FAIL", f"{role_name}_vdd_bar.enabled and bar_layer are required for power_line_8port"
        expected_layer = int(cfg.emx.ap_layer if role_name == "primary" else cfg.emx.m9_layer)
        if int(vdd.bar_layer) != expected_layer:
            return "FAIL", f"{role_name}_vdd_bar.bar_layer must match its coil layer ({expected_layer})"
    bad_labels = [label for label in s8p.port_map if _looks_like_placeholder(label)]
    if bad_labels:
        return "FAIL", f"port_map contains placeholder labels: {bad_labels}"
    return "PASS", "config loads; layout audits enforce per-sample shared line_width_um for bridge and vertical power lines"


def _port_map_approval_status(path_raw: str | None, expected_port_pairs: str) -> tuple[str, str, dict[str, Any]]:
    if not path_raw:
        return (
            "FAIL",
            "--port-map-approval-summary is required before the 500-sample S8P EMX launch can be marked ready",
            {"path": "", "status": "MISSING"},
        )
    path = Path(path_raw).expanduser().resolve()
    record: dict[str, Any] = {"path": str(path)}
    if not path.is_file():
        record["status"] = "MISSING"
        return "FAIL", f"missing port-map approval summary: {path}", record
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        record.update({"status": "INVALID_JSON", "error": f"{type(exc).__name__}: {exc}"})
        return "FAIL", record["error"], record

    role_records = data.get("role_records") if isinstance(data, dict) else None
    role_ports = [str(item.get("port", "")) for item in role_records or [] if isinstance(item, dict)]
    touchstone_port_order = [str(item) for item in data.get("touchstone_port_order", [])] if isinstance(data, dict) else []
    if not touchstone_port_order:
        touchstone_port_order = role_ports
    expected_ports = [f"P{idx:03d}" for idx in range(1, 9)]
    pair_text = _normalize_pair_text(str(data.get("port_pairs", "")))
    expected_pair_text = _normalize_pair_text(expected_port_pairs)
    checks = {
        "overall_status_is_pass": data.get("overall_status") == "PASS",
        "approval_status_is_approved": data.get("approval_status") == "APPROVED",
        "decision_allows_mars_emx_run": data.get("decision") == "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN",
        "role_ports_are_distinct_p001_to_p008": sorted(role_ports) == expected_ports,
        "touchstone_port_order_is_p001_to_p008": touchstone_port_order == expected_ports,
        "port_pairs_match_validation_pairs": pair_text == expected_pair_text,
    }
    record.update(
        {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "overall_status": data.get("overall_status"),
            "approval_status": data.get("approval_status"),
            "decision": data.get("decision"),
            "port_pairs": data.get("port_pairs", ""),
            "expected_port_pairs": expected_port_pairs,
            "role_ports": role_ports,
            "touchstone_port_order": touchstone_port_order,
            "checks": checks,
        }
    )
    if all(checks.values()):
        return "PASS", f"approved port map summary: {path}", record
    return "FAIL", json.dumps(record, ensure_ascii=False, sort_keys=True), record


def _geometry_contract_approval_status(
    path_raw: str | None,
    expected_port_pairs: str,
    expected_bridge_width_um: float | None,
    expected_ground_frame_width_um: float,
) -> tuple[str, str, dict[str, Any]]:
    if not path_raw:
        return (
            "FAIL",
            "--geometry-contract-approval-summary is required before the 500-sample S8P EMX launch can be marked ready",
            {"path": "", "status": "MISSING"},
        )
    path = Path(path_raw).expanduser().resolve()
    record: dict[str, Any] = {"path": str(path)}
    if not path.is_file():
        record["status"] = "MISSING"
        return "FAIL", f"missing geometry-contract approval summary: {path}", record
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        record.update({"status": "INVALID_JSON", "error": f"{type(exc).__name__}: {exc}"})
        return "FAIL", record["error"], record

    contract = data.get("approved_geometry_contract") if isinstance(data, dict) else {}
    pair_text = _normalize_pair_text(_geometry_pair_text(contract.get("differential_pair_label_map") if isinstance(contract, dict) else []))
    expected_pair_text = _normalize_pair_text(expected_port_pairs)
    contract_bridge_width_um = contract.get("bridge_width_um") if isinstance(contract, dict) else None
    if expected_bridge_width_um is None:
        bridge_width_check = _positive_float(contract_bridge_width_um)
    else:
        bridge_width_check = _float_eq(contract_bridge_width_um, expected_bridge_width_um)
    checks = {
        "overall_status_is_pass": data.get("overall_status") == "PASS",
        "approval_status_is_approved": data.get("approval_status") == "APPROVED",
        "decision_allows_mars_emx_run": data.get("decision") == "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN",
        "bridge_width_contract_valid": bridge_width_check,
        "superseded_10nm_recorded": _float_eq(contract.get("superseded_literal_10nm_bridge_width_um") if isinstance(contract, dict) else None, 0.01),
        "vertical_reference_is_max_height": (contract.get("vertical_length_reference_dimension") if isinstance(contract, dict) else None)
        == "max(primary_outer_height_um, secondary_outer_height_um)",
        "ground_frame_width_matches_contract": _float_eq(
            contract.get("ground_frame_width_um") if isinstance(contract, dict) else None,
            expected_ground_frame_width_um,
        ),
        "port_pairs_match_validation_pairs": bool(expected_pair_text) and pair_text == expected_pair_text,
    }
    record.update(
        {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "overall_status": data.get("overall_status"),
            "approval_status": data.get("approval_status"),
            "decision": data.get("decision"),
            "contract": contract,
            "expected_port_pairs": expected_port_pairs,
            "checks": checks,
        }
    )
    if all(checks.values()):
        return "PASS", f"approved geometry contract summary: {path}", record
    return "FAIL", json.dumps(record, ensure_ascii=False, sort_keys=True), record


def _geometry_pair_text(pair_map: Any) -> str:
    if not isinstance(pair_map, list):
        return ""
    parts = []
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


def _positive_float(value: Any) -> bool:
    try:
        return float(value) > 0.0
    except (TypeError, ValueError):
        return False


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_fixed_bridge_args(flag: str, args: argparse.Namespace) -> list[str]:
    expected_bridge_width_um = _optional_float(args.expected_bridge_width_um)
    if expected_bridge_width_um is None:
        return []
    return [flag, f"{expected_bridge_width_um:.12g}"]


def _normalize_pair_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def _build_commands(
    repo_root: Path,
    dataset_dir: Path | None,
    config: Path,
    out_dir: Path,
    run_dir: Path,
    quality_dir: Path,
    target_json: Path,
    args: argparse.Namespace,
    *,
    has_inverse_targets: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    python = "${PYTHON}"
    contract_audit = out_dir / "power_line_8port_contract_audit"
    inverse_quality = out_dir / "inverse_quality_gates"
    inverse_table = inverse_quality / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_table.csv"
    inverse_candidates = inverse_quality / "physical_feature_inverse_geometry_prediction" / "physical_feature_inverse_geometry_candidates.csv"
    bootstrap_queue_dir = out_dir / "geometry_bootstrap_candidate_queue"
    bootstrap_candidates = bootstrap_queue_dir / "s8p_geometry_bootstrap_candidate_queue.csv"
    candidates = bootstrap_candidates if args.bootstrap_geometry_candidate_queue else inverse_candidates
    layout_smoke_dir = out_dir / "layout_smoke_create_only"
    layout_smoke_audit = out_dir / "layout_smoke_8port_audit"
    selected_samples = quality_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_samples.csv"
    feature_coverage_plan = quality_dir / "physical_feature_balanced_acquisition_plan"
    post_emx_inverse_table_dir = quality_dir / "physical_feature_inverse_training_table"
    post_emx_inverse_manifest = post_emx_inverse_table_dir / "physical_feature_inverse_training_manifest.json"
    post_emx_inverse_table = post_emx_inverse_table_dir / "physical_feature_inverse_training_table.csv"
    post_emx_inverse_report = post_emx_inverse_table_dir / "physical_feature_inverse_training_report.md"
    post_emx_inverse_quality = quality_dir / "physical_feature_inverse_model_quality"
    post_emx_saved_inverse_model = quality_dir / "physical_feature_saved_inverse_model"
    post_emx_saved_inverse_target_layout_smoke = quality_dir / "physical_feature_saved_inverse_target_layout_smoke"
    selected_layout_audit = quality_dir / "selected_power_line_8port_layout_audit"
    selected_port_pair_audit = quality_dir / "selected_s8p_port_pair_physical_candidate_audit"
    selected_hfss_handoff = quality_dir / "selected_s8p_hfss_handoff"
    selected_hfss_aedt_scripts = quality_dir / "selected_s8p_hfss_aedt_scripts"
    selected_hfss_payload_views = quality_dir / "selected_s8p_hfss_payload_views"
    selected_hfss_postrun_validation = quality_dir / "selected_s8p_hfss_postrun_validation"
    final_report_evidence_packet = quality_dir / "s8p_final_report_evidence_packet"
    run_status_dir = run_dir / "next_gen_s8p_mars_run_status"
    objective_acceptance_dir = quality_dir / "next_gen_s8p_objective_acceptance"
    feature_columns = ",".join(_resolved_feature_columns(args))
    commands = [
        {
            "name": "Audit approved 8-port vertical power-line topology contract",
            "command": [
                str(python),
                _repo_script("audit_power_line_8port_contract.py"),
                "--config",
                str(config),
                "--out-dir",
                str(contract_audit),
                *_optional_fixed_bridge_args("--expected-bridge-width-um", args),
                "--bridge-width-tolerance-um",
                f"{float(args.bridge_width_tolerance_um):.12g}",
                "--expected-ground-frame-width-um",
                f"{float(args.expected_ground_frame_width_um):.12g}",
                "--ground-frame-width-tolerance-um",
                f"{float(args.ground_frame_width_tolerance_um):.12g}",
                "--expected-ground-frame-policy",
                str(args.expected_ground_frame_policy),
                "--expected-differential-port-pairs",
                str(args.validation_port_pairs),
                "--expected-frequency-start-ghz",
                "5.0",
                "--expected-frequency-stop-ghz",
                "60.0",
                "--expected-frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
            ],
            "expected_outputs": [
                str(contract_audit / "power_line_8port_contract_audit_summary.json"),
                str(contract_audit / "power_line_8port_contract_audit_report.md"),
            ],
        },
    ]
    if args.bootstrap_geometry_candidate_queue:
        commands.append(
            {
                "name": "Build bootstrap geometry candidate queue for first S8P EMX labels",
                "command": [
                    str(python),
                    _repo_script("build_s8p_geometry_bootstrap_candidate_queue.py"),
                    "--config",
                    str(config),
                    "--out-dir",
                    str(bootstrap_queue_dir),
                    "--count",
                    str(int(args.inverse_candidate_count)),
                    "--expected-count",
                    str(int(args.expected_emx_count)),
                    "--sampler",
                    str(args.bootstrap_sampler),
                    "--seed",
                    str(int(args.bootstrap_seed)),
                    "--oversample-factor",
                    f"{float(args.bootstrap_oversample_factor):.12g}",
                    "--max-sampling-rounds",
                    str(int(args.bootstrap_max_sampling_rounds)),
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "60.0",
                    "--expected-frequency-step-ghz",
                    "1.0",
                    "--expected-frequency-points",
                    "56",
                ],
                "expected_outputs": [
                    str(bootstrap_candidates),
                    str(bootstrap_queue_dir / "s8p_geometry_bootstrap_candidate_queue_summary.json"),
                ],
            }
        )
    else:
        commands.append(
            {
                "name": "Build inverse training table and candidate geometries",
                "command": [
                    str(python),
                    _repo_script("run_dataset_quality_gates.py"),
                    str(dataset_dir),
                    "--out-dir",
                    str(inverse_quality),
                    "--skip-validation",
                    "--skip-visualization",
                    "--skip-geometry-audit",
                    "--skip-touchstone-audit",
                    "--physical-feature-columns",
                    feature_columns,
                    "--build-physical-feature-inverse-training-table",
                    "--predict-geometry-from-physical-features",
                    "--inverse-candidate-count",
                    str(int(args.inverse_candidate_count)),
                    "--inverse-k-neighbors",
                    str(int(args.inverse_k_neighbors)),
                    "--inverse-target-json",
                    str(target_json),
                    "--inverse-geometry-config",
                    str(config),
                ]
                + _scalar_q_command_args(args),
                "expected_outputs": [str(inverse_table), str(candidates)],
            }
        )
    commands.extend(
        [
        {
            "name": "Run one-candidate create-only layout smoke before EMX queue",
            "command": [
                str(python),
                _repo_script("run_candidate_queue_dataset.py"),
                "--candidate-csv",
                str(candidates),
                "--out-dir",
                str(layout_smoke_dir),
                "--config",
                str(config),
                "--max-count",
                "1",
                "--batch-size",
                "1",
                "--create-only",
                "--force-wideband-5-60-1p0",
                "--expected-port-mode",
                "single_ended_shield_grounded",
                "--expected-pin-purpose",
                "51",
                "--expected-touchstone-extension",
                ".s8p",
                "--expected-ports",
                "8",
                "--expected-frequency-start-ghz",
                "5.0",
                "--expected-frequency-stop-ghz",
                "60.0",
                "--expected-frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
                "--max-touchstone-checks",
                str(int(args.s8p_touchstone_checks)),
                "--fail-on-error",
            ],
            "expected_outputs": [
                str(layout_smoke_dir / "dataset_rows.csv"),
                str(layout_smoke_dir / "dataset_manifest.json"),
                str(layout_smoke_dir / "candidate_queue_dataset_summary.json"),
            ],
        },
        {
            "name": "Audit one-candidate smoke 8-port power-line layout evidence",
            "command": [
                str(python),
                _repo_script("audit_selected_power_line_8port_layout_samples.py"),
                "--samples-csv",
                str(layout_smoke_dir / "dataset_rows.csv"),
                "--dataset-dir",
                str(layout_smoke_dir),
                "--out-dir",
                str(layout_smoke_audit),
                "--expected-port-names",
                "P001,P002,P003,P004,P005,P006,P007,P008",
                "--expected-pin-purpose",
                "51",
                *_optional_fixed_bridge_args("--expected-power-line-bridge-width-um", args),
                "--power-line-tolerance-um",
                f"{float(args.bridge_width_tolerance_um):.12g}",
                "--internal-angle-deg",
                "135.0",
                "--terminal-angle-deg",
                "90.0",
                "--angle-tolerance-deg",
                "0.001",
                "--max-samples",
                "1",
            ],
            "expected_outputs": [
                str(layout_smoke_audit / "selected_power_line_8port_layout_audit_summary.json"),
                str(layout_smoke_audit / "selected_power_line_8port_layout_audit_report.md"),
            ],
        },
        {
            "name": "Run candidate geometries through 8-worker EMX dataset generation",
            "command": [
                str(python),
                _repo_script("run_candidate_queue_dataset_parallel.py"),
                "--candidate-csv",
                str(candidates),
                "--out-dir",
                str(run_dir),
                "--config",
                str(config),
                "--jobs",
                str(int(args.jobs)),
                "--max-count",
                str(int(args.emx_max_count)),
                "--expected-count",
                str(int(args.expected_emx_count)),
                "--batch-size",
                str(int(args.batch_size)),
                "--resume-completed",
                "--force-wideband-5-60-1p0",
                "--expected-jobs",
                str(int(args.expected_jobs)),
                "--expected-port-mode",
                "single_ended_shield_grounded",
                "--expected-pin-purpose",
                "51",
                "--expected-touchstone-extension",
                ".s8p",
                "--expected-ports",
                "8",
                "--expected-frequency-start-ghz",
                "5.0",
                "--expected-frequency-stop-ghz",
                "60.0",
                "--expected-frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
                "--max-touchstone-checks",
                str(int(args.s8p_touchstone_checks)),
                "--fail-on-error",
            ],
            "expected_outputs": [str(run_dir / "dataset_rows.csv"), str(run_dir / "parallel_candidate_queue_dataset_summary.json")],
        },
        {
            "name": "Run S8P physical-feature quality gates and select validation sample",
            "command": [
                str(python),
                _repo_script("run_dataset_quality_gates.py"),
                str(run_dir),
                "--out-dir",
                str(quality_dir),
                "--skip-validation",
                "--skip-visualization",
                "--skip-geometry-audit",
                "--skip-touchstone-audit",
                "--extract-response-features",
                "--audit-s8p-physical-feature-dataset",
                "--s8p-expected-count",
                str(int(args.emx_max_count)),
                "--s8p-expected-ok-count",
                str(int(args.emx_max_count)),
                "--s8p-max-touchstone-checks",
                str(int(args.s8p_touchstone_checks)),
                "--touchstone-all",
                "--touchstone-expected-ports",
                "8",
                "--touchstone-port-pairs",
                str(args.validation_port_pairs),
                "--touchstone-target-frequency-ghz",
                "15.0",
                "--expected-frequency-start-ghz",
                "5.0",
                "--expected-frequency-stop-ghz",
                "60.0",
                "--expected-frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
                "--touchstone-ground-unused-ports",
            ]
            + _scalar_q_command_args(args)
            + [
                "--select-physical-feature-validation-samples",
                "--physical-feature-validation-sample-count",
                str(int(args.validation_sample_count)),
            ],
            "expected_outputs": [
                str(quality_dir / "dataset_quality_gates_summary.json"),
                str(quality_dir / "scalar_q_feature_dataset" / "dataset_rows.csv"),
                str(quality_dir / "scalar_q_feature_dataset" / "scalar_q_feature_summary.json"),
                str(selected_samples),
            ],
        },
        {
            "name": "Plan Lp/Ls/Q/K response-space coverage and sparse-bin acquisition targets",
            "command": [
                str(python),
                _repo_script("plan_physical_feature_balanced_acquisition.py"),
                str(quality_dir / "scalar_q_feature_dataset"),
                "--out-dir",
                str(feature_coverage_plan),
                "--feature-columns",
                feature_columns,
                "--bins",
                str(int(args.coverage_plan_bins)),
                "--desired-total-count",
                str(int(args.coverage_plan_desired_total_count or args.emx_max_count)),
                "--next-count",
                str(int(args.coverage_plan_next_count)),
            ],
            "expected_outputs": [
                str(feature_coverage_plan / "physical_feature_acquisition_plan_summary.json"),
                str(feature_coverage_plan / "physical_feature_acquisition_plan_report.md"),
                str(feature_coverage_plan / "physical_feature_acquisition_bins.csv"),
                str(feature_coverage_plan / "physical_feature_acquisition_targets.csv"),
                str(feature_coverage_plan / "physical_feature_marginal_histograms.png"),
                str(feature_coverage_plan / "physical_feature_pairwise_scatter.png"),
                str(feature_coverage_plan / "physical_feature_bin_coverage_heatmap.png"),
            ],
        },
        {
            "name": "Build post-EMX Lp/Ls/Q/K inverse training table from generated S8P labels",
            "command": [
                str(python),
                _repo_script("build_physical_feature_inverse_training_table.py"),
                str(quality_dir / "scalar_q_feature_dataset"),
                "--out-dir",
                str(post_emx_inverse_table_dir),
                "--feature-columns",
                feature_columns,
                "--config",
                str(config),
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(post_emx_inverse_manifest),
                str(post_emx_inverse_report),
                str(post_emx_inverse_table),
            ],
        },
        {
            "name": "Audit post-EMX Lp/Ls/Q/K inverse-model quality with leave-one-out KNN",
            "command": [
                str(python),
                _repo_script("audit_physical_feature_inverse_model_quality.py"),
                "--training-csv",
                str(post_emx_inverse_table),
                "--out-dir",
                str(post_emx_inverse_quality),
                "--k-neighbors",
                str(int(args.inverse_k_neighbors)),
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(post_emx_inverse_quality / "physical_feature_inverse_model_quality_summary.json"),
                str(post_emx_inverse_quality / "physical_feature_inverse_model_quality_report.md"),
                str(post_emx_inverse_quality / "physical_feature_inverse_model_cv_predictions.csv"),
                str(post_emx_inverse_quality / "physical_feature_inverse_model_geometry_errors.csv"),
            ],
        },
        {
            "name": "Train saved baseline Lp/Ls/Q/K-to-geometry inverse model",
            "command": [
                str(python),
                _repo_script("train_physical_feature_inverse_model.py"),
                "--training-csv",
                str(post_emx_inverse_table),
                "--out-dir",
                str(post_emx_saved_inverse_model),
                "--config",
                str(config),
                "--degree",
                "2",
                "--ridge-alpha",
                "1e-6",
                "--target-json",
                str(target_json),
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(post_emx_saved_inverse_model / "physical_feature_inverse_model.json"),
                str(post_emx_saved_inverse_model / "physical_feature_inverse_model_training_summary.json"),
                str(post_emx_saved_inverse_model / "physical_feature_inverse_model_training_report.md"),
                str(post_emx_saved_inverse_model / "physical_feature_inverse_model_training_cv_predictions.csv"),
                str(post_emx_saved_inverse_model / "physical_feature_inverse_model_training_geometry_errors.csv"),
                str(post_emx_saved_inverse_model / "physical_feature_inverse_model_target_predictions.csv"),
            ],
        },
        {
            "name": "Audit selected validation sample candidate S8P port-pair physics",
            "command": [
                str(python),
                _repo_script("audit_s8p_port_pair_physical_candidates.py"),
                "--samples-csv",
                str(selected_samples),
                "--dataset-dir",
                str(run_dir),
                "--out-dir",
                str(selected_port_pair_audit),
                "--expected-port-pairs",
                str(args.validation_port_pairs),
                "--candidate-port-pairs",
                "1,4:5,6;7,8:1,2;1,2:7,8;3,4:5,6;1,2:3,4;5,6:7,8",
                "--expected-ports",
                "8",
                "--expected-frequency-start-ghz",
                "5.0",
                "--expected-frequency-stop-ghz",
                "60.0",
                "--expected-frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
                "--ground-unused-ports",
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(selected_port_pair_audit / "s8p_port_pair_physical_candidate_audit_summary.json"),
                str(selected_port_pair_audit / "s8p_port_pair_physical_candidate_audit_records.csv"),
            ],
        },
        {
            "name": "Audit selected validation sample 8-port power-line layout evidence",
            "command": [
                str(python),
                _repo_script("audit_selected_power_line_8port_layout_samples.py"),
                "--samples-csv",
                str(selected_samples),
                "--dataset-dir",
                str(run_dir),
                "--out-dir",
                str(selected_layout_audit),
                "--expected-port-names",
                "P001,P002,P003,P004,P005,P006,P007,P008",
                "--expected-pin-purpose",
                "51",
                *_optional_fixed_bridge_args("--expected-power-line-bridge-width-um", args),
                "--power-line-tolerance-um",
                f"{float(args.bridge_width_tolerance_um):.12g}",
                "--internal-angle-deg",
                "135.0",
                "--terminal-angle-deg",
                "90.0",
                "--angle-tolerance-deg",
                "0.001",
            ],
            "expected_outputs": [
                str(selected_layout_audit / "selected_power_line_8port_layout_audit_summary.json"),
                str(selected_layout_audit / "selected_power_line_8port_layout_audit_report.md"),
            ],
        },
        {
            "name": "Build selected sample HFSS rebuild handoff packet",
            "command": [
                str(python),
                _repo_script("build_selected_s8p_hfss_handoff_packet.py"),
                "--samples-csv",
                str(selected_samples),
                "--dataset-dir",
                str(run_dir),
                "--out-dir",
                str(selected_hfss_handoff),
                "--layout-audit-summary",
                str(selected_layout_audit / "selected_power_line_8port_layout_audit_summary.json"),
                "--port-pairs",
                str(args.validation_port_pairs),
                *_optional_fixed_bridge_args("--expected-bridge-width-um", args),
                "--bridge-tolerance-um",
                f"{float(args.bridge_width_tolerance_um):.12g}",
            ],
            "expected_outputs": [
                str(selected_hfss_handoff / "selected_s8p_hfss_handoff_summary.json"),
                str(selected_hfss_handoff / "hfss_rebuild_checklist.md"),
                str(selected_hfss_handoff / "hfss_port_map.csv"),
                str(selected_hfss_handoff / "hfss_bridge_geometry.csv"),
            ],
        },
        {
            "name": "Generate selected sample HFSS AEDT build/solve scripts",
            "command": [
                str(python),
                _repo_script("build_s8p_hfss_aedt_scripts_from_handoff.py"),
                "--handoff-summary",
                str(selected_hfss_handoff / "selected_s8p_hfss_handoff_summary.json"),
                "--out-dir",
                str(selected_hfss_aedt_scripts),
                "--frequency-start-ghz",
                "5.0",
                "--frequency-stop-ghz",
                "60.0",
                "--frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
            ],
            "expected_outputs": [
                str(selected_hfss_aedt_scripts / "hfss_s8p_aedt_script_packet_summary.json"),
                str(selected_hfss_aedt_scripts / "run_generated_hfss_s8p_scripts.commands.ps1"),
                str(selected_hfss_aedt_scripts / "samples" / "<rank_eval>" / "hfss_s8p_build_payload.json"),
                str(selected_hfss_aedt_scripts / "samples" / "<rank_eval>" / "build_hfss_s8p_from_payload.py"),
                str(selected_hfss_aedt_scripts / "samples" / "<rank_eval>" / "solve_export_hfss_s8p.py"),
            ],
        },
        {
            "name": "Render selected sample HFSS payload geometry views",
            "command": [
                str(python),
                _repo_script("render_hfss_model_views_from_payload.py"),
                "--aedt-packet-summary",
                str(selected_hfss_aedt_scripts / "hfss_s8p_aedt_script_packet_summary.json"),
                "--out-dir",
                str(selected_hfss_payload_views),
            ],
            "expected_outputs": [
                str(selected_hfss_payload_views / "hfss_payload_geometry_render_batch_summary.json"),
                str(selected_hfss_payload_views / "<rank_eval>" / "hfss_payload_geometry_render_summary.json"),
                str(selected_hfss_payload_views / "<rank_eval>" / "hfss_payload_geometry_top_annotated.png"),
                str(selected_hfss_payload_views / "<rank_eval>" / "hfss_payload_geometry_quality_checks.png"),
            ],
        },
        {
            "name": "Prepare post-HFSS EMX/HFSS S8P physical validation gate",
            "command": [
                str(python),
                _repo_script("run_s8p_hfss_postrun_validation_from_aedt_packet.py"),
                "--aedt-packet-summary",
                str(selected_hfss_aedt_scripts / "hfss_s8p_aedt_script_packet_summary.json"),
                "--out-dir",
                str(selected_hfss_postrun_validation),
                "--max-percent-error",
                "10.0",
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(selected_hfss_postrun_validation / "s8p_hfss_postrun_validation_summary.json"),
                str(selected_hfss_postrun_validation / "s8p_hfss_postrun_validation_report.md"),
                str(selected_hfss_postrun_validation / "s8p_hfss_postrun_validation_results.csv"),
            ],
        },
        {
            "name": "Build final S8P report evidence packet",
            "command": [
                str(python),
                _repo_script("build_s8p_final_report_evidence_packet.py"),
                "--quality-dir",
                str(quality_dir),
                "--out-dir",
                str(final_report_evidence_packet),
                "--max-percent-error",
                "10.0",
                "--target-ghz",
                "15.0",
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(final_report_evidence_packet / "s8p_final_report_evidence_packet_summary.json"),
                str(final_report_evidence_packet / "S8P_FINAL_REPORT_EVIDENCE_PACKET_CN.md"),
                str(final_report_evidence_packet / "s8p_final_report_artifact_manifest.csv"),
                str(final_report_evidence_packet / "s8p_final_report_evidence_checks.csv"),
            ],
        },
        {
            "name": "Summarize current next-gen S8P MARS run status",
            "command": [
                str(python),
                _repo_script("summarize_next_gen_s8p_mars_run.py"),
                "--run-dir",
                str(run_dir),
                "--quality-dir",
                str(quality_dir),
                "--out-dir",
                str(run_status_dir),
                "--expected-count",
                str(int(args.emx_max_count)),
                "--expected-jobs",
                str(int(args.expected_jobs)),
                "--expected-ports",
                "8",
                "--expected-frequency-start-ghz",
                "5.0",
                "--expected-frequency-stop-ghz",
                "60.0",
                "--expected-frequency-step-ghz",
                "1.0",
                "--expected-frequency-points",
                "56",
                "--max-touchstone-checks",
                str(int(args.s8p_touchstone_checks)),
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(run_status_dir / "next_gen_s8p_mars_run_status_summary.json"),
                str(run_status_dir / "next_gen_s8p_mars_run_status_report.md"),
                str(run_status_dir / "next_gen_s8p_mars_run_status_evidence.csv"),
            ],
        },
        {
            "name": "Build objective-level next-gen S8P acceptance audit",
            "command": [
                str(python),
                _repo_script("build_next_gen_s8p_objective_acceptance_audit.py"),
                "--launch-summary",
                str(out_dir / "physical_feature_s8p_launch_packet_summary.json"),
                "--combined-approval-summary",
                str(args.combined_approval_summary),
                "--run-status-summary",
                str(run_status_dir / "next_gen_s8p_mars_run_status_summary.json"),
                "--out-dir",
                str(objective_acceptance_dir),
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(objective_acceptance_dir / "next_gen_s8p_objective_acceptance_summary.json"),
                str(objective_acceptance_dir / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md"),
                str(objective_acceptance_dir / "next_gen_s8p_objective_acceptance_evidence.csv"),
            ],
        },
        {
            "name": "Refresh final S8P report evidence packet with objective acceptance audit",
            "command": [
                str(python),
                _repo_script("build_s8p_final_report_evidence_packet.py"),
                "--quality-dir",
                str(quality_dir),
                "--objective-acceptance-summary",
                str(objective_acceptance_dir / "next_gen_s8p_objective_acceptance_summary.json"),
                "--out-dir",
                str(final_report_evidence_packet),
                "--max-percent-error",
                "10.0",
                "--target-ghz",
                "15.0",
                "--no-fail-exit",
            ],
            "expected_outputs": [
                str(final_report_evidence_packet / "s8p_final_report_evidence_packet_summary.json"),
                str(final_report_evidence_packet / "S8P_FINAL_REPORT_EVIDENCE_PACKET_CN.md"),
                str(final_report_evidence_packet / "s8p_final_report_artifact_manifest.csv"),
                str(final_report_evidence_packet / "s8p_final_report_evidence_checks.csv"),
            ],
        },
    ]
    )
    if has_inverse_targets:
        _insert_after_command(
            commands,
            "Train saved baseline Lp/Ls/Q/K-to-geometry inverse model",
            {
                "name": "Run saved-model target geometry create-only layout smoke",
                "command": [
                    str(python),
                    _repo_script("run_candidate_queue_dataset.py"),
                    "--candidate-csv",
                    str(post_emx_saved_inverse_model / "physical_feature_inverse_model_target_predictions.csv"),
                    "--out-dir",
                    str(post_emx_saved_inverse_target_layout_smoke),
                    "--config",
                    str(config),
                    "--max-count",
                    "1",
                    "--batch-size",
                    "1",
                    "--create-only",
                    "--force-wideband-5-60-1p0",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "60.0",
                    "--expected-frequency-step-ghz",
                    "1.0",
                    "--expected-frequency-points",
                    "56",
                    "--fail-on-error",
                ],
                "expected_outputs": [
                    str(post_emx_saved_inverse_target_layout_smoke / "dataset_rows.csv"),
                    str(post_emx_saved_inverse_target_layout_smoke / "dataset_manifest.json"),
                    str(post_emx_saved_inverse_target_layout_smoke / "candidate_queue_dataset_summary.json"),
                ],
            },
        )
    if not args.no_package:
        commands.append(
            {
                "name": "Package lightweight MARS run artifacts",
                "command": [
                    str(python),
                    _repo_script("package_mars_dataset_run.py"),
                    str(run_dir),
                    "--include-quality-figures",
                ],
                "expected_outputs": ["tar.gz package path printed by package_mars_dataset_run.py"],
            }
        )
    inverse_artifacts = {
        "post_emx_inverse_training_manifest": str(post_emx_inverse_manifest),
        "post_emx_inverse_training_report": str(post_emx_inverse_report),
        "post_emx_inverse_training_table": str(post_emx_inverse_table),
        "post_emx_inverse_model_quality_summary": str(post_emx_inverse_quality / "physical_feature_inverse_model_quality_summary.json"),
        "post_emx_inverse_model_quality_report": str(post_emx_inverse_quality / "physical_feature_inverse_model_quality_report.md"),
        "post_emx_saved_inverse_model_json": str(post_emx_saved_inverse_model / "physical_feature_inverse_model.json"),
        "post_emx_saved_inverse_model_summary": str(post_emx_saved_inverse_model / "physical_feature_inverse_model_training_summary.json"),
        "post_emx_saved_inverse_model_report": str(post_emx_saved_inverse_model / "physical_feature_inverse_model_training_report.md"),
        "post_emx_saved_inverse_target_predictions": str(post_emx_saved_inverse_model / "physical_feature_inverse_model_target_predictions.csv"),
        "post_emx_saved_inverse_target_layout_smoke_summary": str(post_emx_saved_inverse_target_layout_smoke / "candidate_queue_dataset_summary.json"),
        "post_emx_inverse_training_source": str(quality_dir / "scalar_q_feature_dataset"),
        "pre_emx_inverse_quality_dir": "" if args.bootstrap_geometry_candidate_queue else str(inverse_quality),
        "pre_emx_inverse_candidate_csv": "" if args.bootstrap_geometry_candidate_queue else str(inverse_candidates),
        "objective_acceptance_summary": str(objective_acceptance_dir / "next_gen_s8p_objective_acceptance_summary.json"),
        "objective_acceptance_report": str(objective_acceptance_dir / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md"),
        "objective_acceptance_evidence_csv": str(objective_acceptance_dir / "next_gen_s8p_objective_acceptance_evidence.csv"),
    }
    return commands, inverse_artifacts


def _insert_after_command(commands: list[dict[str, Any]], after_name: str, command: dict[str, Any]) -> None:
    for idx, item in enumerate(commands):
        if item.get("name") == after_name:
            commands.insert(idx + 1, command)
            return
    commands.append(command)


def _scalar_q_command_args(args: argparse.Namespace) -> list[str]:
    if not args.scalar_q_definition:
        return []
    return [
        "--derive-scalar-q-feature",
        "--scalar-q-definition",
        str(args.scalar_q_definition),
        "--scalar-q-output-column",
        str(args.scalar_q_output_column),
    ]


def _render_commands(commands: list[dict[str, Any]], repo_root: Path) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated physical-feature inverse-design -> S8P EMX runbook.",
        "# Review the summary JSON before running. Do not run while overall_status is NOT_READY.",
        f"DEFAULT_REPO_ROOT={shlex.quote(str(repo_root))}",
        'REPO_ROOT="${REPO_ROOT:-${DEFAULT_REPO_ROOT}}"',
        'if [[ -z "${PYTHON:-}" ]]; then',
        '  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then',
        '    PYTHON="${REPO_ROOT}/.venv/bin/python"',
        "  elif command -v python3 >/dev/null 2>&1; then",
        '    PYTHON="$(command -v python3)"',
        "  elif command -v python >/dev/null 2>&1; then",
        '    PYTHON="$(command -v python)"',
        "  else",
        "    echo 'No usable Python found for S8P launch packet.' >&2",
        "    exit 11",
        "  fi",
        "fi",
        "export PYTHON",
        "",
    ]
    for index, item in enumerate(commands, start=1):
        lines.append(f"echo '[{index}/{len(commands)}] {item['name']}'")
        lines.append(_shell_join(item["command"]))
        lines.append("")
    return "\n".join(lines)


def _shell_join(command: list[str]) -> str:
    if len(command) <= 8:
        return " ".join(_shell_quote_arg(item) for item in command)
    lines = [_shell_quote_arg(command[0]) + " \\"]
    for idx, item in enumerate(command[1:], start=1):
        suffix = " \\" if idx < len(command) - 1 else ""
        lines.append(f"  {_shell_quote_arg(item)}{suffix}")
    return "\n".join(lines)


def _shell_quote_arg(item: str) -> str:
    if item == "${PYTHON}":
        return '"${PYTHON}"'
    if item.startswith("${REPO_ROOT}/"):
        return f'"{item}"'
    return shlex.quote(item)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-Feature S8P Launch Packet",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Dataset dir: `{summary['dataset_dir']}`",
        f"- Config: `{summary['config']}`",
        f"- Commands: `{summary['commands_path']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Commands", ""])
    for index, item in enumerate(summary["commands"], start=1):
        lines.append(f"{index}. {item['name']}")
        for output in item.get("expected_outputs", []):
            lines.append(f"   - expected: `{output}`")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _split_columns(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _has_unresolved_placeholder(raw_text: str) -> bool:
    upper = raw_text.upper()
    return any(token in upper for token in ("TODO", "TBD", "PLACEHOLDER", "REPLACE"))


def _looks_like_placeholder(value: str) -> bool:
    stripped = str(value).strip()
    if not stripped:
        return True
    upper = stripped.upper()
    return any(token in upper for token in ("TODO", "TBD", "PLACEHOLDER", "REPLACE", "CONFIRM")) or stripped.startswith("/REPLACE/")


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
