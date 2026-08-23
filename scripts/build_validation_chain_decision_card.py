#!/usr/bin/env python3
"""Build a strict EMX -> HFSS geometry -> HFSS physical -> ADS decision card.

The script is intentionally read-only with respect to solver data. It does not
run EMX, HFSS, or ADS. It reads the existing gate summaries and records whether
the project is allowed to advance from EMX reference acceptance to HFSS geometry
traceability, HFSS physical audit, and then to the final <=5% EMX-vs-HFSS/ADS
comparison.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_PACKAGE_DIR = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
DEFAULT_EMX_FIRST = (
    DEFAULT_PACKAGE_DIR
    / "emx_first_validation_gate_current_rerun_20260614"
    / "emx_first_validation_gate_summary.json"
)
DEFAULT_HFSS_PHYSICAL = (
    DEFAULT_PACKAGE_DIR
    / "hfss_physical_gate_current_rerun_20260614"
    / "touchstone_transformer_audit_summary.json"
)
DEFAULT_HFSS_GEOMETRY = (
    DEFAULT_PACKAGE_DIR
    / "hfss_model_geometry_asset_audit_20260614"
    / "hfss_model_geometry_asset_audit_summary.json"
)
DEFAULT_OUT_DIR = DEFAULT_PROJECT_ROOT / "validation_chain_decision_20260614"

EMX_REQUIRED_CHECKS = (
    "source identity",
    "source provenance header",
    "S-matrix shape",
    "frequency row count",
    "finite numeric values",
    "frequency monotonicity",
    "reciprocity",
    "passivity",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "final ADS sweep coverage",
    "ADS no-extrapolation plot grid",
    "target frequency availability",
    "ADS photo anchor",
    "basic numeric physics sanity",
    "physical metric window",
    "smooth transformer metric window",
    "approved port-pair photo alignment",
)

HFSS_REQUIRED_CHECKS = (
    "source identity",
    "port count",
    "frequency row count",
    "finite numeric values",
    "frequency monotonicity",
    "reciprocity",
    "passivity",
    "required ADS sweep coverage",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "ADS-equivalent metric finiteness",
    "target frequency point",
    "target-frequency transformer metrics",
    "positive metric window",
    "smooth transformer metric window",
)

HFSS_GEOMETRY_REQUIRED_CHECKS = (
    "HFSS top-view PNG",
    "HFSS isometric-view PNG",
    "HFSS geometry-quality PNG",
    "HFSS STEP model",
)

ACCEPTED_COMPARISON_REQUIRED_CHECKS = (
    "accepted EMX import status",
    "accepted EMX import decision",
    "accepted EMX local SHA",
    "accepted EMX import verifier evidence",
    "accepted EMX import artifact bundle",
    "accepted EMX import core metric artifact",
    "HFSS geometry asset audit evidence",
    "HFSS geometry required asset checks",
    "HFSS geometry artifact paths",
    "HFSS Touchstone physical gate",
    "HFSS Touchstone physical gate status",
    "HFSS Touchstone required differential/physics checks",
    "HFSS Touchstone physical gate internal checks",
    "EMX-vs-HFSS compare status",
    "EMX-vs-HFSS compare source traceability",
    "EMX-vs-HFSS compare criterion",
    "EMX-vs-HFSS compare frequency-grid checks",
    "EMX-vs-HFSS compare core metric errors",
    "ADS/Python formula note",
    "ADS-style plot_data integrity",
    "ADS-style core metric figures",
)

CORE_METRICS = ("k", "qp", "qs", "lp_nh", "ls_nh")


@dataclass(frozen=True)
class Stage:
    name: str
    status: str
    decision: str
    evidence: str
    finding: str
    next_action: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "decision": self.decision,
            "evidence": self.evidence,
            "finding": self.finding,
            "next_action": self.next_action,
            "details": self.details,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    emx_summary_path = Path(args.emx_first_summary).expanduser().resolve()
    hfss_geometry_summary_path = Path(args.hfss_geometry_summary).expanduser().resolve() if args.hfss_geometry_summary else None
    hfss_summary_path = Path(args.hfss_physical_summary).expanduser().resolve() if args.hfss_physical_summary else None
    accepted_summary_path = Path(args.accepted_validation_summary).expanduser().resolve() if args.accepted_validation_summary else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    emx_summary = _read_json(emx_summary_path)
    hfss_geometry_summary = _read_json(hfss_geometry_summary_path) if hfss_geometry_summary_path else {"_missing": "not supplied"}
    hfss_summary = _read_json(hfss_summary_path) if hfss_summary_path else {"_missing": "not supplied"}
    accepted_summary = _read_json(accepted_summary_path) if accepted_summary_path else {"_missing": "not supplied"}

    emx_stage = _evaluate_emx_stage(emx_summary_path, emx_summary, args)
    hfss_geometry_stage = _evaluate_hfss_geometry_stage(hfss_geometry_summary_path, hfss_geometry_summary, emx_stage)
    hfss_stage = _evaluate_hfss_stage(hfss_summary_path, hfss_summary, emx_stage, args)
    comparison_stage = _evaluate_comparison_stage(
        accepted_summary_path,
        accepted_summary,
        emx_stage,
        hfss_geometry_stage,
        hfss_stage,
        args,
    )
    stages = [emx_stage, hfss_geometry_stage, hfss_stage, comparison_stage]
    overall_status, decision = _overall_decision(stages)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "arguments": {
            "expected_start_ghz": float(args.expected_start_ghz),
            "expected_stop_ghz": float(args.expected_stop_ghz),
            "expected_step_ghz": float(args.expected_step_ghz),
            "expected_points": int(args.expected_points),
            "max_percent_error": float(args.max_percent_error),
            "target_ghz": float(args.target_ghz),
        },
        "inputs": {
            "emx_first_summary": _file_record(emx_summary_path),
            "hfss_geometry_summary": _file_record(hfss_geometry_summary_path) if hfss_geometry_summary_path else None,
            "hfss_physical_summary": _file_record(hfss_summary_path) if hfss_summary_path else None,
            "accepted_validation_summary": _file_record(accepted_summary_path) if accepted_summary_path else None,
        },
        "stages": [stage.as_dict() for stage in stages],
        "limitations": [
            "This script only evaluates existing JSON evidence.",
            "It does not run EMX, HFSS, ADS, Cadence, or MARS.",
            "A diagnostic HFSS geometry or physical PASS cannot override a failed EMX-first golden-reference gate.",
            "Final comparison requires both HFSS geometry asset traceability and HFSS physical S4P gates before the accepted EMX-vs-HFSS/ADS <=5% gate.",
        ],
    }

    summary_path = out_dir / "validation_chain_decision_summary.json"
    report_path = out_dir / "validation_chain_decision_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for stage in stages:
        print(f"{stage.status:22s} {stage.name}: {stage.finding}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx-first-summary", default=str(DEFAULT_EMX_FIRST))
    parser.add_argument("--hfss-geometry-summary", default=str(DEFAULT_HFSS_GEOMETRY))
    parser.add_argument("--hfss-physical-summary", default=str(DEFAULT_HFSS_PHYSICAL))
    parser.add_argument("--accepted-validation-summary")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-stop-ghz", type=float, default=50.0)
    parser.add_argument("--expected-step-ghz", type=float, default=0.1)
    parser.add_argument("--expected-points", type=int, default=451)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=5.0)
    parser.add_argument("--frequency-tolerance-ghz", type=float, default=1.0e-4)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _evaluate_emx_stage(path: Path, summary: dict[str, Any], args: argparse.Namespace) -> Stage:
    evidence = str(path)
    if "_missing" in summary or "_parse_error" in summary:
        return Stage(
            name="EMX-first golden reference",
            status="MISSING",
            decision="WAIT_FOR_EMX_FIRST_GATE",
            evidence=evidence,
            finding=_missing_detail(summary),
            next_action="Run target wideband EMX on MARS, then run build_emx_first_validation_gate.py on the resulting EMX S4P.",
            details={"missing": summary},
        )

    check_failures = _required_check_failures(summary, EMX_REQUIRED_CHECKS)
    frequency_failures = _frequency_failures(
        _frequency_from_emx_summary(summary),
        expected_start_ghz=float(args.expected_start_ghz),
        expected_stop_ghz=float(args.expected_stop_ghz),
        expected_step_ghz=float(args.expected_step_ghz),
        expected_points=int(args.expected_points),
        tolerance_ghz=float(args.frequency_tolerance_ghz),
    )
    status = str(summary.get("overall_status", "UNKNOWN"))
    decision = str(summary.get("decision", "UNKNOWN"))
    accepted = (
        status == "PASS"
        and decision == "ACCEPT_AS_GOLDEN_EMX_REFERENCE"
        and not check_failures
        and not frequency_failures
    )
    if accepted:
        stage_status = "PASS"
        stage_decision = "ALLOW_HFSS_COMPARISON_PREP"
        finding = "EMX source is accepted as the golden ADS/physics reference."
        next_action = "Proceed to HFSS geometry traceability, HFSS physical gate, and final accepted EMX/HFSS/ADS comparison."
    else:
        stage_status = "FAIL"
        stage_decision = "BLOCK_HFSS_COMPARISON"
        failure_text = _join_failures(check_failures + frequency_failures)
        finding = (
            f"EMX reference is not accepted: overall_status={status}, decision={decision}; "
            f"{failure_text or 'no detailed failing check was found, but status/decision is not accepted'}."
        )
        next_action = "Regenerate the target EMX wideband S4P on MARS and pass EMX-first before using HFSS comparison figures."
    return Stage(
        name="EMX-first golden reference",
        status=stage_status,
        decision=stage_decision,
        evidence=evidence,
        finding=finding,
        next_action=next_action,
        details={
            "overall_status": status,
            "source_decision": decision,
            "frequency": _frequency_from_emx_summary(summary),
            "target_record": summary.get("target_record"),
            "failed_required_checks": check_failures,
            "frequency_failures": frequency_failures,
        },
    )


def _evaluate_hfss_stage(
    path: Path | None,
    summary: dict[str, Any],
    emx_stage: Stage,
    args: argparse.Namespace,
) -> Stage:
    evidence = str(path) if path else "not supplied"
    if "_missing" in summary or "_parse_error" in summary:
        return Stage(
            name="HFSS physical S4P gate",
            status="MISSING",
            decision="WAIT_FOR_HFSS_PHYSICAL_GATE",
            evidence=evidence,
            finding=_missing_detail(summary),
            next_action="Run audit_touchstone_transformer.py on the HFSS S4P after HFSS export.",
            details={"missing": summary},
        )

    check_failures = _required_check_failures(summary, HFSS_REQUIRED_CHECKS)
    frequency_failures = _frequency_failures(
        _frequency_from_touchstone_summary(summary),
        expected_start_ghz=None,
        expected_stop_ghz=float(args.expected_stop_ghz),
        expected_step_ghz=float(args.expected_step_ghz),
        expected_points=None,
        tolerance_ghz=float(args.frequency_tolerance_ghz),
        require_cover_start_ghz=float(args.expected_start_ghz),
    )
    source_status = str(summary.get("overall_status", "UNKNOWN"))
    physical_pass = source_status == "PASS" and not check_failures and not frequency_failures
    if physical_pass and emx_stage.status == "PASS":
        stage_status = "PASS"
        stage_decision = "ALLOW_FINAL_COMPARISON"
        finding = "HFSS S4P passes the physical gate and accepted EMX is available."
        next_action = "Run run_accepted_emx_hfss_ads_validation.py."
    elif physical_pass:
        stage_status = "PASS_DIAGNOSTIC_ONLY"
        stage_decision = "DO_NOT_COMPARE_UNTIL_EMX_ACCEPTED"
        finding = "HFSS S4P passes as a standalone physical diagnostic, but EMX-first is not accepted."
        next_action = "Keep HFSS plots as diagnostic evidence only; recover accepted EMX before final comparison."
    else:
        stage_status = "FAIL"
        stage_decision = "REBUILD_OR_REEXPORT_HFSS"
        finding = (
            f"HFSS physical gate is not accepted: overall_status={source_status}; "
            f"{_join_failures(check_failures + frequency_failures)}."
        )
        next_action = "Fix HFSS model/export, rerun the S4P physical gate, then compare only after EMX-first also passes."
    return Stage(
        name="HFSS physical S4P gate",
        status=stage_status,
        decision=stage_decision,
        evidence=evidence,
        finding=finding,
        next_action=next_action,
        details={
            "overall_status": source_status,
            "frequency": _frequency_from_touchstone_summary(summary),
            "target_point": (summary.get("metric_summary") or {}).get("target_point"),
            "failed_required_checks": check_failures,
            "frequency_failures": frequency_failures,
        },
    )


def _evaluate_hfss_geometry_stage(path: Path | None, summary: dict[str, Any], emx_stage: Stage) -> Stage:
    evidence = str(path) if path else "not supplied"
    if "_missing" in summary or "_parse_error" in summary:
        return Stage(
            name="HFSS geometry asset traceability",
            status="MISSING",
            decision="WAIT_FOR_HFSS_GEOMETRY_AUDIT",
            evidence=evidence,
            finding=_missing_detail(summary),
            next_action="Run audit_hfss_model_geometry_assets.py on the HFSS model-view PNGs and STEP export.",
            details={"missing": summary},
        )

    check_failures = _required_check_failures(summary, HFSS_GEOMETRY_REQUIRED_CHECKS)
    status = str(summary.get("overall_status", "UNKNOWN"))
    decision = str(summary.get("decision", "UNKNOWN"))
    accepted = status == "PASS" and decision == "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS" and not check_failures
    if accepted and emx_stage.status == "PASS":
        stage_status = "PASS"
        stage_decision = "ALLOW_HFSS_PHYSICAL_GATE"
        finding = "HFSS model geometry assets are traceable and accepted for final-chain use."
        next_action = "Continue to the HFSS physical S4P gate for the same modeled sample."
    elif accepted:
        stage_status = "PASS_DIAGNOSTIC_ONLY"
        stage_decision = "DO_NOT_USE_UNTIL_EMX_ACCEPTED"
        finding = "HFSS geometry assets are traceable, but EMX-first is not accepted."
        next_action = "Keep HFSS model views as traceability evidence only; recover accepted EMX before final comparison."
    else:
        stage_status = "FAIL"
        stage_decision = "REBUILD_OR_REEXPORT_HFSS_GEOMETRY_ASSETS"
        finding = (
            f"HFSS geometry traceability is not accepted: overall_status={status}, decision={decision}; "
            f"{_join_failures(check_failures) or 'status/decision is not accepted'}."
        )
        next_action = "Fix or regenerate HFSS model-view PNGs and STEP export, then rerun audit_hfss_model_geometry_assets.py."
    return Stage(
        name="HFSS geometry asset traceability",
        status=stage_status,
        decision=stage_decision,
        evidence=evidence,
        finding=finding,
        next_action=next_action,
        details={
            "overall_status": status,
            "source_decision": decision,
            "artifacts": summary.get("artifacts"),
            "failed_required_checks": check_failures,
        },
    )


def _evaluate_comparison_stage(
    path: Path | None,
    summary: dict[str, Any],
    emx_stage: Stage,
    hfss_geometry_stage: Stage,
    hfss_stage: Stage,
    args: argparse.Namespace,
) -> Stage:
    evidence = str(path) if path else "not supplied"
    if emx_stage.status != "PASS":
        return Stage(
            name="Accepted EMX-vs-HFSS/ADS comparison",
            status="BLOCKED_BY_EMX_REFERENCE",
            decision="DO_NOT_USE_HFSS_COMPARISON",
            evidence=evidence,
            finding="Final comparison is blocked because EMX-first did not accept a golden EMX reference.",
            next_action=emx_stage.next_action,
            details={"blocking_stage": emx_stage.name},
        )
    if hfss_geometry_stage.status != "PASS":
        return Stage(
            name="Accepted EMX-vs-HFSS/ADS comparison",
            status="BLOCKED_BY_HFSS_GEOMETRY_GATE",
            decision="DO_NOT_USE_HFSS_COMPARISON",
            evidence=evidence,
            finding="Final comparison is blocked because HFSS geometry traceability is not accepted with an accepted EMX reference.",
            next_action=hfss_geometry_stage.next_action,
            details={"blocking_stage": hfss_geometry_stage.name},
        )
    if hfss_stage.status != "PASS":
        return Stage(
            name="Accepted EMX-vs-HFSS/ADS comparison",
            status="BLOCKED_BY_HFSS_PHYSICAL_GATE",
            decision="DO_NOT_USE_HFSS_COMPARISON",
            evidence=evidence,
            finding="Final comparison is blocked because HFSS physical gate is not accepted with an accepted EMX reference.",
            next_action=hfss_stage.next_action,
            details={"blocking_stage": hfss_stage.name},
        )
    if "_missing" in summary or "_parse_error" in summary:
        return Stage(
            name="Accepted EMX-vs-HFSS/ADS comparison",
            status="MISSING",
            decision="WAIT_FOR_ACCEPTED_COMPARISON_RUN",
            evidence=evidence,
            finding=_missing_detail(summary),
            next_action="Run run_accepted_emx_hfss_ads_validation.py with accepted EMX import summary and HFSS S4P.",
            details={"missing": summary},
        )

    check_failures = _required_check_failures(summary, ACCEPTED_COMPARISON_REQUIRED_CHECKS)
    metric_failures = _comparison_metric_failures(summary, max_percent_error=float(args.max_percent_error))
    grid_failures = _comparison_grid_failures(
        summary,
        expected_start_ghz=float(args.expected_start_ghz),
        expected_stop_ghz=float(args.expected_stop_ghz),
        expected_points=int(args.expected_points),
        tolerance_ghz=float(args.frequency_tolerance_ghz),
    )
    status = str(summary.get("overall_status", "UNKNOWN"))
    decision = str(summary.get("decision", "UNKNOWN"))
    accepted = (
        status == "PASS"
        and decision == "ACCEPT_HFSS_VALIDATION_SAMPLE"
        and not check_failures
        and not metric_failures
        and not grid_failures
    )
    if accepted:
        stage_status = "PASS"
        stage_decision = "ACCEPT_HFSS_VALIDATION_SAMPLE"
        finding = "Final EMX-vs-HFSS/ADS comparison is accepted for the sample."
        next_action = "Use the generated EMX, HFSS, and overlay Lp/Ls/Qp/Qs/K figures as accepted evidence."
    else:
        stage_status = "FAIL"
        stage_decision = "DO_NOT_USE_HFSS_COMPARISON"
        finding = (
            f"Final comparison is not accepted: overall_status={status}, decision={decision}; "
            f"{_join_failures(check_failures + metric_failures + grid_failures)}."
        )
        next_action = "Fix the failing accepted-comparison evidence before reporting a <=5% EMX-vs-HFSS result."
    return Stage(
        name="Accepted EMX-vs-HFSS/ADS comparison",
        status=stage_status,
        decision=stage_decision,
        evidence=evidence,
        finding=finding,
        next_action=next_action,
        details={
            "overall_status": status,
            "source_decision": decision,
            "failed_required_checks": check_failures,
            "metric_failures": metric_failures,
            "grid_failures": grid_failures,
        },
    )


def _overall_decision(stages: list[Stage]) -> tuple[str, str]:
    if all(stage.status == "PASS" for stage in stages):
        return "PASS", "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN"
    if stages[0].status in {"FAIL", "MISSING"}:
        return "BLOCKED_BY_EMX_REFERENCE", "DO_NOT_USE_HFSS_COMPARISON"
    if stages[1].status not in {"PASS"}:
        return "BLOCKED_BY_HFSS_GEOMETRY_GATE", "DO_NOT_USE_HFSS_COMPARISON"
    if stages[2].status not in {"PASS"}:
        return "BLOCKED_BY_HFSS_PHYSICAL_GATE", "DO_NOT_USE_HFSS_COMPARISON"
    return "INCOMPLETE", "WAIT_FOR_ACCEPTED_COMPARISON"


def _required_check_failures(summary: dict[str, Any], required_names: tuple[str, ...]) -> list[str]:
    by_name = {str(item.get("name")): item for item in summary.get("checks", [])}
    failures: list[str] = []
    for name in required_names:
        item = by_name.get(name)
        if item is None:
            failures.append(f"{name}=missing")
        elif item.get("status") != "PASS":
            failures.append(f"{name}={item.get('status')} ({item.get('detail', '')})")
    return failures


def _frequency_from_emx_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return dict(summary.get("frequency_ghz") or {})


def _frequency_from_touchstone_summary(summary: dict[str, Any]) -> dict[str, Any]:
    freq = dict(summary.get("frequency") or {})
    if not freq:
        return {}
    return {
        "start": _hz_to_ghz(freq.get("start_hz")),
        "stop": _hz_to_ghz(freq.get("stop_hz")),
        "step": _hz_to_ghz(freq.get("step_hz")),
        "points": freq.get("points"),
    }


def _frequency_failures(
    frequency: dict[str, Any],
    *,
    expected_start_ghz: float | None,
    expected_stop_ghz: float | None,
    expected_step_ghz: float | None,
    expected_points: int | None,
    tolerance_ghz: float,
    require_cover_start_ghz: float | None = None,
) -> list[str]:
    failures: list[str] = []
    if not frequency:
        return ["frequency=missing"]
    start = _float_or_none(frequency.get("start"))
    stop = _float_or_none(frequency.get("stop"))
    step = _float_or_none(frequency.get("step"))
    points = _int_or_none(frequency.get("points"))
    if expected_start_ghz is not None and (start is None or abs(start - expected_start_ghz) > tolerance_ghz):
        failures.append(f"frequency_start expected={expected_start_ghz:g}GHz actual={start}")
    if require_cover_start_ghz is not None and (start is None or start > require_cover_start_ghz + tolerance_ghz):
        failures.append(f"frequency_start must cover {require_cover_start_ghz:g}GHz actual={start}")
    if expected_stop_ghz is not None and (stop is None or abs(stop - expected_stop_ghz) > tolerance_ghz):
        failures.append(f"frequency_stop expected={expected_stop_ghz:g}GHz actual={stop}")
    if expected_step_ghz is not None and (step is None or abs(step - expected_step_ghz) > tolerance_ghz):
        failures.append(f"frequency_step expected={expected_step_ghz:g}GHz actual={step}")
    if expected_points is not None and points != expected_points:
        failures.append(f"frequency_points expected={expected_points} actual={points}")
    return failures


def _comparison_metric_failures(summary: dict[str, Any], *, max_percent_error: float) -> list[str]:
    metrics = summary.get("metrics") or {}
    failures: list[str] = []
    for metric in CORE_METRICS:
        item = metrics.get(metric)
        if not isinstance(item, dict):
            failures.append(f"metric_{metric}=missing")
            continue
        status = item.get("status")
        max_error = _float_or_none(item.get("max_percent_error"))
        if status != "PASS":
            failures.append(f"metric_{metric}_status={status}")
        if max_error is None or max_error > max_percent_error:
            failures.append(f"metric_{metric}_max_percent_error={max_error} limit={max_percent_error}")
    return failures


def _comparison_grid_failures(
    summary: dict[str, Any],
    *,
    expected_start_ghz: float,
    expected_stop_ghz: float,
    expected_points: int,
    tolerance_ghz: float,
) -> list[str]:
    window = summary.get("frequency_window_hz") or {}
    start = _hz_to_ghz(window.get("min"))
    stop = _hz_to_ghz(window.get("max"))
    points = _int_or_none(window.get("count"))
    failures = []
    if start is None or abs(start - expected_start_ghz) > tolerance_ghz:
        failures.append(f"comparison_start expected={expected_start_ghz:g}GHz actual={start}")
    if stop is None or abs(stop - expected_stop_ghz) > tolerance_ghz:
        failures.append(f"comparison_stop expected={expected_stop_ghz:g}GHz actual={stop}")
    if points != expected_points:
        failures.append(f"comparison_points expected={expected_points} actual={points}")
    return failures


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# EMX/HFSS/ADS Validation Chain Decision",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Generated UTC: `{summary['generated_utc']}`",
        "",
        "## Stages",
        "",
        "| Stage | Status | Decision | Finding | Next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for stage in summary["stages"]:
        lines.append(
            "| {name} | {status} | {decision} | {finding} | {next_action} |".format(
                name=_md_escape(stage["name"]),
                status=_md_escape(stage["status"]),
                decision=_md_escape(stage["decision"]),
                finding=_md_escape(stage["finding"]),
                next_action=_md_escape(stage["next_action"]),
            )
        )
    lines.extend(["", "## Evidence", ""])
    for key, record in summary["inputs"].items():
        if record is None:
            continue
        lines.append(f"- `{key}`: `{record.get('path')}` exists=`{record.get('exists')}` sha256=`{record.get('sha256')}`")
    lines.extend(["", "## Boundary", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"_missing": "not supplied"}
    if not path.exists():
        return {"_missing": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": f"{type(exc).__name__}: {exc}", "_path": str(path)}


def _file_record(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False, "sha256": None}
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "bytes": path.stat().st_size if exists else None,
        "sha256": _sha256(path) if exists and path.is_file() else None,
    }


def _sha256(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _missing_detail(summary: dict[str, Any]) -> str:
    if "_missing" in summary:
        return f"missing evidence: {summary['_missing']}"
    if "_parse_error" in summary:
        return f"unreadable evidence: {summary['_parse_error']}"
    return "missing or unreadable evidence"


def _join_failures(failures: list[str]) -> str:
    if not failures:
        return ""
    return "; ".join(failures[:8]) + (f"; ... {len(failures) - 8} more" if len(failures) > 8 else "")


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hz_to_ghz(value: Any) -> float | None:
    item = _float_or_none(value)
    return None if item is None else item / 1.0e9


def _md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
