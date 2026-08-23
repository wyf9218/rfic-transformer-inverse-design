#!/usr/bin/env python3
"""Summarize V66 physical inputs, geometry labels, ports, and S8P contract."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import TransformerOptimizationAdapter, load_run_config  # noqa: E402
from rfic_transformer_inverse_design.core.topology import TransformerSpec  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "hfss_v66_calibration_plan_summary.json"
DEFAULT_CONFIG = REPO_ROOT / "configs" / "mars_s8p_physical_feature_500_template.yaml"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "v66_geometry_input_contract_summary_current"
DEFAULT_PHYSICAL_INPUTS = ["lp_nh_center", "ls_nh_center", "q_center", "k_center"]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = _read_json(plan_path)
    geometry_contract = _geometry_contract(config_path, args)
    variants = [_variant_record(item) for item in plan.get("variants") or [] if isinstance(item, dict)]
    port_rows = [row for variant in variants for row in variant.pop("_port_rows")]

    checks = _checks(plan_path, plan, geometry_contract, variants, port_rows)
    overall_status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "USE_AS_V66_GEOMETRY_AND_INPUT_CONTRACT_EVIDENCE"
        if overall_status == "PASS"
        else "FIX_V66_GEOMETRY_OR_INPUT_CONTRACT_EVIDENCE",
        "plan_summary": str(plan_path),
        "config": "" if config_path is None else str(config_path),
        "physical_model_inputs": list(DEFAULT_PHYSICAL_INPUTS if not args.physical_inputs else _split(args.physical_inputs)),
        "physical_input_note": "The inverse model inputs are physical features Lp, Ls, scalar Q, and K/Kw; geometry columns are labels/outputs.",
        "geometry_contract": geometry_contract,
        "variant_count": len(variants),
        "variants": variants,
        "port_rows": port_rows,
        "checks": checks,
        "limitations": [
            "This summarizes payload geometry and contracts; it does not run EMX, HFSS, or ADS.",
            "HFSS/EMX agreement still requires an exported HFSS .s8p and the <=10% Lp/Ls/Q/Kw comparison gate.",
            "The geometry field order is the inverse-model output/label order, not the physical-feature input order.",
        ],
    }

    summary_path = out_dir / "v66_geometry_input_contract_summary.json"
    variant_csv = out_dir / "v66_geometry_variant_summary.csv"
    port_csv = out_dir / "v66_port_map.csv"
    report_path = out_dir / "V66_GEOMETRY_INPUT_CONTRACT_SUMMARY.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_variant_csv(variant_csv, variants)
    _write_port_csv(port_csv, port_rows)
    report_path.write_text(_render_report(summary, variant_csv, port_csv), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"variant_csv={variant_csv}")
    print(f"port_csv={port_csv}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", default=str(DEFAULT_PLAN))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--physical-inputs", default=",".join(DEFAULT_PHYSICAL_INPUTS))
    parser.add_argument("--expected-variant-count", type=int, default=8)
    parser.add_argument("--expected-port-count", type=int, default=8)
    parser.add_argument("--expected-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-points", type=int, default=56)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _geometry_contract(config_path: Path | None, args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "config_adapter_field_order",
        "config": "" if config_path is None else str(config_path),
        "config_exists": bool(config_path and config_path.is_file()),
        "field_order": [],
        "geometry_columns": [],
        "bounds": {},
        "status": "FAIL",
        "error": "",
    }
    if config_path is None or not config_path.is_file():
        result["error"] = "config_missing"
        return result
    try:
        cfg = load_run_config(config_path)
        adapter = TransformerOptimizationAdapter(cfg.bounds)
        field_order = list(adapter.field_order())
        result["field_order"] = field_order
        result["geometry_columns"] = [f"geom__{field}" for field in field_order]
        result["bounds"] = {name: list(values) for name, values in adapter.bounds().items()}
        result["status"] = "PASS" if field_order else "FAIL"
    except Exception as exc:  # noqa: BLE001 - exact config failure is evidence.
        result = _geometry_contract_from_yaml_bounds(config_path, f"{type(exc).__name__}: {exc}")
    return result


def _geometry_contract_from_yaml_bounds(config_path: Path, load_error: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "yaml_bounds_fallback_after_config_load_failure",
        "config": str(config_path),
        "config_exists": config_path.is_file(),
        "config_load_error": load_error,
        "field_order": [],
        "geometry_columns": [],
        "bounds": {},
        "status": "FAIL",
        "error": "",
    }
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - exact YAML failure is evidence.
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    primary_turns = int(((data.get("topology") or {}).get("primary") or {}).get("turns") or 1)
    secondary_turns = int(((data.get("topology") or {}).get("secondary") or {}).get("turns") or 1)
    field_order = list(
        TransformerSpec.flat_field_order_for_topology(
            primary_turns=primary_turns,
            secondary_turns=secondary_turns,
        )
    )
    bounds = _bounds_from_yaml(data, field_order)
    missing = [name for name in field_order if name not in bounds]
    result["field_order"] = field_order
    result["geometry_columns"] = [f"geom__{field}" for field in field_order]
    result["bounds"] = bounds
    if missing:
        result["error"] = f"missing bounds for {missing}"
    else:
        result["status"] = "PASS"
    return result


def _bounds_from_yaml(data: dict[str, Any], field_order: list[str]) -> dict[str, list[float]]:
    raw = data.get("bounds") if isinstance(data.get("bounds"), dict) else {}
    primary = raw.get("primary") if isinstance(raw.get("primary"), dict) else {}
    secondary = raw.get("secondary") if isinstance(raw.get("secondary"), dict) else {}
    candidates = {
        "primary_outer_width_um": primary.get("outer_width_um"),
        "primary_outer_height_um": primary.get("outer_height_um"),
        "secondary_outer_width_um": secondary.get("outer_width_um"),
        "secondary_outer_height_um": secondary.get("outer_height_um"),
        "primary_width_um": primary.get("trace_width_um"),
        "secondary_width_um": secondary.get("trace_width_um"),
        "primary_spacing_um": primary.get("spacing_um"),
        "secondary_spacing_um": secondary.get("spacing_um"),
        "primary_terminal_y_span_um": primary.get("terminal_y_span_um"),
        "secondary_terminal_y_span_um": secondary.get("terminal_y_span_um"),
        "offset_um": raw.get("offset_um"),
        "primary_feed_extension_um": primary.get("feed_extension_um"),
        "secondary_feed_extension_um": secondary.get("feed_extension_um"),
    }
    out: dict[str, list[float]] = {}
    for name in field_order:
        pair = candidates.get(name)
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            try:
                out[name] = [float(pair[0]), float(pair[1])]
            except (TypeError, ValueError):
                pass
    return out


def _variant_record(variant: dict[str, Any]) -> dict[str, Any]:
    payload_path = Path(str(variant.get("payload_json") or "")).expanduser()
    payload = _read_json(payload_path)
    power = payload.get("power_line_8port_geometry") if isinstance(payload.get("power_line_8port_geometry"), dict) else {}
    grid = payload.get("frequency_grid") if isinstance(payload.get("frequency_grid"), dict) else {}
    source_files = payload.get("source_files") if isinstance(payload.get("source_files"), dict) else {}
    ports = payload.get("ports") or []
    labels = power.get("labels") if isinstance(power.get("labels"), dict) else {}
    primary_line = power.get("primary_power_line") if isinstance(power.get("primary_power_line"), dict) else {}
    secondary_line = power.get("secondary_power_line") if isinstance(power.get("secondary_power_line"), dict) else {}
    primary_bridge = power.get("primary_bridge") if isinstance(power.get("primary_bridge"), dict) else {}
    secondary_bridge = power.get("secondary_bridge") if isinstance(power.get("secondary_bridge"), dict) else {}
    primary_clearance = power.get("primary_power_line_clearance") if isinstance(power.get("primary_power_line_clearance"), dict) else {}
    secondary_clearance = power.get("secondary_power_line_clearance") if isinstance(power.get("secondary_power_line_clearance"), dict) else {}
    diff_pairs = payload.get("differential_port_pairs") or []
    port_rows = [_port_row(str(variant.get("name") or ""), payload_path, item) for item in ports if isinstance(item, dict)]

    return {
        "variant": str(variant.get("name") or ""),
        "payload_json": str(payload_path),
        "payload_exists": payload_path.is_file(),
        "sample_id": str(payload.get("sample_id") or ""),
        "emx_s8p": str(source_files.get("emx_s8p") or ""),
        "gds": str(source_files.get("gds") or ""),
        "port_count": len(ports),
        "port_order": [str(item.get("port_name") or "") for item in ports if isinstance(item, dict)],
        "role_labels": labels,
        "differential_port_pairs": diff_pairs,
        "frequency_grid": grid,
        "line_width_um": _number(power.get("line_width_um")),
        "bridge_width_um": _number(power.get("bridge_width_um")),
        "vertical_length_um": _number(power.get("vertical_length_um")),
        "max_outer_height_um": _number(power.get("max_outer_height_um")),
        "vertical_length_diameter_ratio": _number(power.get("vertical_length_diameter_ratio")),
        "primary_power_line_center_x_um": _number(primary_line.get("center_x_um")),
        "primary_power_line_width_um": _number(primary_line.get("width_um")),
        "primary_power_line_height_um": _number(primary_line.get("height_um")),
        "secondary_power_line_center_x_um": _number(secondary_line.get("center_x_um")),
        "secondary_power_line_width_um": _number(secondary_line.get("width_um")),
        "secondary_power_line_height_um": _number(secondary_line.get("height_um")),
        "primary_bridge_width_um": _number(primary_bridge.get("width_um")),
        "primary_bridge_length_um": _number(primary_bridge.get("length_um")),
        "primary_bridge_horizontal": bool(primary_bridge.get("is_horizontal")),
        "primary_bridge_extends_away": bool(primary_bridge.get("extends_away_from_coil_interior")),
        "secondary_bridge_width_um": _number(secondary_bridge.get("width_um")),
        "secondary_bridge_length_um": _number(secondary_bridge.get("length_um")),
        "secondary_bridge_horizontal": bool(secondary_bridge.get("is_horizontal")),
        "secondary_bridge_extends_away": bool(secondary_bridge.get("extends_away_from_coil_interior")),
        "primary_other_coil_clearance_um": _number(primary_clearance.get("other_coil_boundary_clearance_um")),
        "secondary_other_coil_clearance_um": _number(secondary_clearance.get("other_coil_boundary_clearance_um")),
        "required_shield_inner_bbox_um": power.get("required_shield_inner_bbox_um") or {},
        "grounded_conductor_bbox_um": power.get("grounded_conductor_bbox_um") or {},
        "_port_rows": port_rows,
    }


def _port_row(variant: str, payload_path: Path, port: dict[str, Any]) -> dict[str, Any]:
    signal_origin = ((port.get("signal_label") or {}).get("origin_um") or ["", ""])
    ground_origin = ((port.get("ground_label") or {}).get("origin_um") or ["", ""])
    return {
        "variant": variant,
        "payload_json": str(payload_path),
        "port_name": str(port.get("port_name") or ""),
        "role": str(port.get("role") or ""),
        "ground_name": str(port.get("ground_name") or ""),
        "signal_metal": str(port.get("signal_metal") or ""),
        "ground_metal": str(port.get("ground_metal") or ""),
        "signal_x_um": _number_at(signal_origin, 0),
        "signal_y_um": _number_at(signal_origin, 1),
        "ground_x_um": _number_at(ground_origin, 0),
        "ground_y_um": _number_at(ground_origin, 1),
        "signal_z_um": _number(port.get("signal_z_um")),
        "ground_z_um": _number(port.get("ground_z_um")),
        "port_sheet_width_um": _number(port.get("port_sheet_width_um")),
        "port_sheet_axis": str(port.get("port_sheet_axis") or ""),
    }


def _checks(
    plan_path: Path,
    plan: dict[str, Any],
    geometry_contract: dict[str, Any],
    variants: list[dict[str, Any]],
    port_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    expected_count = 8
    checks = [
        _check("plan summary exists", plan_path.is_file(), str(plan_path)),
        _check("plan status PASS", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check(
            "geometry field order resolves",
            geometry_contract.get("status") == "PASS",
            geometry_contract.get("source") or geometry_contract.get("error") or geometry_contract.get("config"),
        ),
        _check("geometry field order present", bool(geometry_contract.get("field_order")), str(geometry_contract.get("field_order"))),
        _check("variant count is 8", len(variants) == expected_count, f"variants={len(variants)}"),
        _check("port rows are present", len(port_rows) >= expected_count, f"port_rows={len(port_rows)}"),
    ]
    for item in variants:
        name = item["variant"]
        grid = item.get("frequency_grid") or {}
        checks.extend(
            [
                _check(f"{name} payload exists", bool(item.get("payload_exists")), str(item.get("payload_json"))),
                _check(f"{name} has 8 ports", int(item.get("port_count") or 0) == 8, str(item.get("port_order"))),
                _check(f"{name} port order P001-P008", item.get("port_order") == [f"P{i:03d}" for i in range(1, 9)], str(item.get("port_order"))),
                _check(f"{name} frequency grid 5-60GHz", float(grid.get("start_ghz") or 0) == 5.0 and float(grid.get("stop_ghz") or 0) == 60.0, str(grid)),
                _check(f"{name} frequency grid 0.5GHz 56 points", float(grid.get("step_ghz") or 0) == 0.5 and int(grid.get("points") or 0) == 111, str(grid)),
                _check(f"{name} shared line/bridge width", _same_number(item.get("line_width_um"), item.get("bridge_width_um")), f"line={item.get('line_width_um')}, bridge={item.get('bridge_width_um')}"),
                _check(f"{name} primary bridge same width", _same_number(item.get("line_width_um"), item.get("primary_bridge_width_um")), f"line={item.get('line_width_um')}, primary_bridge={item.get('primary_bridge_width_um')}"),
                _check(f"{name} secondary bridge same width", _same_number(item.get("line_width_um"), item.get("secondary_bridge_width_um")), f"line={item.get('line_width_um')}, secondary_bridge={item.get('secondary_bridge_width_um')}"),
                _check(f"{name} vertical length ratio 1.5", _same_number(item.get("vertical_length_diameter_ratio"), 1.5), str(item.get("vertical_length_diameter_ratio"))),
                _check(f"{name} bridges are horizontal", bool(item.get("primary_bridge_horizontal")) and bool(item.get("secondary_bridge_horizontal")), "primary/secondary"),
                _check(f"{name} bridges extend away from coil interior", bool(item.get("primary_bridge_extends_away")) and bool(item.get("secondary_bridge_extends_away")), "primary/secondary"),
            ]
        )
    return checks


def _write_variant_csv(path: Path, variants: list[dict[str, Any]]) -> None:
    fields = [
        "variant",
        "sample_id",
        "payload_json",
        "emx_s8p",
        "port_count",
        "line_width_um",
        "bridge_width_um",
        "vertical_length_um",
        "max_outer_height_um",
        "vertical_length_diameter_ratio",
        "primary_power_line_center_x_um",
        "secondary_power_line_center_x_um",
        "primary_bridge_length_um",
        "secondary_bridge_length_um",
        "primary_other_coil_clearance_um",
        "secondary_other_coil_clearance_um",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in variants:
            writer.writerow({field: item.get(field, "") for field in fields})


def _write_port_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "variant",
        "port_name",
        "role",
        "ground_name",
        "signal_metal",
        "ground_metal",
        "signal_x_um",
        "signal_y_um",
        "ground_x_um",
        "ground_y_um",
        "signal_z_um",
        "ground_z_um",
        "port_sheet_width_um",
        "port_sheet_axis",
        "payload_json",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _render_report(summary: dict[str, Any], variant_csv: Path, port_csv: Path) -> str:
    inputs = ", ".join(f"`{item}`" for item in summary["physical_model_inputs"])
    geometry_columns = summary["geometry_contract"].get("geometry_columns") or []
    first = (summary["variants"] or [{}])[0]
    checks = summary["checks"]
    failed = [item for item in checks if item["status"] != "PASS"]
    lines = [
        "# V66 Geometry/Input Contract Summary",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Physical inverse-model inputs: {inputs}",
        f"- Geometry output columns: `{len(geometry_columns)}` columns",
        f"- Variant count: `{summary['variant_count']}`",
        f"- Variant CSV: `{variant_csv}`",
        f"- Port map CSV: `{port_csv}`",
        "",
        "## Important distinction",
        "",
        "- The inverse model input is `Lp/Ls/Q/K`, not Zin and not raw geometry.",
        "- Geometry values are labels/outputs for the inverse model and traceability inputs for HFSS/EMX layout generation.",
        "",
        "## Current V66 geometry sample",
        "",
        f"- Sample ID: `{first.get('sample_id', '')}`",
        f"- Port order: `{', '.join(first.get('port_order') or [])}`",
        f"- Differential pairs: `{first.get('differential_port_pairs', [])}`",
        f"- Frequency grid: `{first.get('frequency_grid', {})}`",
        f"- Shared line width: `{first.get('line_width_um')}` um",
        f"- Bridge width: `{first.get('bridge_width_um')}` um",
        f"- Vertical power-line length: `{first.get('vertical_length_um')}` um",
        f"- Vertical length ratio: `{first.get('vertical_length_diameter_ratio')}`",
        f"- Primary/secondary other-coil clearance: `{first.get('primary_other_coil_clearance_um')}` / `{first.get('secondary_other_coil_clearance_um')}` um",
        "",
        "## Geometry output field order",
        "",
    ]
    lines.extend(f"- `{column}`" for column in geometry_columns)
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {item['status']}: {item['name']} - {item['detail']}" for item in checks)
    if failed:
        lines.extend(["", "## Failing Checks", ""])
        lines.extend(f"- {item['name']}: {item['detail']}" for item in failed)
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _split(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_at(values: Any, index: int) -> float | None:
    try:
        return _number(values[index])
    except (TypeError, IndexError):
        return None


def _same_number(left: Any, right: Any, tol: float = 1.0e-9) -> bool:
    left_num = _number(left)
    right_num = _number(right)
    return left_num is not None and right_num is not None and abs(left_num - right_num) <= tol


if __name__ == "__main__":
    raise SystemExit(main())
