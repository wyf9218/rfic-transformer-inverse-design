#!/usr/bin/env python3
"""Summarize current progress of the next-generation S8P MARS run.

This is a read-only status digest for long remote runs. It does not replace
the stricter dataset, readiness, or EMX/HFSS validation gates; it gathers their
current artifacts into one concise JSON/Markdown/CSV bundle so the run can be
resumed or reviewed without guessing.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


@dataclass(frozen=True)
class Evidence:
    status: str
    requirement: str
    evidence: str
    next_action: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "requirement": self.requirement,
            "evidence": self.evidence,
            "next_action": self.next_action,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    quality_dir = (
        Path(args.quality_dir).expanduser().resolve()
        if args.quality_dir
        else run_dir / "dataset_quality_gates_s8p_physical_feature"
    )
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else run_dir / "next_gen_s8p_mars_run_status"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_csv(run_dir / "dataset_rows.csv")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    manifest = _read_json(run_dir / "dataset_manifest.json")
    parallel_summary = _read_json(run_dir / "parallel_candidate_queue_dataset_summary.json")
    quality_summary = _read_json(quality_dir / "dataset_quality_gates_summary.json")
    selected_samples = _read_csv(quality_dir / "physical_feature_validation_sample_selection" / "physical_feature_validation_samples.csv")
    port_pair_summary = _read_json(
        quality_dir
        / "selected_s8p_port_pair_physical_candidate_audit"
        / "s8p_port_pair_physical_candidate_audit_summary.json"
    )
    layout_summary = _read_json(
        quality_dir
        / "selected_power_line_8port_layout_audit"
        / "selected_power_line_8port_layout_audit_summary.json"
    )
    handoff_summary = _read_json(
        quality_dir
        / "selected_s8p_hfss_handoff"
        / "selected_s8p_hfss_handoff_summary.json"
    )
    aedt_summary = _read_json(
        quality_dir
        / "selected_s8p_hfss_aedt_scripts"
        / "hfss_s8p_aedt_script_packet_summary.json"
    )
    payload_summary = _read_json(
        quality_dir
        / "selected_s8p_hfss_payload_views"
        / "hfss_payload_geometry_render_batch_summary.json"
    )
    inverse_training_manifest = _read_json(
        quality_dir
        / "physical_feature_inverse_training_table"
        / "physical_feature_inverse_training_manifest.json"
    )
    inverse_model_quality_summary = _read_json(
        quality_dir
        / "physical_feature_inverse_model_quality"
        / "physical_feature_inverse_model_quality_summary.json"
    )
    saved_inverse_model_summary = _read_json(
        quality_dir
        / "physical_feature_saved_inverse_model"
        / "physical_feature_inverse_model_training_summary.json"
    )
    postrun_summary = _read_json(
        quality_dir
        / "selected_s8p_hfss_postrun_validation"
        / "s8p_hfss_postrun_validation_summary.json"
    )
    final_report_evidence_summary = _read_json(
        quality_dir
        / "s8p_final_report_evidence_packet"
        / "s8p_final_report_evidence_packet_summary.json"
    )

    touchstones = _touchstone_inventory(run_dir, ok_rows, args)
    evidence = []
    evidence.extend(_parallel_evidence(parallel_summary, args))
    evidence.extend(_dataset_row_evidence(rows, ok_rows, args))
    evidence.extend(_touchstone_evidence(touchstones, args))
    evidence.extend(_touchstone_source_evidence(touchstones, args))
    evidence.extend(_dataset_manifest_evidence(manifest, rows, args))
    evidence.extend(_summary_evidence("S8P dataset quality gates", quality_summary, "Run run_dataset_quality_gates.py after EMX finishes."))
    evidence.extend(_selected_sample_evidence(selected_samples))
    evidence.extend(_port_pair_evidence(port_pair_summary))
    evidence.extend(_summary_evidence("selected sample 8-port layout audit", layout_summary, "Run audit_selected_power_line_8port_layout_samples.py."))
    evidence.extend(_summary_evidence("selected sample HFSS rebuild handoff", handoff_summary, "Run build_selected_s8p_hfss_handoff_packet.py."))
    evidence.extend(_summary_evidence("selected sample HFSS AEDT scripts", aedt_summary, "Run build_s8p_hfss_aedt_scripts_from_handoff.py."))
    evidence.extend(_payload_evidence(payload_summary))
    evidence.extend(_inverse_training_evidence(inverse_training_manifest))
    evidence.extend(_inverse_model_quality_evidence(inverse_model_quality_summary))
    evidence.extend(_saved_inverse_model_evidence(saved_inverse_model_summary))
    evidence.extend(_postrun_evidence(postrun_summary))
    evidence.extend(_final_report_evidence(final_report_evidence_summary))

    stage = _stage(evidence)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": stage["overall_status"],
        "decision": stage["decision"],
        "run_dir": str(run_dir),
        "quality_dir": str(quality_dir),
        "expected": {
            "count": int(args.expected_count),
            "jobs": int(args.expected_jobs),
            "ports": int(args.expected_ports),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
            "max_touchstone_checks": int(args.max_touchstone_checks),
        },
        "rows": {
            "row_count": len(rows),
            "ok_count": len(ok_rows),
        },
        "touchstone_inventory": _touchstone_summary(touchstones),
        "dataset_manifest": _manifest_summary(manifest),
        "status_counts": _status_counts(evidence),
        "evidence": [item.as_dict() for item in evidence],
        "limitations": [
            "This digest is read-only and does not run EMX, HFSS, ADS, or Cadence.",
            "PASS/WARNING here does not replace strict dataset quality gates or EMX/HFSS <=5% Lp/Ls/Q/K/Kw validation.",
            "WAITING means the expected artifact is not present yet; it is not treated as completed evidence.",
        ],
    }
    summary_path = out_dir / "next_gen_s8p_mars_run_status_summary.json"
    report_path = out_dir / "next_gen_s8p_mars_run_status_report.md"
    csv_path = out_dir / "next_gen_s8p_mars_run_status_evidence.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_evidence_csv(csv_path, evidence)

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"evidence_csv={csv_path}")
    for item in evidence:
        print(f"{item.status:8s} {item.requirement}: {item.evidence}")
    return 0 if summary["overall_status"] != "NOT_READY" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--quality-dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--expected-count", type=int, default=500)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-differential-port-pairs", default="1,4:5,6")
    parser.add_argument("--expected-power-line-port-map", default="P001,P002,P003,P004,P005,P006,P007,P008")
    parser.add_argument("--expected-power-line-bridge-width-um", type=float, default=10.0)
    parser.add_argument("--power-line-bridge-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument("--expected-power-line-vertical-length-ratio", type=float, default=1.5)
    parser.add_argument("--power-line-vertical-length-ratio-tolerance", type=float, default=1.0e-12)
    parser.add_argument("--expected-power-line-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--power-line-ground-frame-width-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument(
        "--expected-power-line-ground-frame-policy",
        default="power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
    )
    parser.add_argument("--max-touchstone-checks", type=int, default=500)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}
    return payload if isinstance(payload, dict) else {}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "none", "nan"}


def _resolve(dataset_dir: Path, raw_path: str) -> Path:
    path = Path(str(raw_path)).expanduser()
    return path if path.is_absolute() else dataset_dir / path


def _touchstone_inventory(run_dir: Path, ok_rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, Any]]:
    records = []
    for index, row in enumerate(ok_rows):
        raw_path = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        path = _resolve(run_dir, raw_path) if raw_path else None
        record: dict[str, Any] = {
            "row_index": index,
            "evaluation": row.get("evaluation") or row.get("cache_key") or str(index),
            "path": "" if path is None else str(path),
            "exists": bool(path is not None and path.is_file()),
            "suffix": "" if path is None else path.suffix.lower(),
            "checked": False,
            "num_ports": None,
            "freq_points": None,
            "freq_start_hz": None,
            "freq_stop_hz": None,
            "freq_step_hz": None,
            "source_kind": None,
            "source_status": "NOT_CHECKED",
            "status": "PASS",
            "reason": "",
        }
        reasons = []
        if path is None:
            reasons.append("missing touchstone_path")
        elif not path.is_file():
            reasons.append("file not found")
        elif path.suffix.lower() != ".s8p":
            reasons.append(f"suffix expected .s8p, got {path.suffix}")
        if path is not None and path.is_file():
            source_kind = _source_kind(path)
            record["source_kind"] = source_kind
            if source_kind == "EMX":
                record["source_status"] = "PASS"
            else:
                record["source_status"] = "FAIL"
                reasons.append(f"source kind expected EMX, got {source_kind}")
        if index < int(args.max_touchstone_checks) and path is not None and path.is_file():
            record["checked"] = True
            try:
                loaded = load_touchstone(path)
                freqs = list(float(item) for item in loaded.freqs_hz)
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"load failed: {type(exc).__name__}: {exc}")
            else:
                record["num_ports"] = int(loaded.num_ports)
                record["freq_points"] = len(freqs)
                if freqs:
                    record["freq_start_hz"] = float(freqs[0])
                    record["freq_stop_hz"] = float(freqs[-1])
                if len(freqs) > 1:
                    record["freq_step_hz"] = float(freqs[1] - freqs[0])
                reasons.extend(_touchstone_frequency_reasons(record, args))
        record["status"] = "PASS" if not reasons else "FAIL"
        record["reason"] = "; ".join(reasons)
        records.append(record)
    return records


def _source_kind(path: Path) -> str:
    text_parts = [" ".join(path.parts)]
    try:
        comments: list[str] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if line.startswith("!"):
                    comments.append(line[1:].strip())
                    continue
                if line.startswith("#") or not line:
                    continue
                break
        text_parts.append(" ".join(comments[:100]))
    except OSError:
        pass
    lowered = " ".join(text_parts).lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if "hfss" in lowered or ".aedt" in lowered or "ansys" in lowered:
        return "HFSS"
    if any(token == "emx" or token.startswith("emx") for token in tokens):
        return "EMX"
    if "advanced design system" in lowered or "keysight" in lowered or any(token == "ads" for token in tokens):
        return "ADS"
    return "UNKNOWN"


def _touchstone_frequency_reasons(record: dict[str, Any], args: argparse.Namespace) -> list[str]:
    reasons = []
    tol = float(args.frequency_tolerance_hz)
    expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
    expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
    expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
    if record["num_ports"] != int(args.expected_ports):
        reasons.append(f"ports expected {args.expected_ports}, got {record['num_ports']}")
    if record["freq_points"] != int(args.expected_frequency_points):
        reasons.append(f"frequency points expected {args.expected_frequency_points}, got {record['freq_points']}")
    if record["freq_start_hz"] is not None and abs(float(record["freq_start_hz"]) - expected_start) > tol:
        reasons.append(f"frequency start expected {expected_start}, got {record['freq_start_hz']}")
    if record["freq_stop_hz"] is not None and abs(float(record["freq_stop_hz"]) - expected_stop) > tol:
        reasons.append(f"frequency stop expected {expected_stop}, got {record['freq_stop_hz']}")
    if record["freq_step_hz"] is not None and abs(float(record["freq_step_hz"]) - expected_step) > tol:
        reasons.append(f"frequency step expected {expected_step}, got {record['freq_step_hz']}")
    return reasons


def _parallel_evidence(summary: dict[str, Any], args: argparse.Namespace) -> list[Evidence]:
    requirement = "8-worker EMX candidate queue completed"
    if not summary:
        return [Evidence("WAITING", requirement, "parallel_candidate_queue_dataset_summary.json missing", "Continue or start RUN_EMX=1 on MARS.")]
    status = summary.get("overall_status")
    jobs = summary.get("jobs_requested")
    merged = summary.get("merged_row_count")
    checks = summary.get("checks") or []
    failed = [item for item in checks if not bool(item.get("pass"))]
    passed = status == "PASS" and int(jobs or 0) == int(args.expected_jobs) and int(merged or 0) == int(args.expected_count) and not failed
    return [
        Evidence(
            "PASS" if passed else "FAIL",
            requirement,
            f"overall_status={status}, jobs={jobs}, merged_row_count={merged}, failed_checks={len(failed)}",
            "Fix failed shards or rerun the parallel candidate queue before quality gates.",
        )
    ]


def _dataset_row_evidence(rows: list[dict[str, str]], ok_rows: list[dict[str, str]], args: argparse.Namespace) -> list[Evidence]:
    row_count = len(rows)
    ok_count = len(ok_rows)
    passed = row_count == int(args.expected_count) and ok_count == int(args.expected_count)
    status = "PASS" if passed else ("WAITING" if row_count < int(args.expected_count) else "FAIL")
    return [
        Evidence(
            status,
            "dataset_rows.csv has expected 500 successful rows",
            f"row_count={row_count}, ok_count={ok_count}, expected={args.expected_count}",
            "Wait for all shards or inspect failed rows before accepting the dataset.",
        )
    ]


def _touchstone_evidence(records: list[dict[str, Any]], args: argparse.Namespace) -> list[Evidence]:
    exists_count = sum(1 for item in records if item.get("exists"))
    s8p_count = sum(1 for item in records if item.get("exists") and item.get("suffix") == ".s8p")
    checked = [item for item in records if item.get("checked")]
    failed = [item for item in records if item.get("status") == "FAIL"]
    if len(records) < int(args.expected_count):
        status = "WAITING"
    elif failed:
        status = "FAIL"
    else:
        status = "PASS"
    return [
        Evidence(
            status,
            "all successful rows point to valid .s8p files",
            f"records={len(records)}, files_exist={exists_count}, s8p_files={s8p_count}, checked={len(checked)}, failed={len(failed)}",
            "Run or repair missing EMX outputs; do not proceed to HFSS until .s8p inventory is clean.",
        )
    ]


def _touchstone_source_evidence(records: list[dict[str, Any]], args: argparse.Namespace) -> list[Evidence]:
    source_counts: dict[str, int] = {}
    for item in records:
        key = str(item.get("source_kind") or "MISSING")
        source_counts[key] = source_counts.get(key, 0) + 1
    failed = [
        item
        for item in records
        if item.get("exists") and item.get("source_kind") != "EMX"
    ]
    if len(records) < int(args.expected_count):
        status = "WAITING"
    elif failed:
        status = "FAIL"
    else:
        status = "PASS"
    return [
        Evidence(
            status,
            "all successful rows are traceable to EMX-generated .s8p files",
            f"records={len(records)}, source_counts={source_counts}, failed_source_count={len(failed)}",
            "Reject or quarantine any returned row whose Touchstone path/header is HFSS, ADS, or unknown instead of EMX.",
        )
    ]


def _dataset_manifest_evidence(manifest: dict[str, Any], rows: list[dict[str, str]], args: argparse.Namespace) -> list[Evidence]:
    requirement = "dataset manifest matches approved S8P topology contract"
    if not manifest:
        status = "WAITING" if not rows else "FAIL"
        return [
            Evidence(
                status,
                requirement,
                "dataset_manifest.json missing",
                "Write and audit dataset_manifest.json before treating returned .s8p files as the approved 8-port topology.",
            )
        ]
    checks = _dataset_manifest_contract_checks(manifest, args)
    failed = [item for item in checks if not item[1]]
    status = "PASS" if not failed else "FAIL"
    details = "; ".join(f"{name}={detail}" for name, ok, detail in checks for _ in [ok])
    if failed:
        details += "; failed=" + ",".join(item[0] for item in failed)
    return [
        Evidence(
            status,
            requirement,
            details,
            "Reject this MARS result for final training until the manifest matches the user-approved port, bridge, ground, and frequency contract.",
        )
    ]


def _dataset_manifest_contract_checks(manifest: dict[str, Any], args: argparse.Namespace) -> list[tuple[str, bool, str]]:
    power = manifest.get("power_line_8port") if isinstance(manifest.get("power_line_8port"), dict) else {}
    target = manifest.get("target_frequency") if isinstance(manifest.get("target_frequency"), dict) else {}
    expected_pairs, pair_reason = _parse_pair_text(str(args.expected_differential_port_pairs))
    actual_pairs, actual_pair_reason = _parse_pair_value(manifest.get("differential_port_pairs"))
    expected_map = _split_csv(str(args.expected_power_line_port_map))
    actual_map = [str(item) for item in power.get("port_map", [])] if isinstance(power.get("port_map"), list) else []
    bridge = _to_float(power.get("bridge_width_um"))
    ratio = _to_float(power.get("vertical_length_diameter_ratio"))
    ground_width = _to_float(power.get("ground_frame_width_um"))
    start_hz = _to_float(target.get("start_hz") or target.get("frequency_start_hz"))
    stop_hz = _to_float(target.get("stop_hz") or target.get("frequency_stop_hz"))
    step_hz = _to_float(target.get("step_hz") or target.get("frequency_step_hz"))
    points = _to_int(target.get("points") or target.get("band_points"))
    tol_hz = float(args.frequency_tolerance_hz)
    expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
    expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
    expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
    return [
        (
            "port_mode",
            str(manifest.get("port_mode")) == str(args.expected_port_mode),
            f"expected={args.expected_port_mode} actual={manifest.get('port_mode')}",
        ),
        (
            "differential_port_pairs",
            actual_pairs is not None and expected_pairs is not None and actual_pairs == expected_pairs,
            f"expected={expected_pairs if expected_pairs is not None else pair_reason} actual={actual_pairs if actual_pairs is not None else actual_pair_reason}",
        ),
        ("power_line_8port_enabled", bool(power.get("enabled")), str(power.get("enabled"))),
        (
            "power_line_8port_port_map",
            actual_map == expected_map,
            f"expected={expected_map} actual={actual_map}",
        ),
        (
            "power_line_8port_bridge_width",
            bridge is not None
            and abs(float(bridge) - float(args.expected_power_line_bridge_width_um)) <= float(args.power_line_bridge_width_tolerance_um),
            f"expected={args.expected_power_line_bridge_width_um} actual={power.get('bridge_width_um')}",
        ),
        (
            "power_line_8port_vertical_length_ratio",
            ratio is not None
            and abs(float(ratio) - float(args.expected_power_line_vertical_length_ratio))
            <= float(args.power_line_vertical_length_ratio_tolerance),
            f"expected={args.expected_power_line_vertical_length_ratio} actual={power.get('vertical_length_diameter_ratio')}",
        ),
        (
            "power_line_8port_ground_frame_width",
            ground_width is not None
            and abs(float(ground_width) - float(args.expected_power_line_ground_frame_width_um))
            <= float(args.power_line_ground_frame_width_tolerance_um),
            f"expected={args.expected_power_line_ground_frame_width_um} actual={power.get('ground_frame_width_um')}",
        ),
        (
            "power_line_8port_ground_frame_policy",
            str(power.get("ground_frame_policy")) == str(args.expected_power_line_ground_frame_policy),
            f"expected={args.expected_power_line_ground_frame_policy} actual={power.get('ground_frame_policy')}",
        ),
        (
            "target_frequency_start",
            start_hz is not None and abs(float(start_hz) - expected_start) <= tol_hz,
            f"expected={expected_start} actual={target.get('start_hz') or target.get('frequency_start_hz')}",
        ),
        (
            "target_frequency_stop",
            stop_hz is not None and abs(float(stop_hz) - expected_stop) <= tol_hz,
            f"expected={expected_stop} actual={target.get('stop_hz') or target.get('frequency_stop_hz')}",
        ),
        (
            "target_frequency_step",
            step_hz is not None and abs(float(step_hz) - expected_step) <= tol_hz,
            f"expected={expected_step} actual={target.get('step_hz') or target.get('frequency_step_hz')}",
        ),
        (
            "target_frequency_points",
            points == int(args.expected_frequency_points),
            f"expected={args.expected_frequency_points} actual={target.get('points') or target.get('band_points')}",
        ),
    ]


def _summary_evidence(requirement: str, summary: dict[str, Any], missing_action: str) -> list[Evidence]:
    if not summary:
        return [Evidence("WAITING", requirement, "summary missing", missing_action)]
    status = str(summary.get("overall_status") or "")
    if status == "PASS":
        evidence_status = "PASS"
    elif status in {"", "None"}:
        evidence_status = "WAITING"
    else:
        evidence_status = "FAIL"
    return [
        Evidence(
            evidence_status,
            requirement,
            f"overall_status={status}, decision={summary.get('decision')}",
            "Inspect the referenced report and fix failed checks before continuing.",
        )
    ]


def _selected_sample_evidence(rows: list[dict[str, str]]) -> list[Evidence]:
    return [
        Evidence(
            "PASS" if rows else "WAITING",
            "random physical-feature validation sample selected",
            f"selected_rows={len(rows)}",
            "Run select_physical_feature_validation_samples.py through the quality-gate command.",
        )
    ]


def _port_pair_evidence(summary: dict[str, Any]) -> list[Evidence]:
    requirement = "selected sample S8P port-pair physical diagnostic passed"
    if not summary:
        return [Evidence("WAITING", requirement, "summary missing", "Run audit_s8p_port_pair_physical_candidates.py.")]
    status = str(summary.get("overall_status") or "")
    expected_pass = bool(summary.get("expected_port_pairs_all_pass"))
    if status == "PASS" and expected_pass:
        evidence_status = "PASS"
        next_action = "Use the recorded expected port pair for HFSS/ADS validation."
    elif status == "REVIEW":
        evidence_status = "QUESTION"
        next_action = "Review candidate port-pair curves before accepting the convention."
    else:
        evidence_status = "FAIL"
        next_action = "Fix port-pair convention or select another validation sample."
    return [
        Evidence(
            evidence_status,
            requirement,
            f"overall_status={status}, expected_port_pairs={summary.get('expected_port_pairs')}, expected_all_pass={expected_pass}",
            next_action,
        )
    ]


def _payload_evidence(summary: dict[str, Any]) -> list[Evidence]:
    if not summary:
        return [
            Evidence(
                "WAITING",
                "HFSS payload geometry views rendered",
                "summary missing",
                "Run render_hfss_model_views_from_payload.py before reporting HFSS geometry images.",
            )
        ]
    status = summary.get("overall_status")
    count = int(summary.get("rendered_count") or 0)
    return [
        Evidence(
            "PASS" if status == "PASS" and count > 0 else "FAIL",
            "HFSS payload geometry views rendered",
            f"overall_status={status}, rendered_count={count}",
            "Regenerate payload renders and inspect per-sample geometry summaries.",
        )
    ]


def _postrun_evidence(summary: dict[str, Any]) -> list[Evidence]:
    requirement = "EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed"
    port_requirement = "HFSS build port manifest proves 8-port integration lines"
    if not summary:
        return [
            Evidence(
                "WAITING",
                requirement,
                "summary missing",
                "After HFSS exports .s8p, run run_s8p_hfss_postrun_validation_from_aedt_packet.py.",
            ),
            Evidence(
                "WAITING",
                port_requirement,
                "summary missing",
                "Run HFSS build/export and postrun validation so the build-time port manifest is checked.",
            ),
        ]
    status = summary.get("overall_status")
    if status == "WAITING_FOR_HFSS":
        return [
            Evidence(
                "WAITING",
                requirement,
                f"overall_status={status}, status_counts={summary.get('status_counts')}",
                "HFSS has not exported the matching .s8p yet; run HFSS solve/export, then rerun postrun validation.",
            ),
            Evidence(
                "WAITING",
                port_requirement,
                f"overall_status={status}, status_counts={summary.get('status_counts')}",
                "HFSS .s8p and build-time port manifest are both required before accepting validation.",
            ),
        ]
    port_manifest_ok, port_manifest_detail = _postrun_port_manifest_checks_passed(summary)
    return [
        Evidence(
            "PASS" if status == "PASS" else "FAIL",
            requirement,
            f"overall_status={status}, status_counts={summary.get('status_counts')}",
            "Use final curves only if postrun validation is PASS and <=5% checks pass.",
        ),
        Evidence(
            "PASS" if status == "PASS" and port_manifest_ok else "FAIL",
            port_requirement,
            f"overall_status={status}; {port_manifest_detail}",
            "Do not accept HFSS curves unless the build-time port manifest proves P001-P008 order, P001_G-P008_G grounds, and signal-to-ground integration lines.",
        ),
    ]


def _inverse_training_evidence(summary: dict[str, Any]) -> list[Evidence]:
    requirement = "post-EMX inverse training table uses Lp/Ls/Q/K without Zin"
    if not summary:
        return [
            Evidence(
                "WAITING",
                requirement,
                "summary missing",
                "Run build_physical_feature_inverse_training_table.py after S8P physical-feature extraction.",
            )
        ]
    status = str(summary.get("overall_status") or "")
    contract_ok, contract_detail = _physical_feature_contract_status(summary.get("input_feature_contract") or {})
    row_count = summary.get("training_count")
    passed = status == "PASS" and contract_ok and int(row_count or 0) > 0
    return [
        Evidence(
            "PASS" if passed else "FAIL",
            requirement,
            f"overall_status={status}, training_count={row_count}, {contract_detail}",
            "Regenerate the inverse training table from real .s8p labels with Lp/Ls/Q/K inputs and no Zin columns.",
        )
    ]


def _inverse_model_quality_evidence(summary: dict[str, Any]) -> list[Evidence]:
    requirement = "post-EMX inverse model quality audit passed"
    if not summary:
        return [
            Evidence(
                "WAITING",
                requirement,
                "summary missing",
                "Run audit_physical_feature_inverse_model_quality.py before claiming inverse-design signal.",
            )
        ]
    status = str(summary.get("overall_status") or "")
    contract_ok, contract_detail = _physical_feature_contract_status(summary.get("input_feature_contract") or {})
    quality = summary.get("quality_summary") or {}
    has_metrics = bool(quality.get("per_geometry"))
    passed = status == "PASS" and contract_ok and has_metrics
    return [
        Evidence(
            "PASS" if passed else "FAIL",
            requirement,
            f"overall_status={status}, has_per_geometry_metrics={has_metrics}, {contract_detail}",
            "Fix inverse-model CV failures or collect more EMX labels before using the model for geometry candidates.",
        )
    ]


def _saved_inverse_model_evidence(summary: dict[str, Any]) -> list[Evidence]:
    requirement = "saved Lp/Ls/Q/K-to-geometry inverse model is trained"
    if not summary:
        return [
            Evidence(
                "WAITING",
                requirement,
                "summary missing",
                "Run train_physical_feature_inverse_model.py after inverse training-table and quality checks.",
            )
        ]
    status = str(summary.get("overall_status") or "")
    contract_ok, contract_detail = _physical_feature_contract_status(summary.get("input_feature_contract") or {})
    model_json = _resolve(Path("."), str(summary.get("model_json") or "")) if summary.get("model_json") else None
    model_exists = bool(model_json is not None and model_json.is_file())
    method = str(summary.get("method") or "")
    passed = status == "PASS" and contract_ok and model_exists and method == "standardized_polynomial_ridge_regression"
    return [
        Evidence(
            "PASS" if passed else "FAIL",
            requirement,
            f"overall_status={status}, method={method}, model_exists={model_exists}, {contract_detail}",
            "Train and save a reproducible physical-feature inverse model JSON before claiming physical-feature-to-structure inversion.",
        )
    ]


def _final_report_evidence(summary: dict[str, Any]) -> list[Evidence]:
    requirement = "final report evidence packet passed"
    if not summary:
        return [
            Evidence(
                "WAITING",
                requirement,
                "summary missing",
                "Run build_s8p_final_report_evidence_packet.py after postrun validation so report figures and source files have a manifest.",
            )
        ]
    status = str(summary.get("overall_status") or "")
    decision = summary.get("decision")
    categories = {str(item.get("category")) for item in summary.get("artifacts") or [] if item.get("status") == "PASS"}
    required_categories = {
        "physical_feature_distribution",
        "emx_layout_structure",
        "physical_feature_inverse_training_data",
        "physical_feature_inverse_model_quality",
        "physical_feature_inverse_saved_model",
        "hfss_aedt_rebuild_scripts",
        "hfss_model_structure",
        "hfss_rebuild_port_trace",
        "emx_hfss_touchstone_sources",
        "emx_hfss_physical_curves",
        "emx_hfss_ads_style_report_figures",
    }
    missing = sorted(required_categories - categories)
    passed = status == "PASS" and not missing
    return [
        Evidence(
            "PASS" if passed else ("WAITING" if status.startswith("WAITING") else "FAIL"),
            requirement,
            f"overall_status={status}, decision={decision}, missing_categories={missing}",
            "Do not use the final figures in a report until the evidence packet is PASS and source artifacts are traceable.",
        )
    ]


def _postrun_port_manifest_checks_passed(summary: dict[str, Any]) -> tuple[bool, str]:
    required_names = (
        "HFSS build port manifest exists",
        "HFSS build port manifest schema",
        "HFSS build port manifest has 8 ports",
        "HFSS build port manifest port order is P001-P008",
        "HFSS build port manifest ground names are P001_G-P008_G",
        "HFSS build port manifest records integration lines",
    )
    statuses = {
        str(item.get("name", "")): str(item.get("status", ""))
        for item in summary.get("checks") or []
        if item.get("name")
    }
    missing_or_failed = [name for name in required_names if statuses.get(name) != "PASS"]
    return not missing_or_failed, f"port_manifest_checks_missing_or_failed={missing_or_failed}"


def _physical_feature_contract_status(contract: dict[str, Any]) -> tuple[bool, str]:
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


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    power = manifest.get("power_line_8port") if isinstance(manifest.get("power_line_8port"), dict) else {}
    target = manifest.get("target_frequency") if isinstance(manifest.get("target_frequency"), dict) else {}
    return {
        "present": bool(manifest),
        "port_mode": manifest.get("port_mode"),
        "differential_port_pairs": manifest.get("differential_port_pairs"),
        "power_line_8port": {
            "enabled": power.get("enabled"),
            "bridge_width_um": power.get("bridge_width_um"),
            "vertical_length_diameter_ratio": power.get("vertical_length_diameter_ratio"),
            "port_map": power.get("port_map"),
            "ground_frame_width_um": power.get("ground_frame_width_um"),
            "ground_frame_policy": power.get("ground_frame_policy"),
        },
        "target_frequency": {
            "start_hz": target.get("start_hz") or target.get("frequency_start_hz"),
            "stop_hz": target.get("stop_hz") or target.get("frequency_stop_hz"),
            "step_hz": target.get("step_hz") or target.get("frequency_step_hz"),
            "points": target.get("points") or target.get("band_points"),
        },
    }


def _touchstone_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "record_count": len(records),
        "exists_count": sum(1 for item in records if item.get("exists")),
        "s8p_count": sum(1 for item in records if item.get("suffix") == ".s8p"),
        "checked_count": sum(1 for item in records if item.get("checked")),
        "fail_count": sum(1 for item in records if item.get("status") == "FAIL"),
    }


def _stage(evidence: list[Evidence]) -> dict[str, str]:
    requirements = {item.requirement: item.status for item in evidence}
    completion_requirements = [
        "post-EMX inverse training table uses Lp/Ls/Q/K without Zin",
        "post-EMX inverse model quality audit passed",
        "saved Lp/Ls/Q/K-to-geometry inverse model is trained",
        "EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed",
        "HFSS build port manifest proves 8-port integration lines",
        "final report evidence packet passed",
    ]
    if all(requirements.get(item) == "PASS" for item in completion_requirements):
        return {"overall_status": "PASS", "decision": "READY_TO_REPORT_VERIFIED_NEXT_GEN_S8P_SAMPLE"}
    if any(item.status == "FAIL" for item in evidence):
        return {"overall_status": "NOT_READY", "decision": "FIX_FAILED_S8P_MARS_ARTIFACTS"}
    if any(item.status == "QUESTION" for item in evidence):
        return {"overall_status": "QUESTION", "decision": "REVIEW_PORT_PAIR_OR_SCIENTIFIC_ASSUMPTION"}
    if requirements.get("EMX/HFSS Lp/Ls/Q/K/Kw postrun comparison completed") == "PASS":
        return {"overall_status": "WAITING_FOR_FINAL_REPORT_EVIDENCE", "decision": "BUILD_FINAL_EVIDENCE_PACKET_AND_VERIFY_INVERSE_MODEL"}
    if requirements.get("HFSS payload geometry views rendered") == "PASS":
        return {"overall_status": "WAITING_FOR_HFSS_EXPORT", "decision": "RUN_HFSS_SOLVE_AND_EXPORT_S8P"}
    if requirements.get("selected sample HFSS rebuild handoff") == "PASS":
        return {"overall_status": "WAITING_FOR_HFSS_PACKET", "decision": "GENERATE_OR_RUN_HFSS_AEDT_SCRIPTS"}
    if requirements.get("S8P dataset quality gates") == "PASS":
        return {"overall_status": "EMX_DATASET_READY_FOR_HFSS_HANDOFF", "decision": "BUILD_SELECTED_SAMPLE_HFSS_HANDOFF"}
    if (
        requirements.get("all successful rows point to valid .s8p files") == "PASS"
        and requirements.get("dataset manifest matches approved S8P topology contract") == "PASS"
    ):
        return {"overall_status": "EMX_DATASET_READY_FOR_QUALITY_GATES", "decision": "RUN_S8P_PHYSICAL_FEATURE_QUALITY_GATES"}
    return {"overall_status": "WAITING_FOR_MARS_EMX", "decision": "CONTINUE_OR_START_500_SAMPLE_EMX_RUN"}


def _status_counts(evidence: list[Evidence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.status] = counts.get(item.status, 0) + 1
    return dict(sorted(counts.items()))


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Gen S8P MARS Run Status",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Run dir: `{summary['run_dir']}`",
        f"- Quality dir: `{summary['quality_dir']}`",
        f"- Rows: `{summary['rows']['row_count']}` total, `{summary['rows']['ok_count']}` ok",
        f"- Touchstones: `{summary['touchstone_inventory']['s8p_count']}` .s8p files, `{summary['touchstone_inventory']['fail_count']}` failed records",
        "",
        "## Evidence",
        "",
        "| Status | Requirement | Evidence | Next action |",
        "| --- | --- | --- | --- |",
    ]
    for item in summary["evidence"]:
        lines.append(f"| {_cell(item['status'])} | {_cell(item['requirement'])} | {_cell(item['evidence'])} | {_cell(item['next_action'])} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _write_evidence_csv(path: Path, evidence: list[Evidence]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "requirement", "evidence", "next_action"])
        writer.writeheader()
        writer.writerows(item.as_dict() for item in evidence)


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _split_csv(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _parse_pair_text(text: str) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    try:
        groups = [group.strip() for group in str(text).split(":") if group.strip()]
        if len(groups) != 2:
            return None, f"expected two groups, got {text!r}"
        pairs = []
        for group in groups:
            values = [value.strip() for value in group.split(",") if value.strip()]
            if len(values) != 2:
                return None, f"expected two ports in {group!r}"
            pairs.append((int(values[0]), int(values[1])))
    except ValueError as exc:
        return None, str(exc)
    return _normalize_pair_list(pairs)


def _parse_pair_value(value: Any) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    if not isinstance(value, list) or len(value) != 2:
        return None, f"expected two pairs, got {value!r}"
    pairs: list[tuple[int, int]] = []
    for item in value:
        if isinstance(item, dict):
            raw = [item.get("positive", item.get("pos", item.get("p"))), item.get("negative", item.get("neg", item.get("n")))]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            raw = [item[0], item[1]]
        else:
            return None, f"invalid pair {item!r}"
        try:
            pairs.append((int(raw[0]), int(raw[1])))
        except (TypeError, ValueError) as exc:
            return None, f"invalid pair {item!r}: {exc}"
    return _normalize_pair_list(pairs)


def _normalize_pair_list(pairs: list[tuple[int, int]]) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    flat = [port for pair in pairs for port in pair]
    if len(pairs) != 2 or len(set(flat)) != 4:
        return None, f"pairs must use four distinct ports, got {pairs!r}"
    if min(flat) >= 1:
        pairs = [(a - 1, b - 1) for a, b in pairs]
    if min(port for pair in pairs for port in pair) < 0:
        return None, f"ports cannot be negative after normalization, got {pairs!r}"
    return (pairs[0], pairs[1]), ""


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
