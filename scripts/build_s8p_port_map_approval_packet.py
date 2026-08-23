#!/usr/bin/env python3
"""Build an auditable approval packet for the S8P port map and formulas.

This packet is intentionally separate from EMX/HFSS execution. It records the
candidate P001-P008 physical roles, differential pair polarity, and ADS/Python
Lp/Ls/Q/K equations so the port convention can be reviewed before launching a
large EMX run.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROLE_ORDER = (
    "left_power_top",
    "left_power_bottom",
    "primary_top",
    "primary_bottom",
    "secondary_top",
    "secondary_bottom",
    "right_power_top",
    "right_power_bottom",
)
EXPECTED_PORT_NAMES = tuple(f"P{index:03d}" for index in range(1, 9))
EXPECTED_PAIR_WINDING_ORDER = ("primary", "secondary")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    power_line_path = Path(args.power_line_geometry).expanduser().resolve()
    layout_path = Path(args.layout_json).expanduser().resolve() if args.layout_json else None
    power_line = _read_json(power_line_path) if power_line_path.is_file() else {}
    layout = _read_json(layout_path) if layout_path is not None and layout_path.is_file() else {}
    labels = power_line.get("labels") if isinstance(power_line, dict) else {}
    labels = labels if isinstance(labels, dict) else {}
    port_pairs, pair_errors = _parse_port_pairs(args.port_pairs)
    role_records = _role_records(labels, power_line)
    touchstone_port_order = _layout_ports(layout) or list(EXPECTED_PORT_NAMES)
    pair_records = _pair_records(port_pairs, role_records, touchstone_port_order)
    checks = [
        _check("power_line_geometry_exists", power_line_path.is_file(), str(power_line_path)),
        _check("layout_json_exists_or_not_required", layout_path is None or layout_path.is_file(), "" if layout_path is None else str(layout_path)),
        _check("power_line_8port_enabled", bool(power_line.get("enabled")) if isinstance(power_line, dict) else False, str(power_line.get("enabled") if isinstance(power_line, dict) else "")),
        _check("all_role_labels_present", all(role in labels for role in ROLE_ORDER), json.dumps(labels, sort_keys=True)),
        _check("role_labels_are_distinct_P001_to_P008", sorted(record["port"] for record in role_records) == list(EXPECTED_PORT_NAMES), str([record["port"] for record in role_records])),
        _check("touchstone_port_order_is_P001_to_P008", touchstone_port_order == list(EXPECTED_PORT_NAMES), str(touchstone_port_order)),
        _check("layout_ports_match_P001_to_P008", _layout_ports(layout) in ([], list(EXPECTED_PORT_NAMES)), str(_layout_ports(layout))),
        _check("differential_port_pairs_parse", not pair_errors, "; ".join(pair_errors) or str(args.port_pairs)),
        _check("differential_port_pairs_use_distinct_ports", _pairs_use_distinct_valid_ports(port_pairs), str(port_pairs)),
        _check("differential_pair_roles_traceable", all(record.get("plus_role") and record.get("minus_role") for record in pair_records), json.dumps(pair_records, sort_keys=True)),
        _check(
            "differential_pair_winding_roles_match_primary_secondary_order",
            _pair_winding_order_matches(pair_records, EXPECTED_PAIR_WINDING_ORDER),
            json.dumps(
                [
                    {
                        "pair_index": record.get("pair_index"),
                        "expected": record.get("expected_winding_role"),
                        "actual": record.get("pair_winding_role"),
                        "plus": record.get("plus_port"),
                        "minus": record.get("minus_port"),
                    }
                    for record in pair_records
                ],
                sort_keys=True,
            ),
        ),
    ]
    structurally_valid = all(item["status"] != "FAIL" for item in checks)
    approval_status = "APPROVED" if args.approved else "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL"
    overall_status = "PASS" if structurally_valid else "FAIL"
    decision = (
        "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN"
        if structurally_valid and args.approved
        else "AWAITING_USER_ADVISOR_PORT_MAP_APPROVAL"
        if structurally_valid
        else "DO_NOT_USE_PORT_MAP_UNTIL_CHECKS_PASS"
    )
    artifacts = {
        "summary": str(out_dir / "s8p_port_map_approval_summary.json"),
        "report": str(out_dir / "S8P_PORT_MAP_APPROVAL_REPORT_CN.md"),
        "port_map_csv": str(out_dir / "s8p_port_map_roles.csv"),
        "differential_port_pairs_csv": str(out_dir / "s8p_differential_port_pairs.csv"),
        "formula_trace": str(out_dir / "s8p_ads_python_formula_trace.md"),
    }
    summary = {
        "schema": "rfic_transformer_s8p_port_map_approval.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "approval_status": approval_status,
        "decision": decision,
        "power_line_geometry": str(power_line_path),
        "layout_json": "" if layout_path is None else str(layout_path),
        "preview_image": str(Path(args.preview_image).expanduser().resolve()) if args.preview_image else "",
        "port_debug_image": str(Path(args.port_debug_image).expanduser().resolve()) if args.port_debug_image else "",
        "port_pairs": args.port_pairs,
        "touchstone_port_order": touchstone_port_order,
        "role_records": role_records,
        "pair_records": pair_records,
        "checks": checks,
        "artifacts": artifacts,
        "limitations": [
            "This approval packet records topology intent and equations only; it does not run EMX, HFSS, ADS, or Cadence.",
            "Do not use this candidate to launch the 500-sample EMX run until approval_status is APPROVED.",
            "Final acceptance still requires EMX/HFSS .s8p curves and <=5% Lp/Ls/Q/K/Kw comparison.",
        ],
    }
    _write_csv(Path(artifacts["port_map_csv"]), role_records)
    _write_csv(Path(artifacts["differential_port_pairs_csv"]), pair_records)
    Path(artifacts["formula_trace"]).write_text(_render_formula_trace(summary), encoding="utf-8")
    Path(artifacts["report"]).write_text(_render_report(summary), encoding="utf-8")
    Path(artifacts["summary"]).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"approval_status={approval_status}")
    print(f"decision={decision}")
    print(f"summary={artifacts['summary']}")
    print(f"report={artifacts['report']}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--power-line-geometry", required=True)
    parser.add_argument("--layout-json")
    parser.add_argument("--preview-image")
    parser.add_argument("--port-debug-image")
    parser.add_argument("--port-pairs", default="1,4:5,6")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--approved", action="store_true", help="Only set after user/advisor approval of the port map")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _role_records(labels: dict[str, Any], power_line: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    port_to_ground = _port_ground_lookup(power_line)
    port_to_winding = _port_winding_lookup(labels, power_line)
    for index, role in enumerate(ROLE_ORDER, start=1):
        port = str(labels.get(role, ""))
        records.append(
            {
                "order": index,
                "role": role,
                "port": port,
                "ground": port_to_ground.get(port, f"{port}_G" if port else ""),
                "physical_side": "left" if role.startswith("left_") else "right" if role.startswith("right_") else "coil",
                "terminal_position": "top" if role.endswith("_top") else "bottom" if role.endswith("_bottom") else "legacy_coil",
                "winding_role": port_to_winding.get(port, ""),
            }
        )
    return records


def _port_ground_lookup(power_line: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for section_name in ("primary_power_line", "secondary_power_line", "physical_left_power_line", "physical_right_power_line"):
        section = power_line.get(section_name) if isinstance(power_line, dict) else None
        if not isinstance(section, dict):
            continue
        for port_key, ground_key in (("top_port_label", "top_ground_label"), ("bottom_port_label", "bottom_ground_label")):
            port = section.get(port_key)
            ground = section.get(ground_key)
            if port and ground:
                lookup[str(port)] = str(ground)
    return lookup


def _port_winding_lookup(labels: dict[str, Any], power_line: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    role_to_winding = {
        "primary_top": "primary",
        "primary_bottom": "primary",
        "secondary_top": "secondary",
        "secondary_bottom": "secondary",
    }
    for role, winding in role_to_winding.items():
        port = labels.get(role)
        if port:
            lookup[str(port)] = winding
    for section_name, winding in (("primary_power_line", "primary"), ("secondary_power_line", "secondary")):
        section = power_line.get(section_name) if isinstance(power_line, dict) else None
        if not isinstance(section, dict):
            continue
        for port_key in ("top_port_label", "bottom_port_label"):
            port = section.get(port_key)
            if port:
                lookup[str(port)] = winding
    return lookup


def _parse_port_pairs(text: str) -> tuple[list[tuple[int, int]], list[str]]:
    pairs: list[tuple[int, int]] = []
    errors: list[str] = []
    parts = [item.strip() for item in str(text).split(":") if item.strip()]
    if len(parts) != 2:
        return [], [f"expected two differential pairs separated by ':', got {text!r}"]
    for part in parts:
        items = [item.strip() for item in part.split(",") if item.strip()]
        if len(items) != 2:
            errors.append(f"pair {part!r} must contain two ports")
            continue
        try:
            pairs.append((int(items[0]), int(items[1])))
        except ValueError:
            errors.append(f"pair {part!r} contains a non-integer port")
    return pairs, errors


def _pairs_use_distinct_valid_ports(pairs: list[tuple[int, int]]) -> bool:
    flat = [port for pair in pairs for port in pair]
    return len(pairs) == 2 and len(flat) == 4 and len(set(flat)) == 4 and all(1 <= port <= 8 for port in flat)


def _pair_records(
    pairs: list[tuple[int, int]],
    role_records: list[dict[str, Any]],
    touchstone_port_order: list[str],
) -> list[dict[str, Any]]:
    by_port = {str(record.get("port")): record for record in role_records}
    by_order = {
        index: by_port.get(str(port), {"port": str(port), "role": "", "winding_role": ""})
        for index, port in enumerate(touchstone_port_order, start=1)
    }
    records: list[dict[str, Any]] = []
    for idx, (plus, minus) in enumerate(pairs, start=1):
        plus_record = by_order.get(plus, {})
        minus_record = by_order.get(minus, {})
        plus_winding = str(plus_record.get("winding_role", ""))
        minus_winding = str(minus_record.get("winding_role", ""))
        pair_winding = plus_winding if plus_winding and plus_winding == minus_winding else ""
        expected_winding = EXPECTED_PAIR_WINDING_ORDER[idx - 1] if idx <= len(EXPECTED_PAIR_WINDING_ORDER) else ""
        records.append(
            {
                "pair_index": idx,
                "pair_role": f"{expected_winding}_response_pair" if expected_winding else f"response_pair_{idx}",
                "expected_winding_role": expected_winding,
                "pair_winding_role": pair_winding,
                "plus_port_index": plus,
                "minus_port_index": minus,
                "plus_port": plus_record.get("port", ""),
                "minus_port": minus_record.get("port", ""),
                "plus_role": plus_record.get("role", ""),
                "minus_role": minus_record.get("role", ""),
                "plus_winding_role": plus_winding,
                "minus_winding_role": minus_winding,
                "winding_role_matches_expected": bool(pair_winding and pair_winding == expected_winding),
                "syntax": f"{plus},{minus}",
            }
        )
    return records


def _pair_winding_order_matches(pair_records: list[dict[str, Any]], expected_order: tuple[str, ...]) -> bool:
    if len(pair_records) != len(expected_order):
        return False
    for record, expected in zip(pair_records, expected_order):
        if str(record.get("pair_winding_role")) != str(expected):
            return False
        if record.get("winding_role_matches_expected") is not True:
            return False
    return True


def _layout_ports(layout: dict[str, Any]) -> list[str]:
    ports = layout.get("ports") if isinstance(layout, dict) else None
    if not isinstance(ports, list):
        return []
    return [str(port.get("name", "")) for port in ports if isinstance(port, dict)]


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": detail}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _render_formula_trace(summary: dict[str, Any]) -> str:
    pairs = summary["pair_records"]
    pair_lines = [
        f"- Pair {item['pair_index']} ({item['pair_role']}): +{item['plus_port']} ({item['plus_role']}) / -{item['minus_port']} ({item['minus_role']})"
        for item in pairs
    ]
    return "\n".join(
        [
            "# S8P ADS/Python Formula Trace",
            "",
            f"- Port-pair syntax: `{summary['port_pairs']}`",
            f"- Touchstone port order: `{','.join(summary.get('touchstone_port_order', []))}`",
            "- Differential convention:",
            *pair_lines,
            "",
            "## Differential Transform",
            "",
            "Read the single-ended `.s8p` file as `Zse(f)` using the Touchstone reference impedance.",
            "Build two differential terminals from the recorded pair polarity:",
            "",
            "```text",
            "d1 = V(plus_1) - V(minus_1), with current entering plus_1 and leaving minus_1",
            "d2 = V(plus_2) - V(minus_2), with current entering plus_2 and leaving minus_2",
            "Zdiff(f) = differential two-port impedance derived from Zse(f) and the two pair columns",
            "```",
            "",
            "## ADS/Python Metric Equations",
            "",
            "Use the same `Zdiff` ordering in ADS Data Display or Python post-processing.",
            "",
            "```text",
            "omega = 2*pi*freq",
            "Zp = Zdiff[1,1]",
            "Zs = Zdiff[2,2]",
            "Zm = Zdiff[2,1]",
            "Lp = imag(Zp) / omega",
            "Ls = imag(Zs) / omega",
            "M  = imag(Zm) / omega",
            "Qp = imag(Zp) / real(Zp)",
            "Qs = imag(Zs) / real(Zs)",
            "Q  = min(Qp, Qs)",
            "K  = M / sqrt(abs(Lp * Ls))",
            "```",
            "",
            "For display-only positive coupling plots, use `abs(K)` if needed, but keep the signed K convention fixed for pass/fail comparisons.",
            "",
        ]
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Port Map Approval Report",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Approval status: **{summary['approval_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Port pairs: `{summary['port_pairs']}`",
        f"- Touchstone port order: `{','.join(summary.get('touchstone_port_order', []))}`",
        f"- Power-line geometry: `{summary['power_line_geometry']}`",
    ]
    if summary.get("preview_image"):
        lines.extend(["", "## Layout Preview", "", f"![layout preview]({summary['preview_image']})"])
    if summary.get("port_debug_image"):
        lines.extend(["", "## Port Debug", "", f"![port debug]({summary['port_debug_image']})"])
    lines.extend(["", "## P001-P008 Role Map", "", "| Order | Role | Port | Ground | Winding | Side | Position |", "| --- | --- | --- | --- | --- | --- | --- |"])
    for record in summary["role_records"]:
        lines.append(
            f"| {record['order']} | {record['role']} | {record['port']} | {record['ground']} | {record.get('winding_role', '')} | {record['physical_side']} | {record['terminal_position']} |"
        )
    lines.extend(["", "## Differential Pairs", "", "| Pair | Expected winding | Actual winding | Plus | Minus | Plus role | Minus role |", "| --- | --- | --- | --- | --- | --- | --- |"])
    for record in summary["pair_records"]:
        lines.append(
            f"| {record['pair_index']} | {record.get('expected_winding_role', '')} | {record.get('pair_winding_role', '')} | {record['plus_port']} | {record['minus_port']} | {record['plus_role']} | {record['minus_role']} |"
        )
    lines.extend(["", "## Checks", "", "| Status | Check | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Required Approval", ""])
    lines.append("Set `--approved` only after the user/advisor confirms this P001-P008 convention and the differential pair syntax. The first pair is treated as `Lp/primary`; the second pair is treated as `Ls/secondary`.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
