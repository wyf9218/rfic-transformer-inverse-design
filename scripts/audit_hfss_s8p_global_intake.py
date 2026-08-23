#!/usr/bin/env python3
"""Audit all visible HFSS .s8p files before the EMX/HFSS validation gate.

This is an intake guard, not a pass gate.  Its job is to prevent missed HFSS
exports by scanning broad local roots and clearly separating:

* current V66/V67 gate candidates, which may be consumed by the postrun gate;
* historical/report files, which may be useful diagnostics but cannot unlock
  the million-sample campaign by themselves;
* malformed or wrong-grid Touchstone files.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_s8p_global_intake_audit_current"
DEFAULT_SEARCH_ROOTS = [
    PROJECT_ROOT / "outputs",
    PROJECT_ROOT / "reports",
    PROJECT_ROOT,
    Path.home() / "Downloads",
    Path.home() / "Desktop",
]
DEFAULT_CURRENT_GATE_ROOTS = [
    PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current",
    PROJECT_ROOT / "outputs" / "hfss_v67_material_mesh_calibration_plan_current",
]
DEFAULT_STRICT_RECOMPARE_SUMMARY = (
    PROJECT_ROOT
    / "outputs"
    / "existing_hfss_s8p_strict_recompare_current"
    / "existing_hfss_s8p_strict_recompare_summary.json"
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    search_roots = _resolve_roots(args.search_root or [str(path) for path in DEFAULT_SEARCH_ROOTS])
    current_gate_roots = _resolve_roots(args.current_gate_root or [str(path) for path in DEFAULT_CURRENT_GATE_ROOTS])
    records = _scan_records(search_roots, current_gate_roots, args)
    strict_recompare = _read_json(Path(args.strict_recompare_summary).expanduser().resolve())
    summary = _build_summary(out_dir, search_roots, current_gate_roots, records, strict_recompare, args)

    summary_path = out_dir / "hfss_s8p_global_intake_audit_summary.json"
    report_path = out_dir / "HFSS_S8P_GLOBAL_INTAKE_AUDIT_CN.md"
    csv_path = out_dir / "hfss_s8p_global_intake_records.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_records_csv(csv_path, records)

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"global_s8p_count={summary['counts']['global_s8p_count']}")
    print(f"current_gate_spec_pass_count={summary['counts']['current_gate_spec_pass_count']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"records_csv={csv_path}")
    return 0 if summary["overall_status"] in {"PASS", "WAITING_FOR_CURRENT_GATE_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", action="append")
    parser.add_argument("--current-gate-root", action="append")
    parser.add_argument("--strict-recompare-summary", default=str(DEFAULT_STRICT_RECOMPARE_SUMMARY))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-records-in-summary", type=int, default=50)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _resolve_roots(raw_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        path = Path(raw).expanduser().resolve()
        key = str(path)
        if key not in seen:
            roots.append(path)
            seen.add(key)
    return roots


def _scan_records(search_roots: list[Path], current_gate_roots: list[Path], args: argparse.Namespace) -> list[dict[str, Any]]:
    paths: list[Path] = []
    seen: set[str] = set()
    for root in search_roots:
        if root.is_file() and root.suffix.lower() == ".s8p":
            candidates = [root]
        elif root.is_dir():
            candidates = [path for path in sorted(root.rglob("*.s8p")) if path.is_file()]
        else:
            candidates = []
        for path in candidates:
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            paths.append(path.resolve())
    return [_record_for_path(path, current_gate_roots, args) for path in sorted(paths)]


def _record_for_path(path: Path, current_gate_roots: list[Path], args: argparse.Namespace) -> dict[str, Any]:
    location_class = _location_class(path, current_gate_roots)
    record: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "location_class": location_class,
        "source_kind": _source_kind(path),
        "is_current_gate_candidate": location_class == "current_gate",
        "spec_status": "FAIL",
        "reason": "",
        "ports": None,
        "points": None,
        "start_ghz": None,
        "stop_ghz": None,
        "step_ghz": None,
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds") if path.is_file() else "",
    }
    try:
        data = load_touchstone(path)
    except Exception as exc:
        record["reason"] = f"parse_error:{type(exc).__name__}:{exc}"
        return record

    freqs = data.freqs_hz
    ports = int(data.s_matrix.shape[1]) if data.s_matrix.ndim == 3 else 0
    points = int(len(freqs))
    record["ports"] = ports
    record["points"] = points
    if points:
        record["start_ghz"] = float(freqs[0]) / 1.0e9
        record["stop_ghz"] = float(freqs[-1]) / 1.0e9
    if points >= 2:
        record["step_ghz"] = float(freqs[1] - freqs[0]) / 1.0e9
    failures = _contract_failures(freqs, ports, args)
    record["spec_status"] = "PASS" if not failures else "FAIL"
    record["reason"] = "ok" if not failures else ";".join(failures)
    return record


def _location_class(path: Path, current_gate_roots: list[Path]) -> str:
    if any(_is_relative_to(path, root) for root in current_gate_roots):
        return "current_gate"
    text = str(path)
    if "/reports/" in text or "/outputs/existing_hfss_s8p_strict_recompare_current/" in text:
        return "historical_or_report"
    if "/Downloads/" in text or "/Desktop/" in text:
        return "user_drop_location"
    if "/outputs/" in text:
        return "other_output"
    return "other"


def _contract_failures(freqs: Any, ports: int, args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    expected_start_hz = float(args.expected_frequency_start_ghz) * 1.0e9
    expected_stop_hz = float(args.expected_frequency_stop_ghz) * 1.0e9
    expected_step_hz = float(args.expected_frequency_step_ghz) * 1.0e9
    tolerance_hz = float(args.frequency_tolerance_hz)
    if ports != int(args.expected_ports):
        failures.append(f"ports expected={int(args.expected_ports)} actual={ports}")
    if len(freqs) != int(args.expected_frequency_points):
        failures.append(f"points expected={int(args.expected_frequency_points)} actual={len(freqs)}")
    if len(freqs) == 0:
        failures.append("frequency_grid missing")
        return failures
    if not math.isclose(float(freqs[0]), expected_start_hz, abs_tol=tolerance_hz):
        failures.append(f"start_hz expected={expected_start_hz:g} actual={float(freqs[0]):g}")
    if not math.isclose(float(freqs[-1]), expected_stop_hz, abs_tol=tolerance_hz):
        failures.append(f"stop_hz expected={expected_stop_hz:g} actual={float(freqs[-1]):g}")
    if len(freqs) >= 2:
        steps = [float(freqs[index + 1] - freqs[index]) for index in range(len(freqs) - 1)]
        max_step_error = max(abs(step - expected_step_hz) for step in steps)
        if max_step_error > tolerance_hz:
            failures.append(f"step_hz expected={expected_step_hz:g} max_error={max_step_error:g}")
    return failures


def _build_summary(
    out_dir: Path,
    search_roots: list[Path],
    current_gate_roots: list[Path],
    records: list[dict[str, Any]],
    strict_recompare: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    hfss_records = [record for record in records if record.get("source_kind") != "emx_reference"]
    spec_pass = [record for record in hfss_records if record["spec_status"] == "PASS"]
    current_gate_spec_pass = [record for record in spec_pass if record["is_current_gate_candidate"]]
    historical_spec_pass = [record for record in spec_pass if record["location_class"] == "historical_or_report"]
    user_drop_spec_pass = [record for record in spec_pass if record["location_class"] == "user_drop_location"]
    overall_status, decision = _decision(current_gate_spec_pass, historical_spec_pass, user_drop_spec_pass, records)
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "search_roots": [str(path) for path in search_roots],
        "current_gate_roots": [str(path) for path in current_gate_roots],
        "touchstone_contract": {
            "expected_ports": int(args.expected_ports),
            "expected_frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "expected_frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "frequency_tolerance_hz": float(args.frequency_tolerance_hz),
        },
        "counts": {
            "global_s8p_count": len(records),
            "emx_reference_s8p_count": sum(1 for record in records if record.get("source_kind") == "emx_reference"),
            "hfss_candidate_s8p_count": len(hfss_records),
            "global_spec_pass_count": len(spec_pass),
            "current_gate_s8p_count": sum(1 for record in hfss_records if record["is_current_gate_candidate"]),
            "current_gate_spec_pass_count": len(current_gate_spec_pass),
            "historical_or_report_spec_pass_count": len(historical_spec_pass),
            "user_drop_spec_pass_count": len(user_drop_spec_pass),
        },
        "strict_recompare_evidence": _strict_recompare_evidence(strict_recompare),
        "records_sample": records[: max(0, int(args.max_records_in_summary))],
        "method_notes": [
            "Only current_gate spec-pass files can be considered by the V66/V67 postrun gate.",
            "Historical/report or user-drop files are intake evidence only; they do not unlock the million campaign without a mapped EMX/HFSS postrun comparison.",
            "A Touchstone spec PASS only checks 8 ports and 5-60 GHz/1.0 GHz/56-point grid; it does not prove the EMX/HFSS <=10% physical metric gate.",
        ],
    }


def _decision(
    current_gate_spec_pass: list[dict[str, Any]],
    historical_spec_pass: list[dict[str, Any]],
    user_drop_spec_pass: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> tuple[str, str]:
    if current_gate_spec_pass:
        return "PASS", "CURRENT_GATE_HFSS_S8P_FOUND_RUN_UNIFIED_MONITOR"
    if user_drop_spec_pass:
        return "WAITING_FOR_CURRENT_GATE_HFSS", "USER_DROP_S8P_FOUND_IMPORT_OR_MAP_BEFORE_GATE"
    if historical_spec_pass:
        return "WAITING_FOR_CURRENT_GATE_HFSS", "ONLY_HISTORICAL_OR_REPORT_S8P_FOUND_CURRENT_GATE_STILL_EMPTY"
    if records:
        return "WAITING_FOR_CURRENT_GATE_HFSS", "S8P_FILES_FOUND_BUT_NONE_MATCH_FINAL_TOUCHSTONE_CONTRACT"
    return "WAITING_FOR_CURRENT_GATE_HFSS", "NO_HFSS_S8P_FOUND"


def _strict_recompare_evidence(summary: dict[str, Any]) -> dict[str, Any]:
    best = summary.get("best") if isinstance(summary.get("best"), dict) else {}
    target15_best = summary.get("target15_best") if isinstance(summary.get("target15_best"), dict) else {}
    return {
        "summary_available": bool(summary),
        "candidate_count": summary.get("candidate_count"),
        "pass_count": summary.get("pass_count"),
        "best_worst_percent_error": best.get("worst_percent_error"),
        "target15_best_worst_percent_error": target15_best.get("target15_worst_percent_error", target15_best.get("worst_percent_error")),
    }


def _write_records_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "path",
        "location_class",
        "source_kind",
        "is_current_gate_candidate",
        "spec_status",
        "reason",
        "ports",
        "points",
        "start_ghz",
        "stop_ghz",
        "step_ghz",
        "size_bytes",
        "mtime_utc",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def _render_report(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    strict = summary["strict_recompare_evidence"]
    lines = [
        "# HFSS S8P Global Intake Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Global `.s8p` count: `{counts['global_s8p_count']}`",
        f"- EMX reference `.s8p` count: `{counts['emx_reference_s8p_count']}`",
        f"- HFSS-candidate `.s8p` count: `{counts['hfss_candidate_s8p_count']}`",
        f"- HFSS-candidate spec-pass count: `{counts['global_spec_pass_count']}`",
        f"- Current-gate `.s8p` count: `{counts['current_gate_s8p_count']}`",
        f"- Current-gate spec-pass count: `{counts['current_gate_spec_pass_count']}`",
        f"- Historical/report spec-pass count: `{counts['historical_or_report_spec_pass_count']}`",
        f"- User-drop spec-pass count: `{counts['user_drop_spec_pass_count']}`",
        "",
        "## Strict Recompare Evidence",
        "",
        f"- Candidate count: `{strict.get('candidate_count')}`",
        f"- Pass count: `{strict.get('pass_count')}`",
        f"- Best full-band worst error: `{strict.get('best_worst_percent_error')}`",
        f"- Best 15GHz worst error: `{strict.get('target15_best_worst_percent_error')}`",
        "",
        "## Notes",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["method_notes"])
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _source_kind(path: Path) -> str:
    text = str(path).lower()
    name = path.name.lower()
    if "/emx/" in text or name == "emx.s8p" or "emx_reference" in name:
        return "emx_reference"
    if "hfss" in text:
        return "hfss_export_candidate"
    return "unknown_s8p_candidate"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
