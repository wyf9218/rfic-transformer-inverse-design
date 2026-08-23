#!/usr/bin/env python3
"""Build a combined approval/readiness packet for the S8P MARS run.

This packet combines the port-map approval summary, the geometry-contract
approval summary, and the strict-path MARS execution packet status. It is a
review artifact only: it never launches EMX, HFSS, ADS, or Cadence.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PORT_APPROVED_DECISION = "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN"
GEOMETRY_APPROVED_DECISION = "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN"
EXECUTION_READY_DECISION = "MARS_S8P_EXECUTION_RUNBOOK_READY"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    port_path = Path(args.port_map_approval_summary).expanduser().resolve()
    geometry_path = Path(args.geometry_contract_approval_summary).expanduser().resolve()
    execution_path = Path(args.execution_packet_summary).expanduser().resolve() if args.execution_packet_summary else None
    port = _read_json_if_present(port_path)
    geometry = _read_json_if_present(geometry_path)
    execution = _read_json_if_present(execution_path)

    checks = _build_checks(port_path, port, geometry_path, geometry, execution_path, execution)
    port_approved = _is_port_approved(port)
    geometry_approved = _is_geometry_approved(geometry)
    execution_ready = _is_execution_ready(execution)
    all_approved_for_real_emx = port_approved and geometry_approved and execution_ready
    review_ready = _is_review_ready(port, geometry)
    status = "PASS" if review_ready else "FAIL"
    decision = (
        "APPROVED_AND_MARS_EXECUTION_PACKET_READY"
        if all_approved_for_real_emx
        else "AWAITING_USER_ADVISOR_APPROVALS"
        if review_ready
        else "DO_NOT_REVIEW_UNTIL_INPUT_SUMMARIES_PASS"
    )
    summary = {
        "schema": "rfic_transformer_s8p_combined_approval_readiness.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "can_start_real_emx": all_approved_for_real_emx,
        "approval_state": {
            "port_map_approved": port_approved,
            "geometry_contract_approved": geometry_approved,
            "mars_execution_packet_ready": execution_ready,
        },
        "source_summaries": {
            "port_map_approval_summary": str(port_path),
            "geometry_contract_approval_summary": str(geometry_path),
            "execution_packet_summary": "" if execution_path is None else str(execution_path),
        },
        "port_map": _port_record(port),
        "geometry_contract": _geometry_record(geometry),
        "visual_artifacts": _visual_artifacts(port, geometry),
        "required_approval_command": (
            "CONFIRM_S8P_PORT_MAP_APPROVED=YES "
            "CONFIRM_S8P_GEOMETRY_CONTRACT_APPROVED=YES "
            "bash NEXT_GEN_S8P_AFTER_PORT_APPROVAL_COMMANDS_20260619.sh"
        ),
        "checks": checks,
        "artifacts": {
            "summary": str(out_dir / "s8p_combined_approval_readiness_summary.json"),
            "report": str(out_dir / "S8P_COMBINED_APPROVAL_READINESS_REPORT_CN.md"),
            "approval_board_png": str(out_dir / "s8p_combined_approval_readiness_board.png"),
        },
        "limitations": [
            "This packet is a review/readiness artifact only; it does not launch EMX, HFSS, ADS, or Cadence.",
            "A candidate approval summary is not enough to start the 500-sample EMX run.",
            "Real completion still requires 500 generated .s8p files, quality gates, random HFSS rebuild/export, and <=5% EMX/HFSS physical-feature curve comparison.",
        ],
    }
    summary["visual_artifacts"]["approval_board"] = summary["artifacts"]["approval_board_png"]
    summary_path = Path(summary["artifacts"]["summary"])
    report_path = Path(summary["artifacts"]["report"])
    board_path = Path(summary["artifacts"]["approval_board_png"])
    _write_approval_board(board_path, summary)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"can_start_real_emx={summary['can_start_real_emx']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 2 if status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port-map-approval-summary", required=True)
    parser.add_argument("--geometry-contract-approval-summary", required=True)
    parser.add_argument("--execution-packet-summary")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json_if_present(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_checks(
    port_path: Path,
    port: dict[str, Any],
    geometry_path: Path,
    geometry: dict[str, Any],
    execution_path: Path | None,
    execution: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        _check("port-map summary exists", port_path.is_file(), str(port_path)),
        _check("port-map structural status is PASS", port.get("overall_status") == "PASS", str(port.get("overall_status"))),
        _check("port-map approval status is APPROVED", _is_port_approved(port), _approval_detail(port)),
        _check("geometry-contract summary exists", geometry_path.is_file(), str(geometry_path)),
        _check("geometry-contract structural status is PASS", geometry.get("overall_status") == "PASS", str(geometry.get("overall_status"))),
        _check("geometry-contract approval status is APPROVED", _is_geometry_approved(geometry), _approval_detail(geometry)),
        _check(
            "strict MARS execution packet summary exists",
            execution_path is None or execution_path.is_file(),
            "" if execution_path is None else str(execution_path),
        ),
        _check(
            "strict MARS execution packet is READY",
            _is_execution_ready(execution),
            json.dumps(
                {
                    "overall_status": execution.get("overall_status"),
                    "decision": execution.get("decision"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if execution
            else "not supplied",
        ),
    ]


def _is_review_ready(port: dict[str, Any], geometry: dict[str, Any]) -> bool:
    return port.get("overall_status") == "PASS" and geometry.get("overall_status") == "PASS"


def _is_port_approved(data: dict[str, Any]) -> bool:
    return data.get("overall_status") == "PASS" and data.get("approval_status") == "APPROVED" and data.get("decision") == PORT_APPROVED_DECISION


def _is_geometry_approved(data: dict[str, Any]) -> bool:
    return data.get("overall_status") == "PASS" and data.get("approval_status") == "APPROVED" and data.get("decision") == GEOMETRY_APPROVED_DECISION


def _is_execution_ready(data: dict[str, Any]) -> bool:
    return data.get("overall_status") == "PASS" and data.get("decision") == EXECUTION_READY_DECISION


def _approval_detail(data: dict[str, Any]) -> str:
    return json.dumps(
        {
            "overall_status": data.get("overall_status"),
            "approval_status": data.get("approval_status"),
            "decision": data.get("decision"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _port_record(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "port_pairs": data.get("port_pairs", ""),
        "role_records": data.get("role_records", []),
        "pair_records": data.get("pair_records", []),
    }


def _geometry_record(data: dict[str, Any]) -> dict[str, Any]:
    return data.get("approved_geometry_contract") if isinstance(data.get("approved_geometry_contract"), dict) else {}


def _visual_artifacts(port: dict[str, Any], geometry: dict[str, Any]) -> dict[str, str]:
    artifacts = {
        "layout_preview": str(port.get("preview_image", "")),
        "port_debug": str(port.get("port_debug_image", "")),
        "port_map_report": str((port.get("artifacts") or {}).get("report", "")) if isinstance(port.get("artifacts"), dict) else "",
        "geometry_report": str((geometry.get("artifacts") or {}).get("report", "")) if isinstance(geometry.get("artifacts"), dict) else "",
    }
    return artifacts


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if passed else "WAITING" if "APPROVED" in name or "READY" in name else "FAIL", "name": name, "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    state = summary["approval_state"]
    lines = [
        "# S8P Combined Approval Readiness Report",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Can start real EMX now: **{summary['can_start_real_emx']}**",
        "",
        "## Approval State",
        "",
        f"- Port map approved: `{state['port_map_approved']}`",
        f"- Geometry contract approved: `{state['geometry_contract_approved']}`",
        f"- MARS execution packet ready: `{state['mars_execution_packet_ready']}`",
        "",
        "## Port Map To Review",
        "",
        f"- Differential pair syntax: `{summary['port_map'].get('port_pairs', '')}`",
        "- Pair 1 / `Lp`: `P001-P004` / M10 primary winding terminals.",
        "- Pair 2 / `Ls`: `P005-P006` / M9 secondary winding terminals.",
        "",
        "| Order | Port | Ground | Role | Winding | Side |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in summary["port_map"].get("role_records", []):
        lines.append(
            f"| {record.get('order', '')} | {record.get('port', '')} | {record.get('ground', '')} | "
            f"{record.get('role', '')} | {record.get('winding_role', '')} | {record.get('physical_side', '')} |"
        )
    geometry = summary.get("geometry_contract") or {}
    lines.extend(
        [
            "",
            "## Geometry Contract To Review",
            "",
            f"- Bridge width: `{geometry.get('bridge_width_um')}` um.",
            f"- Bridge-width basis: `{geometry.get('bridge_width_contract_basis')}`.",
            f"- Superseded literal 10nm value recorded/rejected: `{geometry.get('superseded_literal_10nm_bridge_width_um')}` um.",
            f"- Vertical length reference: `{geometry.get('vertical_length_reference_dimension')}`.",
            f"- Vertical length ratio: `{geometry.get('vertical_length_diameter_ratio')}`.",
            f"- Ground-frame width: `{geometry.get('ground_frame_width_um')}` um.",
            f"- Ground-frame policy: `{geometry.get('ground_frame_policy')}`.",
            "",
            "## Gate Checks",
            "",
            "| Status | Check | Detail |",
            "| --- | --- | --- |",
        ]
    )
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {_cell(check['name'])} | {_cell(check['detail'])} |")
    visuals = summary.get("visual_artifacts") or {}
    lines.extend(
        [
            "",
            "## Visual / Source Evidence",
            "",
            f"- Approval board PNG: `{visuals.get('approval_board', '')}`",
            f"- Layout preview: `{visuals.get('layout_preview', '')}`",
            f"- Port debug: `{visuals.get('port_debug', '')}`",
            f"- Port-map report: `{visuals.get('port_map_report', '')}`",
            f"- Geometry report: `{visuals.get('geometry_report', '')}`",
            "",
            "## After Approval",
            "",
            "Run this only after user/advisor approval of both the port map and the geometry contract:",
            "",
            "```bash",
            "cd /home/researcher/Documents/模拟变压器AI反向建模",
            summary["required_approval_command"],
            "```",
            "",
            "This command still does not start EMX. Real EMX remains blocked until strict MARS/Cadence/PDK path preflight passes.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _write_approval_board(path: Path, summary: dict[str, Any]) -> None:
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(16, 10), dpi=180)
    gs = fig.add_gridspec(2, 3, width_ratios=[1.05, 1.05, 1.2], height_ratios=[1, 1], wspace=0.18, hspace=0.24)
    fig.patch.set_facecolor("#f7f8fb")
    fig.suptitle("S8P Transformer Approval Readiness", fontsize=20, fontweight="bold", color="#1f2937", y=0.98)

    _draw_image_panel(fig.add_subplot(gs[0, 0]), "Layout Preview", (summary.get("visual_artifacts") or {}).get("layout_preview", ""))
    _draw_image_panel(fig.add_subplot(gs[1, 0]), "Port Debug", (summary.get("visual_artifacts") or {}).get("port_debug", ""))

    ax_port = fig.add_subplot(gs[:, 1])
    ax_port.axis("off")
    _draw_text_box(
        ax_port,
        "Port Map Review",
        [
            f"Pair syntax: {(summary.get('port_map') or {}).get('port_pairs', '')}",
            "Pair 1 / Lp: P001-P004, M10 primary winding terminals",
            "Pair 2 / Ls: P005-P006, M9 secondary winding terminals",
            "",
            "P002/P003: left vertical power line",
            "P007/P008: right vertical power line",
            "All ports are shield-ground referenced with *_G labels",
        ],
        y_top=0.96,
    )
    _draw_port_table(ax_port, (summary.get("port_map") or {}).get("role_records", []), y_top=0.52)

    ax_status = fig.add_subplot(gs[0, 2])
    ax_status.axis("off")
    state = summary.get("approval_state") or {}
    _draw_status_panel(ax_status, summary, state)

    ax_geom = fig.add_subplot(gs[1, 2])
    ax_geom.axis("off")
    geometry = summary.get("geometry_contract") or {}
    _draw_text_box(
        ax_geom,
        "Geometry Contract Review",
        [
            f"Bridge width: {geometry.get('bridge_width_um')} um",
            "Basis: bridge width equals vertical power-line width",
            f"Rejected literal 10nm value: {geometry.get('superseded_literal_10nm_bridge_width_um')} um",
            f"Vertical length: {geometry.get('vertical_length_diameter_ratio')} x max coil height",
            f"Reference: {geometry.get('vertical_length_reference_dimension')}",
            f"Ground frame: {geometry.get('ground_frame_width_um')} um M5 rectangular frame",
            "Current board is review evidence only; real EMX is still blocked.",
        ],
        y_top=0.96,
    )

    fig.text(
        0.5,
        0.015,
        "Generated from candidate approval summaries. Do not launch real EMX until both approvals and strict MARS path preflight pass.",
        ha="center",
        va="bottom",
        fontsize=10,
        color="#4b5563",
    )
    fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def _draw_image_panel(ax: Any, title: str, path_raw: Any) -> None:
    from matplotlib import image as mpimg

    ax.set_title(title, fontsize=13, fontweight="bold", color="#1f2937")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#cbd5e1")
        spine.set_linewidth(1.0)
    path = Path(str(path_raw)).expanduser() if path_raw else Path("")
    if path.is_file():
        image = mpimg.imread(path)
        ax.imshow(image)
        ax.set_xlabel(path.name, fontsize=8, color="#6b7280")
        return
    ax.text(0.5, 0.5, f"Missing image\n{path_raw}", ha="center", va="center", fontsize=10, color="#991b1b")


def _draw_text_box(ax: Any, title: str, lines: list[str], *, y_top: float) -> None:
    ax.text(0.02, y_top, title, transform=ax.transAxes, fontsize=15, fontweight="bold", color="#111827", va="top")
    y = y_top - 0.08
    for line in lines:
        if not line:
            y -= 0.035
            continue
        ax.text(0.03, y, f"- {line}", transform=ax.transAxes, fontsize=10.5, color="#374151", va="top", wrap=True)
        y -= 0.058


def _draw_port_table(ax: Any, records: list[dict[str, Any]], *, y_top: float) -> None:
    rows = [[record.get("port", ""), record.get("ground", ""), record.get("winding_role", ""), record.get("physical_side", "")] for record in records]
    table = ax.table(
        cellText=rows,
        colLabels=["Port", "Ground", "Winding", "Side"],
        loc="lower left",
        bbox=[0.02, 0.02, 0.96, y_top - 0.04],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor("#d1d5db")
        if row == 0:
            cell.set_facecolor("#e5e7eb")
            cell.set_text_props(weight="bold", color="#111827")
        else:
            cell.set_facecolor("#ffffff" if row % 2 else "#f9fafb")


def _draw_status_panel(ax: Any, summary: dict[str, Any], state: dict[str, Any]) -> None:
    status_color = "#991b1b" if not summary.get("can_start_real_emx") else "#166534"
    _draw_text_box(
        ax,
        "Run Gate Status",
        [
            f"Decision: {summary.get('decision')}",
            f"Can start real EMX: {summary.get('can_start_real_emx')}",
            f"Port map approved: {state.get('port_map_approved')}",
            f"Geometry approved: {state.get('geometry_contract_approved')}",
            f"MARS runbook ready: {state.get('mars_execution_packet_ready')}",
        ],
        y_top=0.96,
    )
    ax.text(
        0.03,
        0.40,
        "CURRENT ACTION: REVIEW ONLY" if not summary.get("can_start_real_emx") else "READY AFTER FINAL PATH PREFLIGHT",
        transform=ax.transAxes,
        fontsize=14,
        fontweight="bold",
        color=status_color,
        va="top",
    )
    ax.text(
        0.03,
        0.30,
        "Required approval command:\n"
        "CONFIRM_S8P_PORT_MAP_APPROVED=YES\n"
        "CONFIRM_S8P_GEOMETRY_CONTRACT_APPROVED=YES\n"
        "bash NEXT_GEN_S8P_AFTER_PORT_APPROVAL_COMMANDS_20260619.sh",
        transform=ax.transAxes,
        fontsize=9,
        color="#374151",
        va="top",
        family="monospace",
    )


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
