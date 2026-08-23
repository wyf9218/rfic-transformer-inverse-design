#!/usr/bin/env python3
"""Build a read-only V66 EMX/HFSS validation report packet.

This packet is intentionally evidence-driven: it only reports artifacts that
already exist. It never fabricates plots, never marks HFSS validation complete
without a selected postrun passing variant, and never unlocks the million-sample
campaign by itself.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_POSTRUN = PROJECT_ROOT / "outputs" / "v66_postrun_gate_evidence_summary_current" / "v66_postrun_gate_evidence_summary.json"
DEFAULT_GEOMETRY = PROJECT_ROOT / "outputs" / "v66_geometry_input_contract_summary_current" / "v66_geometry_input_contract_summary.json"
DEFAULT_VISIBLE = PROJECT_ROOT / "outputs" / "hfss_v66_visible_runner_audit_current" / "hfss_v66_visible_runner_audit_summary.json"
DEFAULT_RESILIENT = PROJECT_ROOT / "outputs" / "hfss_v66_resilient_runner_audit_current" / "hfss_v66_resilient_runner_audit_summary.json"
DEFAULT_HISTORICAL_RECOMPARE = (
    PROJECT_ROOT / "outputs" / "existing_hfss_s8p_strict_recompare_current" / "existing_hfss_s8p_strict_recompare_summary.json"
)
DEFAULT_MILLION = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_execution_current" / "s8p_million_campaign_execution_summary.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "v66_validation_report_packet_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs = {
        "postrun_gate_summary": Path(args.postrun_summary).expanduser().resolve(),
        "geometry_input_summary": Path(args.geometry_summary).expanduser().resolve(),
        "visible_runner_summary": Path(args.visible_runner_summary).expanduser().resolve(),
        "resilient_runner_summary": Path(args.resilient_runner_summary).expanduser().resolve(),
        "historical_recompare_summary": Path(args.historical_recompare_summary).expanduser().resolve(),
        "million_execution_summary": Path(args.million_execution_summary).expanduser().resolve(),
    }
    postrun = _read_json(inputs["postrun_gate_summary"])
    geometry = _read_json(inputs["geometry_input_summary"])
    visible = _read_json(inputs["visible_runner_summary"])
    resilient = _read_json(inputs["resilient_runner_summary"])
    historical_recompare = _read_json(inputs["historical_recompare_summary"])
    million = _read_json(inputs["million_execution_summary"])
    selected = postrun.get("selected_variant") if isinstance(postrun.get("selected_variant"), dict) else {}
    artifact_rows = _artifact_rows(selected)
    checks = _checks(inputs, postrun, geometry, visible, resilient, historical_recompare, million, selected, artifact_rows)
    overall_status, decision = _decision(postrun, selected, checks)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "inputs": {key: str(path) for key, path in inputs.items()},
        "postrun_status": str(postrun.get("overall_status") or ""),
        "postrun_decision": str(postrun.get("decision") or ""),
        "geometry_status": str(geometry.get("overall_status") or ""),
        "visible_runner_status": str(visible.get("overall_status") or ""),
        "visible_runner_decision": str(visible.get("decision") or ""),
        "resilient_runner_status": str(resilient.get("overall_status") or ""),
        "resilient_runner_decision": str(resilient.get("decision") or ""),
        "historical_recompare_exists": inputs["historical_recompare_summary"].is_file(),
        "historical_recompare_candidate_count": int(historical_recompare.get("candidate_count") or 0),
        "historical_recompare_pass_count": int(historical_recompare.get("pass_count") or 0),
        "historical_recompare_best_fullband_worst_percent_error": _path_get(
            historical_recompare, ("best", "worst_percent_error")
        ),
        "historical_recompare_best_target15_worst_percent_error": _path_get(
            historical_recompare, ("target15_best", "target15_worst_percent_error")
        ),
        "historical_recompare_best_target15_core_percent_errors": _path_get(
            historical_recompare, ("target15_best", "target15_core_percent_errors")
        )
        or {},
        "million_execution_status": str(million.get("overall_status") or ""),
        "million_execution_decision": str(million.get("decision") or ""),
        "selected_variant": selected or {},
        "artifact_rows": artifact_rows,
        "physical_model_inputs": geometry.get("physical_model_inputs") or [],
        "geometry_outputs": (geometry.get("geometry_contract") or {}).get("geometry_columns") or [],
        "acceptance_gate": postrun.get("acceptance_gate") or {},
        "checks": checks,
        "report_sections": [
            "Current gate status",
            "Implemented automation",
            "Physical inverse-model input/output contract",
            "Selected EMX/HFSS evidence artifacts",
            "Historical HFSS S8P recompare status",
            "Million-sample lock state",
            "Next action",
        ],
    }

    summary_path = out_dir / "v66_validation_report_packet_summary.json"
    report_path = out_dir / "V66_VALIDATION_REPORT_PACKET_CN.md"
    artifact_csv = out_dir / "v66_validation_report_artifacts.csv"
    manifest_path = out_dir / "v66_validation_report_packet_manifest.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary, artifact_csv), encoding="utf-8")
    _write_artifact_csv(artifact_csv, artifact_rows)
    manifest_path.write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "inputs": [_file_record(path) for path in inputs.values()],
                "outputs": [_file_record(path) for path in (summary_path, report_path, artifact_csv)],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"artifacts_csv={artifact_csv}")
    return 0 if overall_status in {"PASS", "WAITING_FOR_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postrun-summary", default=str(DEFAULT_POSTRUN))
    parser.add_argument("--geometry-summary", default=str(DEFAULT_GEOMETRY))
    parser.add_argument("--visible-runner-summary", default=str(DEFAULT_VISIBLE))
    parser.add_argument("--resilient-runner-summary", default=str(DEFAULT_RESILIENT))
    parser.add_argument("--historical-recompare-summary", default=str(DEFAULT_HISTORICAL_RECOMPARE))
    parser.add_argument("--million-execution-summary", default=str(DEFAULT_MILLION))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _checks(
    inputs: dict[str, Path],
    postrun: dict[str, Any],
    geometry: dict[str, Any],
    visible: dict[str, Any],
    resilient: dict[str, Any],
    historical_recompare: dict[str, Any],
    million: dict[str, Any],
    selected: dict[str, Any],
    artifact_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    checks = [
        _check("postrun gate summary exists", inputs["postrun_gate_summary"].is_file(), str(inputs["postrun_gate_summary"])),
        _check("geometry input summary exists", inputs["geometry_input_summary"].is_file(), str(inputs["geometry_input_summary"])),
        _check("visible runner summary exists", inputs["visible_runner_summary"].is_file(), str(inputs["visible_runner_summary"])),
        _check_status(
            "PASS" if (not inputs["resilient_runner_summary"].is_file() or resilient.get("overall_status") != "FAIL") else "FAIL",
            "resilient runner audit not failed",
            str(resilient.get("overall_status") or "missing optional audit"),
        ),
        _check_status(
            "PASS",
            "historical HFSS recompare is advisory only",
            (
                f"exists={inputs['historical_recompare_summary'].is_file()}, "
                f"candidate_count={historical_recompare.get('candidate_count', '')}, "
                f"pass_count={historical_recompare.get('pass_count', '')}"
            ),
        ),
        _check("million execution summary exists", inputs["million_execution_summary"].is_file(), str(inputs["million_execution_summary"])),
        _check("geometry/input contract PASS", geometry.get("overall_status") == "PASS", str(geometry.get("overall_status"))),
        _check("visible runner audit not failed", visible.get("overall_status") != "FAIL", str(visible.get("overall_status"))),
        _check("million campaign remains locked before validation", million.get("decision") != "EXECUTED_REAL_MILLION_CAMPAIGN_WITHOUT_GATE", str(million.get("decision"))),
    ]
    postrun_status = str(postrun.get("overall_status") or "")
    if postrun_status == "PASS":
        checks.append(_check("selected passing V66 variant exists", bool(selected), str(selected.get("name") or "")))
        checks.append(
            _check(
                "selected variant has report artifacts",
                bool(artifact_rows) and all(row["exists"] == "PASS" for row in artifact_rows if row["required"] == "yes"),
                f"required_artifacts={len([row for row in artifact_rows if row['required'] == 'yes'])}",
            )
        )
    elif postrun_status == "WAITING_FOR_HFSS":
        checks.append(_check_status("WAITING", "selected passing V66 variant exists", "waiting for HFSS .s8p export"))
    else:
        checks.append(_check("postrun gate PASS or WAITING_FOR_HFSS", False, postrun_status))
    return checks


def _decision(
    postrun: dict[str, Any],
    selected: dict[str, Any],
    checks: list[dict[str, str]],
) -> tuple[str, str]:
    if any(item["status"] == "FAIL" for item in checks[:9]):
        return "FAIL", "FIX_REPORT_PACKET_INPUTS"
    status = str(postrun.get("overall_status") or "")
    if status == "PASS" and selected:
        if any(item["status"] == "FAIL" for item in checks):
            return "FAIL", "GATE_EVIDENCE_INCOMPLETE"
        return "PASS", "READY_FOR_PROFESSOR_REPORT_AND_MILLION_GATE"
    if status == "WAITING_FOR_HFSS":
        return "WAITING_FOR_HFSS", "WAIT_FOR_HFSS_S8P_BEFORE_REPORTING_PASS"
    return "FAIL", "EMX_HFSS_GATE_NOT_ACCEPTED"


def _artifact_rows(selected: dict[str, Any]) -> list[dict[str, str]]:
    artifacts = selected.get("artifacts") if isinstance(selected.get("artifacts"), dict) else {}
    labels = [
        ("emx_s8p", "EMX reference .s8p", True),
        ("hfss_s8p", "HFSS exported .s8p", True),
        ("target_marker_csv", "15 GHz metric/error table", True),
        ("compare_summary", "EMX/HFSS compare summary", True),
        ("ads_style_plot_summary", "ADS-style plot summary", True),
        ("emx_plot", "EMX physical-feature curve image", True),
        ("hfss_plot", "HFSS physical-feature curve image", True),
        ("overlay_plot", "EMX/HFSS overlay curve image", True),
        ("percent_error_plot", "percent-error curve image", False),
        ("metric_csv", "full metric curve CSV", True),
    ]
    rows: list[dict[str, str]] = []
    for key, label, required in labels:
        value = str(artifacts.get(key) or selected.get(key) or "")
        exists = "PASS" if value and Path(value).expanduser().is_file() else "MISSING"
        rows.append(
            {
                "key": key,
                "label": label,
                "required": "yes" if required else "no",
                "exists": exists,
                "path": value,
            }
        )
    return rows


def _render_report(summary: dict[str, Any], artifact_csv: Path) -> str:
    selected = summary.get("selected_variant") or {}
    gate = summary.get("acceptance_gate") or {}
    lines = [
        "# V66 EMX/HFSS Validation Report Packet",
        "",
        "## Current Gate Status",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Postrun gate: `{summary['postrun_status']} / {summary['postrun_decision']}`",
        f"- Visible HFSS runner: `{summary['visible_runner_status']} / {summary['visible_runner_decision']}`",
        f"- Resilient HFSS runner: `{summary['resilient_runner_status'] or 'MISSING'} / {summary['resilient_runner_decision'] or 'optional audit not generated'}`",
        f"- Historical HFSS candidates: `{summary['historical_recompare_candidate_count']}` scanned, `{summary['historical_recompare_pass_count']}` passed",
        f"- Million execution: `{summary['million_execution_status']} / {summary['million_execution_decision']}`",
        "",
        "## Implemented Automation",
        "",
        "- EMX/HFSS validation is gated by `.s8p`, 8 ports, 5-60 GHz, 1.0 GHz step, 56 points.",
        "- The HFSS visible runner refuses PASS if per-variant `.s8p` or export manifest files are missing.",
        "- The postrun gate selects a passing V66 variant only when `Lp/Ls/Q/K/Kw` target errors are within threshold and evidence artifacts exist.",
        "- The million-sample executor remains locked until the EMX/HFSS gate passes.",
        "",
        "## Physical Inverse-Model Contract",
        "",
        f"- Inputs: `{', '.join(summary.get('physical_model_inputs') or [])}`",
        f"- Geometry output columns: `{len(summary.get('geometry_outputs') or [])}`",
        f"- Acceptance metrics: `{', '.join(gate.get('metrics') or [])}`",
        f"- Target marker: `{gate.get('target_marker_ghz', '')} GHz`, max error `{gate.get('max_percent_error', '')}%`",
        "",
        "## Selected Evidence",
        "",
    ]
    if selected:
        lines.extend(
            [
                f"- Selected variant: `{selected.get('name', '')}`",
                f"- Worst metric: `{selected.get('worst_metric', '')}`",
                f"- Worst percent error: `{selected.get('worst_percent_error', '')}`",
                "",
                f"Artifact index: `{artifact_csv}`",
                "",
            ]
        )
        for row in summary.get("artifact_rows") or []:
            lines.append(f"- {row['exists']}: {row['label']} - `{row['path']}`")
    else:
        lines.extend(
            [
                "- No selected passing V66 variant yet.",
                "- Required HFSS `.s8p` artifacts are still missing, so no EMX/HFSS pass report is generated.",
            ]
        )
    lines.extend(
        [
            "",
            "## Historical HFSS S8P Recompare",
            "",
            f"- Existing full-band HFSS candidates scanned: `{summary['historical_recompare_candidate_count']}`",
            f"- Existing candidates passing <=10% gate: `{summary['historical_recompare_pass_count']}`",
            f"- Best full-band worst error: `{summary['historical_recompare_best_fullband_worst_percent_error']}`%",
            f"- Best 15 GHz worst error: `{summary['historical_recompare_best_target15_worst_percent_error']}`%",
            f"- Best 15 GHz core errors: `{summary['historical_recompare_best_target15_core_percent_errors']}`",
            "- Historical candidates are advisory evidence only; they do not unlock the million-sample campaign.",
            "",
            "## Next Action",
            "",
            "- Run the V66 HFSS visible wrapper in Mars/Windows PowerShell.",
            "- After HFSS exports `.s8p`, rerun the V66 monitor; it will generate EMX/HFSS plots, error tables, and decide whether the million-sample campaign can start.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_artifact_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["key", "label", "required", "exists", "path"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _path_get(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
    }


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return _check_status("PASS" if passed else "FAIL", name, detail)


def _check_status(status: str, name: str, detail: Any) -> dict[str, str]:
    return {"status": status, "name": name, "detail": str(detail)}


if __name__ == "__main__":
    raise SystemExit(main())
