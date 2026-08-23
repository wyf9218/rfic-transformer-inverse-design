#!/usr/bin/env python3
"""Discover real EMX S8P candidates that satisfy the current final-validation gate.

The final EMX-HFSS validation must not be run on a stale layout. This script
scans existing artifacts for real `.s8p` files, verifies the Touchstone contract,
and reuses the selected 8-port power-line layout audit to decide which EMX
samples are eligible for final HFSS comparison.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    roots = [Path(item).expanduser().resolve() for item in args.search_root]
    candidates = sorted({path.resolve() for root in roots for path in root.rglob("*.s8p") if path.is_file()})
    if args.max_candidates is not None:
        candidates = candidates[: max(0, int(args.max_candidates))]

    results = [_evaluate_candidate(path, index, args) for index, path in enumerate(candidates, start=1)]
    final_valid = [item for item in results if item["final_validation_candidate_status"] == "PASS"]
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if final_valid else "FAIL",
        "decision": "FINAL_VALID_LOCAL_EMX_S8P_FOUND" if final_valid else "NO_FINAL_VALID_LOCAL_EMX_S8P_FOUND",
        "search_roots": [str(root) for root in roots],
        "candidate_count": len(candidates),
        "touchstone_contract_pass_count": sum(1 for item in results if item["touchstone_contract_status"] == "PASS"),
        "layout_evidence_found_count": sum(1 for item in results if item["layout_evidence_status"] == "PASS"),
        "layout_audit_pass_count": sum(1 for item in results if item["layout_audit_status"] == "PASS"),
        "final_valid_count": len(final_valid),
        "requirements": {
            "suffix": ".s8p",
            "port_count": int(args.expected_ports),
            "reference_ohm": float(args.expected_reference_ohm),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
            "selected_layout_contract": "current 8-port power-line layout audit",
        },
        "results": results,
    }

    summary_path = out_dir / "final_valid_emx_s8p_candidate_discovery_summary.json"
    report_path = out_dir / "final_valid_emx_s8p_candidate_discovery_report.md"
    csv_path = out_dir / "final_valid_emx_s8p_candidate_discovery.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_csv(csv_path, results)

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"candidate_count={summary['candidate_count']}")
    print(f"final_valid_count={summary['final_valid_count']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"csv={csv_path}")
    return 2 if summary["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--search-root",
        action="append",
        required=True,
        help="Directory to scan recursively. Repeatable.",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-reference-ohm", type=float, default=50.0)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--expected-port-names", default="P001,P002,P003,P004,P005,P006,P007,P008")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--expected-primary-power-line-layer", type=int, default=74)
    parser.add_argument("--expected-primary-power-line-datatype", type=int, default=0)
    parser.add_argument("--expected-secondary-power-line-layer", type=int, default=39)
    parser.add_argument("--expected-secondary-power-line-datatype", type=int, default=60)
    parser.add_argument("--expected-power-line-vertical-length-diameter-ratio", type=float, default=1.5)
    parser.add_argument("--min-power-line-other-coil-clearance-um", type=float, default=1.0e-6)
    parser.add_argument(
        "--expected-power-line-center-tap-topology",
        default="primary_right_secondary_left",
        choices=("primary_right_secondary_left", "primary_left_secondary_right", "any"),
    )
    parser.add_argument("--power-line-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument("--internal-angle-deg", type=float, default=135.0)
    parser.add_argument("--terminal-angle-deg", type=float, default=90.0)
    parser.add_argument("--angle-tolerance-deg", type=float, default=1.0e-3)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _evaluate_candidate(path: Path, index: int, args: argparse.Namespace) -> dict[str, Any]:
    source = _resolve_source_from_touchstone(path)
    layout_path = None if source is None else _find_layout_json(source)
    power_line_path = None if source is None else _find_power_line_geometry_json(source)
    summary_path = None if source is None else _find_summary_json(source)
    layout_evidence_status = "PASS" if layout_path and power_line_path and summary_path else "FAIL"
    touchstone_checks, touchstone_summary = _touchstone_contract_checks(path, args)
    layout_checks: list[dict[str, str]] = []
    layout_audit_status = "FAIL"
    if layout_evidence_status == "PASS":
        layout_checks = _layout_audit_checks(path, index, args)
        layout_audit_status = "FAIL" if any(item["status"] == "FAIL" for item in layout_checks) else "PASS"
    final_status = "PASS" if touchstone_summary["status"] == "PASS" and layout_audit_status == "PASS" else "FAIL"
    return {
        "index": index,
        "touchstone_path": str(path),
        "evaluation": _evaluation_name(path, source),
        "source": "" if source is None else str(source),
        "layout_json_path": "" if layout_path is None else str(layout_path),
        "power_line_8port_geometry_json_path": "" if power_line_path is None else str(power_line_path),
        "summary_json_path": "" if summary_path is None else str(summary_path),
        "touchstone_contract_status": touchstone_summary["status"],
        "layout_evidence_status": layout_evidence_status,
        "layout_audit_status": layout_audit_status,
        "final_validation_candidate_status": final_status,
        "touchstone": touchstone_summary,
        "touchstone_checks": touchstone_checks,
        "layout_checks": layout_checks,
    }


def _touchstone_contract_checks(path: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "status": "FAIL",
        "ports": None,
        "reference_ohm": None,
        "frequency_start_hz": None,
        "frequency_stop_hz": None,
        "frequency_step_hz": None,
        "frequency_points": None,
    }
    try:
        loaded = load_touchstone(path)
        freqs = np.asarray(loaded.freqs_hz, dtype=float)
        ports = int(loaded.num_ports)
        reference = loaded.reference_impedance_ohm
        scalar_reference = _scalar_reference(reference)
        summary.update(
            {
                "ports": ports,
                "reference_ohm": scalar_reference,
                "frequency_start_hz": None if freqs.size == 0 else float(freqs[0]),
                "frequency_stop_hz": None if freqs.size == 0 else float(freqs[-1]),
                "frequency_step_hz": None if freqs.size < 2 else float(np.median(np.diff(freqs))),
                "frequency_points": int(freqs.size),
            }
        )
        tol = float(args.frequency_tolerance_hz)
        expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
        checks.extend(
            [
                _check("suffix .s8p", path.suffix.lower() == ".s8p", path.name),
                _check("expected port count", ports == int(args.expected_ports), f"ports={ports}"),
                _check(
                    "expected reference impedance",
                    scalar_reference is not None
                    and abs(float(scalar_reference) - float(args.expected_reference_ohm)) <= 1.0e-9,
                    f"reference_ohm={scalar_reference}",
                ),
                _check(
                    "expected frequency points",
                    int(freqs.size) == int(args.expected_frequency_points),
                    f"points={freqs.size}",
                ),
            ]
        )
        if freqs.size:
            checks.append(
                _check(
                    "expected frequency start",
                    abs(float(freqs[0]) - float(args.expected_frequency_start_ghz) * 1.0e9) <= tol,
                    f"start_hz={float(freqs[0])}",
                )
            )
            checks.append(
                _check(
                    "expected frequency stop",
                    abs(float(freqs[-1]) - float(args.expected_frequency_stop_ghz) * 1.0e9) <= tol,
                    f"stop_hz={float(freqs[-1])}",
                )
            )
        if freqs.size >= 2:
            diffs = np.diff(freqs)
            checks.append(
                _check(
                    "expected frequency step",
                    abs(float(np.median(diffs)) - expected_step) <= tol
                    and abs(float(np.max(diffs) - np.min(diffs))) <= tol,
                    f"step_hz={float(np.median(diffs))}, span_hz={float(np.max(diffs) - np.min(diffs))}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("touchstone parse", False, f"{type(exc).__name__}: {exc}"))
    summary["status"] = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    return checks, summary


def _layout_audit_checks(path: Path, index: int, args: argparse.Namespace) -> list[dict[str, str]]:
    audit = _load_selected_layout_audit_module()
    audit_args = argparse.Namespace(
        expected_port_names=str(args.expected_port_names),
        expected_pin_purpose=int(args.expected_pin_purpose),
        expected_power_line_bridge_width_um=None,
        expected_primary_power_line_layer=int(args.expected_primary_power_line_layer),
        expected_primary_power_line_datatype=int(args.expected_primary_power_line_datatype),
        expected_secondary_power_line_layer=int(args.expected_secondary_power_line_layer),
        expected_secondary_power_line_datatype=int(args.expected_secondary_power_line_datatype),
        expected_power_line_vertical_length_diameter_ratio=float(args.expected_power_line_vertical_length_diameter_ratio),
        min_power_line_other_coil_clearance_um=float(args.min_power_line_other_coil_clearance_um),
        expected_power_line_center_tap_topology=str(args.expected_power_line_center_tap_topology),
        power_line_tolerance_um=float(args.power_line_tolerance_um),
        internal_angle_deg=float(args.internal_angle_deg),
        terminal_angle_deg=float(args.terminal_angle_deg),
        angle_tolerance_deg=float(args.angle_tolerance_deg),
        require_target_summary_geometry=True,
    )
    result = audit._audit_row(  # noqa: SLF001
        {
            "selection_rank": str(index),
            "evaluation": _evaluation_name(path, _resolve_source_from_touchstone(path)),
            "touchstone_path": str(path),
        },
        index,
        None,
        audit_args,
    )
    return list(result.get("checks") or [])


def _load_selected_layout_audit_module() -> Any:
    script_path = Path(__file__).resolve().with_name("audit_selected_power_line_8port_layout_samples.py")
    spec = importlib.util.spec_from_file_location("selected_layout_audit_for_discovery", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load layout audit module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_source_from_touchstone(path: Path) -> Path | None:
    if path.parent.name == "emx":
        eval_dir = path.parent.parent
        return (eval_dir / "layout").resolve() if (eval_dir / "layout").is_dir() else eval_dir.resolve()
    parts = path.parts
    if "evaluations" in parts:
        idx = parts.index("evaluations")
        if idx + 1 < len(parts):
            eval_dir = Path(*parts[: idx + 2])
            return (eval_dir / "layout").resolve() if (eval_dir / "layout").is_dir() else eval_dir.resolve()
    if _find_layout_json(path.parent) and _find_power_line_geometry_json(path.parent):
        return path.parent.resolve()
    return None


def _find_layout_json(source: Path) -> Path | None:
    candidates = [
        source / "transformer_layout.layout.json",
        source / "layout" / "transformer_layout.layout.json",
        source / "layout.json",
    ]
    for item in candidates:
        if item.is_file():
            return item.resolve()
    matches = sorted(source.glob("layout/*.layout.json")) + sorted(source.glob("*.layout.json"))
    return matches[0].resolve() if matches else None


def _find_power_line_geometry_json(source: Path) -> Path | None:
    candidates = [
        source / "power_line_8port_geometry.json",
        source / "layout" / "power_line_8port_geometry.json",
    ]
    for item in candidates:
        if item.is_file():
            return item.resolve()
    return None


def _find_summary_json(source: Path) -> Path | None:
    candidates = [source / "summary.json", source / "layout" / "summary.json"]
    if source.name == "layout":
        candidates.append(source.parent / "summary.json")
    seen: set[Path] = set()
    for item in candidates:
        resolved = item.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _evaluation_name(path: Path, source: Path | None) -> str:
    if source is not None:
        if source.name == "layout":
            return source.parent.name
        return source.name
    if path.parent.name == "emx":
        return path.parent.parent.name
    return path.stem


def _scalar_reference(reference: Any) -> float | None:
    values = np.asarray(reference, dtype=float)
    if values.ndim == 0:
        return float(values)
    if values.size and np.allclose(values, values[0]):
        return float(values[0])
    return None


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if bool(passed) else "FAIL", "detail": detail}


def _write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "index",
        "evaluation",
        "final_validation_candidate_status",
        "touchstone_contract_status",
        "layout_evidence_status",
        "layout_audit_status",
        "touchstone_path",
        "source",
        "layout_json_path",
        "power_line_8port_geometry_json_path",
        "summary_json_path",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            writer.writerow({field: item.get(field, "") for field in fields})


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Final-Valid EMX S8P Candidate Discovery",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Candidate `.s8p` files scanned: `{summary['candidate_count']}`",
        f"- Touchstone contract PASS: `{summary['touchstone_contract_pass_count']}`",
        f"- Layout evidence found: `{summary['layout_evidence_found_count']}`",
        f"- Current layout audit PASS: `{summary['layout_audit_pass_count']}`",
        f"- Final-valid local EMX candidates: `{summary['final_valid_count']}`",
        "",
        "## Requirements",
        "",
        "| Item | Required |",
        "| --- | --- |",
    ]
    for key, value in summary["requirements"].items():
        lines.append(f"| {key} | `{value}` |")
    lines.extend(["", "## Candidates", "", "| Status | Evaluation | Touchstone | Reason |", "| --- | --- | --- | --- |"])
    for item in summary["results"]:
        reason = _candidate_reason(item)
        lines.append(
            "| {status} | `{evaluation}` | `{path}` | {reason} |".format(
                status=item["final_validation_candidate_status"],
                evaluation=item["evaluation"],
                path=item["touchstone_path"],
                reason=reason,
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A PASS here only means the real EMX `.s8p` and its layout evidence are eligible for HFSS comparison.",
            "It does not prove EMX-HFSS physical-feature agreement; the separate ADS-style comparison gate must still pass <=10%.",
            "",
        ]
    )
    return "\n".join(lines)


def _candidate_reason(item: dict[str, Any]) -> str:
    if item["final_validation_candidate_status"] == "PASS":
        return "Touchstone contract and current layout audit both PASS."
    failed = []
    if item["touchstone_contract_status"] != "PASS":
        failed.append("Touchstone contract FAIL")
    if item["layout_evidence_status"] != "PASS":
        failed.append("layout evidence missing")
    elif item["layout_audit_status"] != "PASS":
        failed_checks = [check["name"] for check in item.get("layout_checks", []) if check.get("status") == "FAIL"]
        failed.append("layout audit FAIL" + (f": {', '.join(failed_checks[:3])}" if failed_checks else ""))
    return "; ".join(failed)


if __name__ == "__main__":
    raise SystemExit(main())
