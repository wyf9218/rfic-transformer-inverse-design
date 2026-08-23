#!/usr/bin/env python3
"""Build an auditable approval packet for the S8P geometry contract.

This packet is intentionally separate from EMX/HFSS execution. It records the
review decision for the 8-port vertical-power-line geometry contract before a
large MARS EMX run is allowed.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GEOMETRY_APPROVED_DECISION = "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN"
GEOMETRY_AWAITING_DECISION = "AWAITING_USER_ADVISOR_GEOMETRY_CONTRACT_APPROVAL"
GEOMETRY_REJECT_DECISION = "DO_NOT_USE_GEOMETRY_CONTRACT_UNTIL_CHECKS_PASS"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = Path(args.contract_audit_summary).expanduser().resolve()
    checklist_path = Path(args.checklist).expanduser().resolve() if args.checklist else None
    audit = _read_json_if_present(audit_path)
    checks = _build_checks(audit_path, audit, args)
    structurally_valid = all(item["status"] == "PASS" for item in checks)
    approval_status = "APPROVED" if args.approved else "CANDIDATE_REQUIRES_USER_ADVISOR_APPROVAL"
    decision = (
        GEOMETRY_APPROVED_DECISION
        if structurally_valid and args.approved
        else GEOMETRY_AWAITING_DECISION
        if structurally_valid
        else GEOMETRY_REJECT_DECISION
    )
    summary_path = out_dir / "s8p_geometry_contract_approval_summary.json"
    report_path = out_dir / "S8P_GEOMETRY_CONTRACT_APPROVAL_REPORT_CN.md"
    summary = {
        "schema": "rfic_transformer_s8p_geometry_contract_approval.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if structurally_valid else "FAIL",
        "approval_status": approval_status,
        "decision": decision,
        "contract_audit_summary": str(audit_path),
        "checklist": "" if checklist_path is None else str(checklist_path),
        "approved_geometry_contract": _approved_contract_record(audit),
        "checks": checks,
        "artifacts": {
            "summary": str(summary_path),
            "report": str(report_path),
        },
        "limitations": [
            "This approval packet records geometry-contract intent only; it does not run EMX, HFSS, ADS, or Cadence.",
            "Do not use this candidate to launch the 500-sample EMX run until approval_status is APPROVED.",
            "Final acceptance still requires generated .s8p files, S8P quality gates, and EMX/HFSS Lp/Ls/Q/K comparison.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={summary['overall_status']}")
    print(f"approval_status={approval_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 2 if summary["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-audit-summary", required=True)
    parser.add_argument("--checklist")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--approved", action="store_true", help="Only set after user/advisor approval of the geometry contract")
    parser.add_argument("--expected-bridge-width-um", type=float, default=10.0)
    parser.add_argument("--expected-superseded-10nm-um", type=float, default=0.01)
    parser.add_argument("--expected-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--expected-differential-port-pairs", default="1,4:5,6")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json_if_present(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_checks(path: Path, audit: dict[str, Any], args: argparse.Namespace) -> list[dict[str, str]]:
    expected = audit.get("expected") if isinstance(audit, dict) else {}
    power = audit.get("power_line_8port") if isinstance(audit, dict) else {}
    pair_map = audit.get("differential_pair_label_map") if isinstance(audit, dict) else []
    check_status = {item.get("name"): item.get("status") for item in audit.get("checks", []) if isinstance(item, dict)}
    return [
        _check("contract audit summary exists", path.is_file(), str(path)),
        _check("contract audit overall status is PASS", audit.get("overall_status") == "PASS", str(audit.get("overall_status"))),
        _check(
            "contract audit decision is ready",
            audit.get("decision") == "READY_FOR_8PORT_MARS_EMX_RUN",
            str(audit.get("decision")),
        ),
        _check(
            "bridge width is same-width 10um contract",
            _float_eq(power.get("bridge_width_um"), args.expected_bridge_width_um),
            f"bridge_width_um={power.get('bridge_width_um')}",
        ),
        _check(
            "literal 10nm bridge interpretation is rejected",
            check_status.get("literal 10nm bridge interpretation is rejected") == "PASS"
            and _float_eq(expected.get("superseded_literal_10nm_bridge_width_um"), args.expected_superseded_10nm_um),
            json.dumps(
                {
                    "audit_check": check_status.get("literal 10nm bridge interpretation is rejected"),
                    "superseded_literal_10nm_bridge_width_um": expected.get("superseded_literal_10nm_bridge_width_um"),
                },
                sort_keys=True,
            ),
        ),
        _check(
            "vertical length reference is max coil height",
            expected.get("vertical_length_reference_dimension") == "max(primary_outer_height_um, secondary_outer_height_um)",
            str(expected.get("vertical_length_reference_dimension")),
        ),
        _check(
            "vertical length ratio is 1.5",
            _float_eq(power.get("vertical_length_diameter_ratio"), 1.5),
            f"vertical_length_diameter_ratio={power.get('vertical_length_diameter_ratio')}",
        ),
        _check(
            "ground frame width is 100um",
            _float_eq(power.get("ground_frame_width_um"), args.expected_ground_frame_width_um),
            f"ground_frame_width_um={power.get('ground_frame_width_um')}",
        ),
        _check(
            "port map is P001-P008",
            power.get("port_map") == [f"P{index:03d}" for index in range(1, 9)],
            str(power.get("port_map")),
        ),
        _check(
            "differential pair map matches approved candidate",
            _pair_text(pair_map) == args.expected_differential_port_pairs,
            json.dumps(pair_map, sort_keys=True),
        ),
    ]


def _approved_contract_record(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "bridge_width_um": (audit.get("power_line_8port") or {}).get("bridge_width_um"),
        "bridge_width_contract_basis": (audit.get("expected") or {}).get("bridge_width_contract_basis"),
        "superseded_literal_10nm_bridge_width_um": (audit.get("expected") or {}).get("superseded_literal_10nm_bridge_width_um"),
        "vertical_length_reference_dimension": (audit.get("expected") or {}).get("vertical_length_reference_dimension"),
        "vertical_length_diameter_ratio": (audit.get("power_line_8port") or {}).get("vertical_length_diameter_ratio"),
        "ground_frame_width_um": (audit.get("power_line_8port") or {}).get("ground_frame_width_um"),
        "ground_frame_policy": (audit.get("power_line_8port") or {}).get("ground_frame_policy"),
        "differential_pair_label_map": audit.get("differential_pair_label_map") or [],
    }


def _pair_text(pair_map: Any) -> str:
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


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Geometry Contract Approval Report",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Approval status: **{summary['approval_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Contract audit summary: `{summary['contract_audit_summary']}`",
        f"- Checklist: `{summary.get('checklist', '')}`",
        "",
        "## Approved Geometry Contract",
        "",
        "```json",
        json.dumps(summary.get("approved_geometry_contract", {}), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {_cell(check['name'])} | {_cell(check['detail'])} |")
    lines.extend(["", "## Required Approval", ""])
    lines.append("Set `--approved` only after the user/advisor confirms this geometry contract. In particular, the current contract uses a 10.0um same-width bridge and intentionally rejects the old literal 10nm/0.01um interpretation.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
