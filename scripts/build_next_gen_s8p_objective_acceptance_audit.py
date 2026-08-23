#!/usr/bin/env python3
"""Build a requirement-by-requirement acceptance audit for the next-gen S8P goal.

This is a read-only audit. It does not run Cadence, EMX, HFSS, ADS, or any
plotting workflow. It maps the post-meeting user objective into explicit
evidence items so local readiness cannot be mistaken for completed physics.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Evidence:
    objective_id: str
    objective: str
    status: str
    requirement: str
    evidence: str
    next_action: str

    def as_dict(self) -> dict[str, str]:
        return {
            "objective_id": self.objective_id,
            "objective": self.objective,
            "status": self.status,
            "requirement": self.requirement,
            "evidence": self.evidence,
            "next_action": self.next_action,
        }


OBJECTIVES = {
    "1": "Input changes from Zin to Lp/Ls/Q/K and can invert physical features to geometry",
    "2": "Training-data structure is the approved 8-port vertical power-line S8P topology",
    "3": "Generate 500 real EMX S8P samples with 8 parallel workers",
    "4": "Random sample is verified by HFSS and EMX/HFSS Lp/Ls/Q/K/Kw curves",
    "5": "Ambiguities are explicit and no incomplete work is reported as finished",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    launch_summary_path = Path(args.launch_summary).expanduser().resolve()
    combined_summary_path = Path(args.combined_approval_summary).expanduser().resolve()
    run_status_path = Path(args.run_status_summary).expanduser().resolve()
    sync_summary_path = Path(args.sync_summary).expanduser().resolve() if args.sync_summary else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    launch = _read_json(launch_summary_path)
    combined = _read_json(combined_summary_path)
    run_status = _read_json(run_status_path)
    sync = _read_json(sync_summary_path) if sync_summary_path else {}

    evidence: list[Evidence] = []
    evidence.extend(_objective_1(launch, run_status))
    evidence.extend(_objective_2(launch, combined))
    evidence.extend(_objective_3(launch, run_status))
    evidence.extend(_objective_4(run_status))
    evidence.extend(_objective_5(launch, combined, run_status, sync, bool(sync_summary_path)))

    objective_statuses = _objective_statuses(evidence)
    final_ready = bool(objective_statuses) and all(item == "PASS" for item in objective_statuses.values())
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if final_ready else ("FAIL" if any(item == "FAIL" for item in objective_statuses.values()) else "WAITING"),
        "decision": "READY_TO_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE" if final_ready else "DO_NOT_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE",
        "final_objective_ready": final_ready,
        "inputs": {
            "launch_summary": str(launch_summary_path),
            "combined_approval_summary": str(combined_summary_path),
            "run_status_summary": str(run_status_path),
            "sync_summary": "" if sync_summary_path is None else str(sync_summary_path),
        },
        "objective_statuses": objective_statuses,
        "status_counts": _status_counts(evidence),
        "evidence": [item.as_dict() for item in evidence],
        "limitations": [
            "This audit only reads existing evidence and cannot create simulator results.",
            "PASS requires current external EMX/HFSS artifacts; local code readiness alone is not enough.",
            "The bridge-width contract follows the latest approved same-width 10um interpretation; the literal 10nm interpretation is recorded as superseded.",
        ],
    }

    summary_path = out_dir / "next_gen_s8p_objective_acceptance_summary.json"
    report_path = out_dir / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md"
    csv_path = out_dir / "next_gen_s8p_objective_acceptance_evidence.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_csv(csv_path, evidence)

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"final_objective_ready={summary['final_objective_ready']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"evidence_csv={csv_path}")
    return 0 if final_ready or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-summary", required=True, help="physical_feature_s8p_launch_packet_summary.json")
    parser.add_argument("--combined-approval-summary", required=True, help="s8p_combined_approval_readiness_summary.json")
    parser.add_argument("--run-status-summary", required=True, help="next_gen_s8p_mars_run_status_summary.json")
    parser.add_argument("--sync-summary", help="Optional next_gen_s8p_mars_sync_packet_summary_20260619.json")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _objective_1(launch: dict[str, Any], run_status: dict[str, Any]) -> list[Evidence]:
    contract = launch.get("input_feature_contract") or {}
    contract_ok, detail = _physical_feature_contract_status(contract)
    evidence = [
        _ev(
            "1",
            "PASS" if launch.get("overall_status") == "PASS" and contract_ok else "FAIL",
            "Launch packet inverse-design inputs are Lp/Ls/Q/K and not Zin",
            f"launch_status={launch.get('overall_status')}, {detail}",
            "Regenerate the launch packet with physical-feature columns and no Zin-derived inputs.",
        )
    ]
    for requirement in (
        "post-EMX inverse training table uses Lp/Ls/Q/K without Zin",
        "post-EMX inverse model quality audit passed",
        "saved Lp/Ls/Q/K-to-geometry inverse model is trained",
    ):
        evidence.append(_from_run_status("1", run_status, requirement, "Run the post-EMX inverse-model commands after real .s8p labels exist."))
    return evidence


def _objective_2(launch: dict[str, Any], combined: dict[str, Any]) -> list[Evidence]:
    port_map = launch.get("port_map_approval_summary") or {}
    geometry = (combined.get("geometry_contract") or (launch.get("geometry_contract_approval_summary") or {}).get("contract") or {})
    ports = list(port_map.get("touchstone_port_order") or [])
    role_records = list((combined.get("port_map") or {}).get("role_records") or [])
    all_roles_grounded = bool(role_records) and all(str(item.get("ground") or "").endswith("_G") for item in role_records)
    bridge = _to_float(geometry.get("bridge_width_um"))
    ratio = _to_float(geometry.get("vertical_length_diameter_ratio"))
    ground = _to_float(geometry.get("ground_frame_width_um"))
    return [
        _ev(
            "2",
            "PASS" if combined.get("overall_status") == "PASS" and combined.get("can_start_real_emx") is True else "FAIL",
            "Port-map and geometry contracts are approved before real EMX",
            f"combined_status={combined.get('overall_status')}, can_start_real_emx={combined.get('can_start_real_emx')}",
            "Use only approved port-map and geometry summaries before launching MARS EMX.",
        ),
        _ev(
            "2",
            "PASS" if ports == [f"P{idx:03d}" for idx in range(1, 9)] and all_roles_grounded else "FAIL",
            "S8P has eight ordered shield-grounded ports",
            f"touchstone_port_order={ports}, grounded_role_records={all_roles_grounded}",
            "Fix the P001-P008 port map and ground labels before producing .s8p data.",
        ),
        _ev(
            "2",
            "PASS" if bridge == 10.0 and ratio == 1.5 and ground == 100.0 and geometry.get("superseded_literal_10nm_bridge_width_um") == 0.01 else "FAIL",
            "Power-line bridge, vertical length, and ground-frame geometry match approved contract",
            f"bridge_width_um={bridge}, vertical_length_ratio={ratio}, ground_frame_width_um={ground}, superseded_10nm={geometry.get('superseded_literal_10nm_bridge_width_um')}",
            "Resolve geometry-contract mismatch before launching real EMX or building HFSS comparison.",
        ),
    ]


def _objective_3(launch: dict[str, Any], run_status: dict[str, Any]) -> list[Evidence]:
    parallel = launch.get("parallel_emx_contract") or {}
    contract_ok = (
        _to_int(parallel.get("expected_emx_count")) == 500
        and _to_int(parallel.get("emx_max_count")) == 500
        and _to_int(parallel.get("expected_jobs")) == 8
        and _to_int(parallel.get("jobs")) == 8
        and "5-60" in str(parallel.get("wideband_frequency_grid") or "")
    )
    evidence = [
        _ev(
            "3",
            "PASS" if launch.get("overall_status") == "PASS" and contract_ok else "FAIL",
            "Launch packet requests 500 samples, 8 workers, and 5-60 GHz / 1.0 GHz grid",
            f"launch_status={launch.get('overall_status')}, parallel_contract={parallel}",
            "Regenerate launch packet with 500 samples, 8 workers, and wideband grid.",
        )
    ]
    for requirement in (
        "8-worker EMX candidate queue completed",
        "dataset_rows.csv has expected 500 successful rows",
        "all successful rows point to valid .s8p files",
        "all successful rows are traceable to EMX-generated .s8p files",
        "dataset manifest matches approved S8P topology contract",
        "S8P dataset quality gates",
    ):
        evidence.append(_from_run_status("3", run_status, requirement, "Start or continue the real 500-sample MARS EMX run, then run S8P quality gates."))
    return evidence


def _objective_4(run_status: dict[str, Any]) -> list[Evidence]:
    requirements = (
        "random physical-feature validation sample selected",
        "selected sample S8P port-pair physical diagnostic passed",
        "selected sample 8-port layout audit",
        "selected sample HFSS rebuild handoff",
        "selected sample HFSS AEDT scripts",
        "HFSS payload geometry views rendered",
        "EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed",
        "HFSS build port manifest proves 8-port integration lines",
        "final report evidence packet passed",
    )
    return [
        _from_run_status("4", run_status, requirement, "Run the random-sample HFSS rebuild/export and postrun validation chain.")
        for requirement in requirements
    ]


def _objective_5(
    launch: dict[str, Any],
    combined: dict[str, Any],
    run_status: dict[str, Any],
    sync: dict[str, Any],
    has_sync: bool,
) -> list[Evidence]:
    geometry = combined.get("geometry_contract") or {}
    run_complete = run_status.get("overall_status") == "PASS"
    sync_status = str(sync.get("status") or sync.get("overall_status") or "")
    launch_run_dir = str(launch.get("run_dir") or "")
    status_run_dir = str(run_status.get("run_dir") or "")
    launch_run_name = _path_name(launch_run_dir)
    status_run_name = _path_name(status_run_dir)
    if not launch_run_dir or not status_run_dir:
        run_dir_status = "WAITING"
        run_dir_detail = f"launch_run_dir={launch_run_dir or 'missing'}, run_status_run_dir={status_run_dir or 'missing'}"
    elif launch_run_name == status_run_name:
        run_dir_status = "PASS"
        run_dir_detail = f"launch_run_dir={launch_run_dir}, run_status_run_dir={status_run_dir}, run_name={launch_run_name}"
    else:
        run_dir_status = "FAIL"
        run_dir_detail = f"launch_run_dir={launch_run_dir}, run_status_run_dir={status_run_dir}, launch_name={launch_run_name}, status_name={status_run_name}"
    return [
        _ev(
            "5",
            "PASS" if geometry.get("superseded_literal_10nm_bridge_width_um") == 0.01 else "FAIL",
            "10nm bridge ambiguity is explicitly recorded as superseded by the approved 10um same-width contract",
            f"bridge_width_um={geometry.get('bridge_width_um')}, superseded_literal_10nm_bridge_width_um={geometry.get('superseded_literal_10nm_bridge_width_um')}",
            "Ask the user/advisor before changing bridge-width units; do not silently reinterpret geometry.",
        ),
        _ev(
            "5",
            "PASS" if not run_complete else "PASS",
            "Audit boundary prevents claiming completion until all external evidence is present",
            f"run_status={run_status.get('overall_status')}, decision={run_status.get('decision')}",
            "Keep objective active while run status is not PASS.",
        ),
        _ev(
            "5",
            "PASS" if (not has_sync or sync_status == "PASS") else "FAIL",
            "MARS sync packet is hash-checked when supplied",
            f"sync_status={sync_status or 'not_supplied'}, tarball_check={_sync_tarball_check(sync)}",
            "Regenerate and verify the MARS sync packet before transferring code to MARS.",
        ),
        _ev(
            "5",
            run_dir_status,
            "Launch packet and run-status evidence refer to the same candidate run directory",
            run_dir_detail,
            "Regenerate the launch packet or run-status audit so both point to the same 500-sample EMX run directory.",
        ),
    ]


def _from_run_status(objective_id: str, run_status: dict[str, Any], requirement: str, fallback_action: str) -> Evidence:
    record = _run_status_record(run_status, requirement)
    if not record:
        return _ev(objective_id, "WAITING", requirement, "run-status evidence missing", fallback_action)
    status = str(record.get("status") or "WAITING")
    if status not in {"PASS", "FAIL", "WAITING", "QUESTION"}:
        status = "WAITING"
    return _ev(
        objective_id,
        status,
        requirement,
        str(record.get("evidence") or ""),
        str(record.get("next_action") or fallback_action),
    )


def _run_status_record(run_status: dict[str, Any], requirement: str) -> dict[str, Any]:
    for item in run_status.get("evidence") or []:
        if item.get("requirement") == requirement:
            return item
    return {}


def _physical_feature_contract_status(contract: Any) -> tuple[bool, str]:
    if not isinstance(contract, dict) or not contract:
        return False, "input_feature_contract=missing"
    zin_columns = list(contract.get("zin_columns") or [])
    required = {
        "lp": bool(contract.get("lp_columns")),
        "ls": bool(contract.get("ls_columns")),
        "q": bool(contract.get("q_columns")),
        "k": bool(contract.get("k_columns")),
    }
    return (not zin_columns and all(required.values())), f"zin_columns={zin_columns}, required={required}"


def _objective_statuses(evidence: list[Evidence]) -> dict[str, str]:
    out: dict[str, str] = {}
    for objective_id in OBJECTIVES:
        statuses = [item.status for item in evidence if item.objective_id == objective_id]
        if not statuses:
            out[objective_id] = "WAITING"
        elif "FAIL" in statuses:
            out[objective_id] = "FAIL"
        elif "WAITING" in statuses or "QUESTION" in statuses:
            out[objective_id] = "WAITING"
        else:
            out[objective_id] = "PASS"
    return out


def _status_counts(evidence: list[Evidence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.status] = counts.get(item.status, 0) + 1
    return dict(sorted(counts.items()))


def _sync_tarball_check(sync: dict[str, Any]) -> str:
    for check in sync.get("checks") or []:
        if check.get("name") == "tarball_sha256_matches":
            return str(check.get("pass"))
    return "not_found"


def _ev(objective_id: str, status: str, requirement: str, evidence: str, next_action: str) -> Evidence:
    return Evidence(objective_id, OBJECTIVES[objective_id], status, requirement, evidence, next_action)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}
    return payload if isinstance(payload, dict) else {}


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_name(path: str) -> str:
    return Path(path).name if path else ""


def _write_csv(path: Path, evidence: list[Evidence]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["objective_id", "objective", "status", "requirement", "evidence", "next_action"])
        writer.writeheader()
        writer.writerows(item.as_dict() for item in evidence)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Gen S8P Objective Acceptance Audit",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Final objective ready: `{summary['final_objective_ready']}`",
        f"- Status counts: `{summary['status_counts']}`",
        "",
        "## Objective Status",
        "",
    ]
    for objective_id, status in summary.get("objective_statuses", {}).items():
        lines.append(f"- `{status}` {objective_id}. {OBJECTIVES.get(objective_id, objective_id)}")
    lines.extend(["", "## Evidence", "", "| Objective | Status | Requirement | Evidence | Next action |", "| --- | --- | --- | --- | --- |"])
    for item in summary.get("evidence", []):
        lines.append(
            f"| {_cell(item['objective_id'])} | {_cell(item['status'])} | {_cell(item['requirement'])} | "
            f"{_cell(item['evidence'])} | {_cell(item['next_action'])} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary.get("limitations", []))
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
