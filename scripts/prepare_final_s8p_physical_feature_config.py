#!/usr/bin/env python3
"""Prepare a finalized S8P physical-feature config from the MARS template.

This script is conservative: it can write a draft config, but the JSON summary
is the authority. Only `overall_status=PASS` / `READY_FOR_S8P_LAUNCH_PACKET`
means the config has the explicit paths, port map, differential pairs, power-line
layers, and scalar-Q definition needed before launching the 500-sample MARS run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

import yaml


REQUIRED_PATH_FIELDS = (
    "emx_binary",
    "emx_process_file",
    "cadence_install_root",
    "cadence_pdk_cds_lib",
    "cadence_tech_lib",
    "cadence_layer_map",
)
DRY_RUN_PATH_MARKERS = (
    "/tmp/mars_dryrun",
    "mars_dryrun",
    "/tmp/dryrun",
    "dry-run",
)
SCALAR_Q_DEFINITIONS = ("min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary")


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
    template = Path(args.template).expanduser().resolve()
    out_config = Path(args.out_config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else out_config.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = _read_yaml(template)
    discovery = _read_json(Path(args.path_discovery_summary).expanduser().resolve()) if args.path_discovery_summary else {}

    paths = _resolve_path_fields(args, discovery)
    _apply_final_config(raw, args, paths)
    checks = _build_checks(raw, args, paths, template)
    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    decision = "READY_FOR_S8P_LAUNCH_PACKET" if overall_status == "PASS" else "DO_NOT_RUN_GENERATED_S8P_CONFIG_YET"

    out_config.parent.mkdir(parents=True, exist_ok=True)
    out_config.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=False), encoding="utf-8")

    launch_defaults_path = out_dir / "final_s8p_physical_feature_launch_defaults.json"
    launch_defaults = _launch_defaults(args, out_config)
    launch_defaults_path.write_text(json.dumps(launch_defaults, indent=2, ensure_ascii=False), encoding="utf-8")
    commands_path = out_dir / "final_s8p_config_next_commands.sh"
    commands_path.write_text(_render_commands(out_config, launch_defaults_path), encoding="utf-8")
    commands_path.chmod(0o755)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "template": str(template),
        "out_config": str(out_config),
        "path_discovery_summary": "" if not args.path_discovery_summary else str(Path(args.path_discovery_summary).expanduser().resolve()),
        "launch_defaults": str(launch_defaults_path),
        "next_commands": str(commands_path),
        "check_paths": bool(args.check_paths),
        "paths": paths,
        "port_map": _split_csv(args.port_map),
        "role_labels": _split_assignments(args.role_labels),
        "differential_port_pairs": args.differential_port_pairs,
        "scalar_q_definition": args.scalar_q_definition,
        "power_line_layers": {
            "primary": args.primary_power_line_layer,
            "secondary": args.secondary_power_line_layer,
        },
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This script does not run MARS, EMX, Cadence, HFSS, or ADS.",
            "A generated config with overall_status != PASS is a draft only and must not be launched.",
            "PASS still requires running audit_power_line_8port_contract.py and the full launch-packet checks on MARS.",
        ],
    }
    summary_path = out_dir / "final_s8p_physical_feature_config_summary.json"
    report_path = out_dir / "final_s8p_physical_feature_config_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"config={out_config}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"launch_defaults={launch_defaults_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", default="configs/mars_s8p_physical_feature_500_template.yaml")
    parser.add_argument("--out-config", required=True)
    parser.add_argument("--out-dir")
    parser.add_argument("--path-discovery-summary", help="mars_emx_cadence_path_discovery_summary.json")
    parser.add_argument("--emx-binary")
    parser.add_argument("--emx-process-file")
    parser.add_argument("--cadence-install-root")
    parser.add_argument("--cadence-pdk-cds-lib")
    parser.add_argument("--cadence-tech-lib")
    parser.add_argument("--cadence-layer-map")
    parser.add_argument("--license-file")
    parser.add_argument("--cdslmd-license-file")
    parser.add_argument("--execution-mode", default="local")
    parser.add_argument("--port-map", help="Comma-separated physical labels for P001..P008, e.g. P001,P002,...,P008")
    parser.add_argument(
        "--role-labels",
        help="Required comma-separated role=port labels, e.g. primary_top=P001,left_power_top=P002,...; do not rely on semantic port_map order.",
    )
    parser.add_argument("--differential-port-pairs", help="Explicit S8P differential pairs, e.g. 1,4:5,6")
    parser.add_argument("--scalar-q-definition", choices=SCALAR_Q_DEFINITIONS)
    parser.add_argument("--primary-power-line-layer", type=int)
    parser.add_argument("--secondary-power-line-layer", type=int)
    parser.add_argument("--primary-power-line-width-um", type=float, default=10.0)
    parser.add_argument("--secondary-power-line-width-um", type=float, default=10.0)
    parser.add_argument("--power-line-offset-um", type=float, default=12.0)
    parser.add_argument("--expected-sample-count", type=int, default=500)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--check-paths", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Missing template: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Template top-level YAML must be a mapping: {path}")
    return data


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_path_fields(args: argparse.Namespace, discovery: dict[str, Any]) -> dict[str, str]:
    selected = discovery.get("selected_candidates") or {}
    tech_lib_candidates = discovery.get("tech_lib_candidates") or []
    paths = {
        "emx_binary": _candidate_path(selected, "emx_binary"),
        "emx_process_file": _candidate_path(selected, "emx_process_file"),
        "cadence_install_root": _candidate_path(selected, "cadence_install_root"),
        "cadence_pdk_cds_lib": _candidate_path(selected, "cadence_pdk_cds_lib"),
        "cadence_tech_lib": str(tech_lib_candidates[0]) if tech_lib_candidates else "",
        "cadence_layer_map": _candidate_path(selected, "cadence_layer_map"),
    }
    cli = {
        "emx_binary": args.emx_binary,
        "emx_process_file": args.emx_process_file,
        "cadence_install_root": args.cadence_install_root,
        "cadence_pdk_cds_lib": args.cadence_pdk_cds_lib,
        "cadence_tech_lib": args.cadence_tech_lib,
        "cadence_layer_map": args.cadence_layer_map,
    }
    for field, value in cli.items():
        if value:
            paths[field] = str(Path(value).expanduser()) if field != "cadence_tech_lib" else str(value)
    return paths


def _candidate_path(selected: dict[str, Any], field: str) -> str:
    value = selected.get(field) or {}
    if isinstance(value, dict):
        return str(value.get("path") or "")
    return ""


def _apply_final_config(raw: dict[str, Any], args: argparse.Namespace, paths: dict[str, str]) -> None:
    target = _ensure_dict(raw, "target")
    target.update(
        {
            "topology_mode": "1t1t",
            "frequency_start_hz": 5.0e9,
            "frequency_stop_hz": 60.0e9,
            "frequency_step_hz": 0.5e9,
            "band_points": 111,
        }
    )
    emx = _ensure_dict(raw, "emx")
    for field, value in paths.items():
        if value:
            emx[field] = value
    if args.license_file is not None:
        emx["license_file"] = args.license_file
    if args.cdslmd_license_file is not None:
        emx["cdslmd_license_file"] = args.cdslmd_license_file
    emx["execution_mode"] = str(args.execution_mode)
    emx["port_mode"] = "single_ended_shield_grounded"
    emx["cadence_pin_purpose"] = 51
    if args.differential_port_pairs:
        emx["differential_port_pairs"] = str(args.differential_port_pairs)
    power = _ensure_nested_dict(emx, "power_line_8port")
    power.update(
        {
            "enabled": True,
            "bridge_width_um": 10.0,
            "vertical_length_diameter_ratio": 1.5,
            "bridge_y_policy": "center",
            "bridge_motion_axis": "x_only",
            "port_ground_reference": "shield",
        }
    )
    port_map = _split_csv(args.port_map)
    if port_map:
        power["port_map"] = port_map
    role_labels = _split_assignments(args.role_labels)
    if role_labels:
        power["role_labels"] = role_labels
    primary_power_line_layer = _resolved_power_line_layer(raw, args, "primary")
    secondary_power_line_layer = _resolved_power_line_layer(raw, args, "secondary")
    topology = _ensure_dict(raw, "topology")
    _apply_vdd_bar(topology, "primary", primary_power_line_layer, float(args.primary_power_line_width_um), float(args.power_line_offset_um))
    _apply_vdd_bar(topology, "secondary", secondary_power_line_layer, float(args.secondary_power_line_width_um), float(args.power_line_offset_um))
    inverse = _ensure_dict(raw, "physical_feature_inverse")
    inverse["input_features"] = ["lp_nh_center", "ls_nh_center", "q_center", "k_center"]
    if args.scalar_q_definition:
        inverse["scalar_q_definition"] = str(args.scalar_q_definition)
    inverse["expected_sample_count"] = int(args.expected_sample_count)
    inverse["jobs"] = int(args.expected_jobs)


def _ensure_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        value = {}
        raw[key] = value
    return value


def _ensure_nested_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        value = {}
        raw[key] = value
    return value


def _apply_vdd_bar(topology: dict[str, Any], role: str, layer: int | None, width_um: float, offset_um: float) -> None:
    role_raw = _ensure_nested_dict(topology, role)
    role_raw["turns"] = 1
    role_raw["center_tap"] = True
    vdd = _ensure_nested_dict(role_raw, "vdd_bar")
    vdd["enabled"] = True
    if layer is not None:
        vdd["bar_layer"] = int(layer)
    vdd["width_um"] = float(width_um)
    vdd["offset_um"] = float(offset_um)


def _resolved_power_line_layer(raw: dict[str, Any], args: argparse.Namespace, role: str) -> int:
    emx = raw.get("emx") if isinstance(raw.get("emx"), dict) else {}
    if role == "primary":
        cli_layer = args.primary_power_line_layer
        default_layer = int(emx.get("ap_layer", 74))
    elif role == "secondary":
        cli_layer = args.secondary_power_line_layer
        default_layer = int(emx.get("m9_layer", 39))
    else:  # pragma: no cover - defensive only
        raise ValueError(f"Unsupported role: {role}")
    return int(default_layer if cli_layer is None else cli_layer)


def _build_checks(raw: dict[str, Any], args: argparse.Namespace, paths: dict[str, str], template: Path) -> list[Check]:
    checks = [
        Check("PASS" if template.is_file() else "FAIL", "template exists", str(template), "Provide the S8P template."),
    ]
    for field in REQUIRED_PATH_FIELDS:
        value = paths.get(field) or ""
        is_final = bool(value) and not _looks_placeholder(value) and not _looks_dry_run_path(field, value)
        checks.append(
            Check(
                "PASS" if is_final else "FAIL",
                f"{field} finalized",
                value or "missing",
                f"Provide --{field.replace('_', '-')} or a discovery summary containing {field}.",
            )
        )
    if args.check_paths:
        for field in ("emx_binary", "emx_process_file", "cadence_install_root", "cadence_pdk_cds_lib", "cadence_layer_map"):
            value = paths.get(field) or ""
            checks.append(
                Check(
                    "PASS" if value and Path(value).expanduser().exists() else "FAIL",
                    f"{field} exists on this filesystem",
                    value or "missing",
                    "Run this check on MARS or provide paths visible from this machine.",
                )
            )
        checks.extend(_build_cadence_tool_checks(paths.get("cadence_install_root") or ""))
    checks.append(
        Check(
            "PASS" if _valid_proc_path(paths.get("emx_process_file") or "") else "FAIL",
            "emx_process_file has valid EMX proc identity",
            paths.get("emx_process_file") or "missing",
            "Use the foundry/EMX .proc path, not unrelated CAE simulator files such as samcef.proc.",
        )
    )
    checks.append(
        Check(
            "PASS" if _valid_layer_map_path(paths.get("cadence_layer_map") or "") else "FAIL",
            "cadence_layer_map has valid stream layer-map identity",
            paths.get("cadence_layer_map") or "missing",
            "Use the PDK stream/GDS layer-map file; do not use Cadence HTML/PDF documentation pages.",
        )
    )
    port_map = _split_csv(args.port_map)
    role_labels = _split_assignments(args.role_labels)
    checks.append(
        Check(
            "PASS" if len(port_map) == 8 and len(set(port_map)) == 8 and not any(_looks_placeholder(item) for item in port_map) else "FAIL",
            "P001-P008 port map finalized",
            str(port_map or _get_power_line(raw).get("port_map")),
            "Provide --port-map with eight approved physical labels in P001..P008 order.",
        )
    )
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
    role_labels_ok = (
        set(role_labels) == expected_roles
        and len(set(role_labels.values())) == 8
        and set(role_labels.values()) == set(port_map)
    )
    checks.append(
        Check(
            "PASS" if role_labels_ok else "FAIL",
            "P001-P008 role labels finalized",
            str(role_labels),
            "Provide --role-labels with every 8-port physical role mapped to one P001-P008 label.",
        )
    )
    checks.append(
        Check(
            "PASS" if _valid_differential_pairs(args.differential_port_pairs) else "FAIL",
            "differential port pairs finalized",
            str(args.differential_port_pairs or ""),
            "Provide --differential-port-pairs after confirming the physical P001-P008 order.",
        )
    )
    checks.append(
        Check(
            "PASS" if args.scalar_q_definition in SCALAR_Q_DEFINITIONS else "FAIL",
            "scalar Q definition finalized",
            str(args.scalar_q_definition or ""),
            "Provide --scalar-q-definition min/mean/geometric_mean/harmonic_mean/primary/secondary.",
        )
    )
    resolved_layers = {
        "primary": _resolved_power_line_layer(raw, args, "primary"),
        "secondary": _resolved_power_line_layer(raw, args, "secondary"),
    }
    expected_layers = {
        "primary": int((_ensure_dict(raw, "emx")).get("ap_layer", 74)),
        "secondary": int((_ensure_dict(raw, "emx")).get("m9_layer", 39)),
    }
    for role, layer in resolved_layers.items():
        checks.append(
            Check(
                "PASS" if int(layer) == int(expected_layers[role]) else "FAIL",
                f"{role} vertical power-line layer matches its coil layer",
                f"power_line_layer={layer}, coil_layer={expected_layers[role]}",
                f"Use the {role} coil layer for its vertical power-line.",
            )
        )
    power = _get_power_line(raw)
    power_checks = [
        (power.get("enabled") is True, "power_line_8port enabled", str(power.get("enabled"))),
        (float(power.get("bridge_width_um", -1.0)) == 10.0, "bridge width matches vertical power-line width", str(power.get("bridge_width_um"))),
        (float(power.get("vertical_length_diameter_ratio", -1.0)) == 1.5, "vertical length ratio is 1.5", str(power.get("vertical_length_diameter_ratio"))),
        (power.get("bridge_y_policy") == "center", "bridge y policy is center", str(power.get("bridge_y_policy"))),
        (power.get("bridge_motion_axis") == "x_only", "bridge motion axis is x_only", str(power.get("bridge_motion_axis"))),
        (power.get("port_ground_reference") == "shield", "port ground reference is shield", str(power.get("port_ground_reference"))),
    ]
    for passed, name, detail in power_checks:
        checks.append(Check("PASS" if passed else "FAIL", name, detail, "Do not modify the agreed 8-port geometry contract."))
    return checks


def _build_cadence_tool_checks(cadence_install_root: str) -> list[Check]:
    root = Path(cadence_install_root).expanduser()
    checks: list[Check] = []
    for tool in ("dbAccess", "strmin", "strmout"):
        tool_path = root / "bin" / tool
        present_and_executable = tool_path.exists() and os.access(tool_path, os.X_OK)
        checks.append(
            Check(
                "PASS" if present_and_executable else "FAIL",
                f"cadence {tool} executable exists",
                str(tool_path),
                "Use the real Cadence IC install root, e.g. /cae/apps/data/cadence-2025/installs/IC231, not /cae/apps.",
            )
        )
    return checks


def _get_power_line(raw: dict[str, Any]) -> dict[str, Any]:
    emx = raw.get("emx") if isinstance(raw.get("emx"), dict) else {}
    power = emx.get("power_line_8port") if isinstance(emx.get("power_line_8port"), dict) else {}
    return power


def _valid_differential_pairs(text: str | None) -> bool:
    if not text:
        return False
    try:
        first, second = str(text).split(":", 1)
        ports = [int(item.strip()) for part in (first, second) for item in part.split(",", 1)]
    except Exception:
        return False
    return len(ports) == 4 and len(set(ports)) == 4 and all(1 <= port <= 8 for port in ports)


def _launch_defaults(args: argparse.Namespace, out_config: Path) -> dict[str, Any]:
    return {
        "config": str(out_config),
        "physical_feature_columns": "lp_nh_center,ls_nh_center,q_center,k_center",
        "scalar_q_definition": args.scalar_q_definition,
        "scalar_q_output_column": "q_center",
        "inverse_candidate_count": int(args.expected_sample_count),
        "jobs": int(args.expected_jobs),
        "emx_max_count": int(args.expected_sample_count),
        "validation_port_pairs": args.differential_port_pairs,
        "required_follow_up": [
            "Fill dataset-dir and target physical features before building the launch packet.",
            "Run audit_power_line_8port_contract.py on MARS before launching EMX.",
            "Run audit_next_gen_s8p_goal_readiness.py after every external stage to avoid claiming incomplete work.",
        ],
    }


def _render_commands(out_config: Path, launch_defaults_path: Path) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Review final_s8p_physical_feature_config_summary.json first.",
            "REPO_ROOT=${REPO_ROOT:-$(pwd)}",
            f"CONFIG={_sh(out_config)}",
            f"LAUNCH_DEFAULTS={_sh(launch_defaults_path)}",
            "",
            '"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/audit_power_line_8port_contract.py" \\',
            '  --config "${CONFIG}" \\',
            '  --out-dir "${CONFIG}.power_line_8port_contract_audit"',
            "",
            "# Then build the launch packet with your real dataset dir and inverse target JSON.",
            "# See ${LAUNCH_DEFAULTS} for the scalar-Q and worker defaults to pass through.",
            "",
        ]
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Final S8P Physical-Feature Config Preparation",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Output config: `{summary['out_config']}`",
        f"- Launch defaults: `{summary['launch_defaults']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail | Next action |",
        "| --- | --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['status'])} | {_cell(check['name'])} | {_cell(check['detail'])} | {_cell(check['next_action'])} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


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


def _looks_placeholder(value: Any) -> bool:
    text = "" if value is None else str(value).strip()
    return not text or bool(re.search(r"\b(TODO|REPLACE|TBD|PLACEHOLDER)\b", text, flags=re.IGNORECASE)) or text.startswith("/REPLACE/")


def _looks_dry_run_path(field: str, value: Any) -> bool:
    text = "" if value is None else str(value).strip()
    lowered = text.lower()
    if field == "emx_binary" and text == "/usr/bin/true":
        return True
    return any(marker in lowered for marker in DRY_RUN_PATH_MARKERS)


def _valid_proc_path(value: str) -> bool:
    lowered = str(value or "").lower()
    if not lowered.endswith(".proc"):
        return False
    return not any(marker in lowered for marker in ("/patran", "/samcef", "pat3samcef", "samcef.proc"))


def _valid_layer_map_path(value: str) -> bool:
    lowered = str(value or "").lower()
    name = Path(lowered).name
    if name.endswith((".html", ".htm", ".pdf", ".txt", ".md", ".json")):
        return False
    if name.endswith(".layermap"):
        return True
    if name in {"layers.layermap", "layer.map", "streamout.map", "strmout.map", "gds.map"}:
        return True
    return name.endswith(".map") and any(marker in name for marker in ("layer", "stream", "strm", "gds"))


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _sh(path: Path) -> str:
    return "'" + str(path).replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
