#!/usr/bin/env python3
"""Validate exported HFSS S8P files against EMX from an AEDT script packet.

Use this after `build_s8p_hfss_aedt_scripts_from_handoff.py` has generated the
HFSS build/solve scripts and the Windows/HFSS run has exported `.s8p` files.

This script does not run HFSS or ADS. It discovers the HFSS S8P for each sample,
    audits EMX and HFSS Touchstone files, compares Lp/Ls/Q/K/Kw over 5-60 GHz
    with Qp/Qs diagnostic channels, and writes ADS-style physical-feature plots.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Check:
    sample: str
    evaluation: str
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "sample": self.sample,
            "evaluation": self.evaluation,
            "status": self.status,
            "name": self.name,
            "detail": self.detail,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    packet_summary_path = Path(args.aedt_packet_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    compare_script = Path(args.compare_script or repo_root / "scripts" / "compare_emx_hfss_ads.py").expanduser().resolve()
    audit_script = Path(args.touchstone_audit_script or repo_root / "scripts" / "audit_touchstone_transformer.py").expanduser().resolve()
    plot_script = Path(args.plot_script or repo_root / "scripts" / "plot_emx_hfss_ads_style_metrics.py").expanduser().resolve()
    hfss_map = _read_hfss_map(Path(args.hfss_map_csv).expanduser().resolve()) if args.hfss_map_csv else {}
    hfss_results_dir = Path(args.hfss_results_dir).expanduser().resolve() if args.hfss_results_dir else None
    packet = _read_json(packet_summary_path)

    global_checks = [
        _check("", "", "AEDT packet summary exists", packet_summary_path.is_file(), str(packet_summary_path)),
        _check("", "", "AEDT packet summary passed", packet.get("overall_status") == "PASS", str(packet.get("overall_status"))),
        _check("", "", "compare script exists", compare_script.is_file(), str(compare_script)),
        _check("", "", "touchstone audit script exists", audit_script.is_file(), str(audit_script)),
        _check("", "", "ADS-style plot script exists", plot_script.is_file(), str(plot_script)),
    ]
    records = [
        _validate_sample(sample, index, out_dir, hfss_map, hfss_results_dir, compare_script, audit_script, plot_script, args)
        for index, sample in enumerate(packet.get("sample_results") or [], start=1)
        if sample.get("overall_status") == "PASS"
    ]
    sample_checks = [check for record in records for check in record.pop("_check_objects", [])]
    all_checks = global_checks + sample_checks
    overall_status = _overall_status(records, all_checks, args)
    frequency_grid_mode = _frequency_grid_mode(args)
    decision = _decision(overall_status, frequency_grid_mode)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "aedt_packet_summary": str(packet_summary_path),
        "out_dir": str(out_dir),
        "hfss_results_dir": "" if hfss_results_dir is None else str(hfss_results_dir),
        "hfss_map_csv": "" if not args.hfss_map_csv else str(Path(args.hfss_map_csv).expanduser().resolve()),
        "frequency_grid_mode": frequency_grid_mode,
        "final_acceptance_candidate": frequency_grid_mode == "final_5_60_0p5_111",
        "sample_count": len(records),
        "status_counts": _status_counts(records),
        "records": records,
        "checks": [check.as_dict() for check in all_checks],
        "arguments": {
            "port_pairs_source": "payload differential_port_pairs",
            "expected_ports": 8,
            "compare_start_ghz": float(args.compare_start_ghz),
            "compare_stop_ghz": float(args.compare_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "target_ghz": float(args.target_ghz),
            "max_percent_error": float(args.max_percent_error),
            "ground_unused_ports": bool(args.ground_unused_ports),
            "require_all_pass": bool(args.require_all_pass),
            "skip_ads_style_plots": bool(args.skip_ads_style_plots),
        },
        "limitations": [
            "This script validates already-exported files; it does not run HFSS, EMX, or ADS.",
            "WAITING_FOR_HFSS means the EMX/payload side is present but the matching exported HFSS `.s8p` has not been found yet.",
            "PASS is scoped to selected samples and requires EMX/HFSS Touchstone audits plus Lp/Ls/Q/K/Kw comparison within the configured percent-error gate over the configured frequency grid; Qp/Qs remain diagnostic channels.",
            "A diagnostic frequency grid can identify HFSS root-cause settings but is not final acceptance evidence for the 5-60 GHz engineering dataset.",
        ],
    }
    summary_path = out_dir / "s8p_hfss_postrun_validation_summary.json"
    report_path = out_dir / "s8p_hfss_postrun_validation_report.md"
    checks_csv = out_dir / "s8p_hfss_postrun_validation_checks.csv"
    results_csv = out_dir / "s8p_hfss_postrun_validation_results.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(checks_csv, all_checks)
    _write_results_csv(results_csv, records)

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"results_csv={results_csv}")
    return 2 if overall_status in {"FAIL", "WAITING_FOR_HFSS", "NOT_READY"} and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aedt-packet-summary", required=True, help="hfss_s8p_aedt_script_packet_summary.json")
    parser.add_argument("--hfss-results-dir", help="Directory to recursively search for exported HFSS .s8p files")
    parser.add_argument("--hfss-map-csv", help="Optional CSV with evaluation,hfss_touchstone or evaluation,hfss_path")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-script")
    parser.add_argument("--touchstone-audit-script")
    parser.add_argument("--plot-script")
    parser.add_argument("--compare-start-ghz", type=float, default=5.0)
    parser.add_argument("--compare-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument("--min-target-inductance-nh", type=float, default=0.02)
    parser.add_argument("--min-target-q", type=float, default=0.5)
    parser.add_argument("--min-target-abs-k", type=float, default=0.03)
    parser.add_argument("--min-window-abs-k", type=float, default=0.03)
    parser.add_argument("--max-target-abs-k", type=float, default=1.05)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument(
        "--ground-unused-ports",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Short S8P ports outside the selected differential pair to ground before extracting Lp/Ls/Q/K. "
            "This matches the ADS setup where the extra power-line ports are grounded."
        ),
    )
    parser.add_argument("--require-all-pass", action="store_true")
    parser.add_argument("--skip-ads-style-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _validate_sample(
    sample: dict[str, Any],
    index: int,
    out_dir: Path,
    hfss_map: dict[str, Path],
    hfss_results_dir: Path | None,
    compare_script: Path,
    audit_script: Path,
    plot_script: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    evaluation = str(sample.get("evaluation") or f"sample_{index:02d}")
    rank = str(sample.get("selection_rank") or index)
    payload_path = Path(str(sample.get("payload_json") or "")).expanduser()
    payload = _read_json(payload_path)
    source_files = payload.get("source_files") or {}
    emx_s8p = Path(str(source_files.get("emx_s8p") or "")).expanduser()
    formula_trace = _optional_source_path(source_files.get("ads_formula_trace"))
    port_pairs = _payload_port_pairs(payload)
    hfss_s8p = _resolve_hfss_s8p(evaluation, sample, payload_path, hfss_map, hfss_results_dir, exclude_paths={emx_s8p})
    hfss_port_manifest = _resolve_hfss_port_manifest(sample, payload_path, hfss_s8p)
    sample_out = out_dir / "samples" / f"{index:02d}_{_slug(evaluation)}"
    sample_out.mkdir(parents=True, exist_ok=True)

    checks = [
        _check(rank, evaluation, "payload JSON exists", payload_path.is_file(), str(payload_path)),
        _check(rank, evaluation, "payload schema is S8P HFSS build payload", payload.get("schema") == "rfic_transformer_hfss_s8p_build_payload.v1", str(payload.get("schema"))),
        _check(rank, evaluation, "payload has eight ports", len(payload.get("ports") or []) == 8, f"ports={len(payload.get('ports') or [])}"),
        _check(rank, evaluation, "payload EMX S8P exists", emx_s8p.is_file() and emx_s8p.suffix.lower() == ".s8p", str(emx_s8p)),
        _check(rank, evaluation, "payload ADS/Python formula trace exists", formula_trace is not None and formula_trace.is_file(), "" if formula_trace is None else str(formula_trace)),
        _check(rank, evaluation, "payload differential port pairs resolved", bool(port_pairs), port_pairs),
        _check(rank, evaluation, "HFSS S8P exists", hfss_s8p is not None and hfss_s8p.is_file(), "" if hfss_s8p is None else str(hfss_s8p)),
        _check(rank, evaluation, "HFSS Touchstone suffix is .s8p", hfss_s8p is not None and hfss_s8p.suffix.lower() == ".s8p", "" if hfss_s8p is None else str(hfss_s8p)),
    ]
    checks.extend(_formula_trace_checks(rank, evaluation, formula_trace, port_pairs))
    if hfss_s8p is not None and hfss_s8p.is_file():
        checks.extend(_hfss_port_manifest_checks(rank, evaluation, hfss_port_manifest))
    record: dict[str, Any] = {
        "selection_rank": rank,
        "evaluation": evaluation,
        "status": "WAITING_FOR_HFSS",
        "payload_json": str(payload_path),
        "emx_s8p": str(emx_s8p),
        "hfss_s8p": "" if hfss_s8p is None else str(hfss_s8p),
        "hfss_port_manifest": "" if hfss_port_manifest is None else str(hfss_port_manifest),
        "ads_formula_trace": "" if formula_trace is None else str(formula_trace),
        "port_pairs": port_pairs,
        "sample_out_dir": str(sample_out),
        "emx_audit_summary": "",
        "hfss_audit_summary": "",
        "compare_summary": "",
        "target_marker_csv": "",
        "ads_style_plot_summary": "",
        "worst_metric": "",
        "worst_percent_error": None,
    }
    if any(check.status == "FAIL" for check in checks):
        record["_check_objects"] = checks
        non_hfss_failures = [
            check
            for check in checks
            if check.status == "FAIL" and check.name not in {"HFSS S8P exists", "HFSS Touchstone suffix is .s8p"}
        ]
        record["status"] = "FAIL" if non_hfss_failures else "WAITING_FOR_HFSS"
        return record

    emx_audit = sample_out / "emx_touchstone_audit"
    hfss_audit = sample_out / "hfss_touchstone_audit"
    compare_out = sample_out / "emx_vs_hfss_compare"
    plot_out = sample_out / "ads_style_metric_plots"
    command_records = []
    command_records.append(_run_command(_touchstone_audit_command(audit_script, emx_s8p, emx_audit, port_pairs, args, expected_source_kind="ANY")))
    command_records.append(_run_command(_touchstone_audit_command(audit_script, hfss_s8p, hfss_audit, port_pairs, args, expected_source_kind="ANY")))
    command_records.append(_run_command(_compare_command(compare_script, emx_s8p, hfss_s8p, compare_out, port_pairs, args)))
    if not args.skip_ads_style_plots:
        command_records.append(_run_command(_plot_command(plot_script, emx_s8p, hfss_s8p, plot_out, port_pairs, args)))
    checks.extend(_command_checks(rank, evaluation, command_records))

    emx_summary = _read_json(emx_audit / "touchstone_transformer_audit_summary.json")
    hfss_summary = _read_json(hfss_audit / "touchstone_transformer_audit_summary.json")
    compare_summary = _read_json(compare_out / "emx_hfss_ads_comparison_summary.json")
    plot_summary = _read_json(plot_out / "ads_style_metric_plot_summary.json") if not args.skip_ads_style_plots else {}
    checks.extend(_audit_summary_checks(rank, evaluation, "EMX", emx_summary))
    checks.extend(_audit_summary_checks(rank, evaluation, "HFSS", hfss_summary))
    checks.extend(_compare_summary_checks(rank, evaluation, compare_summary, args))
    checks.extend(_compare_frequency_contract_checks(rank, evaluation, compare_summary, args))
    checks.extend(_target_marker_checks(rank, evaluation, compare_summary, compare_out, args))
    if not args.skip_ads_style_plots:
        checks.extend(_plot_summary_checks(rank, evaluation, plot_summary))
    worst_metric, worst_error = _worst_metric(compare_summary)
    status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    record.update(
        {
            "status": status,
            "emx_audit_summary": str(emx_audit / "touchstone_transformer_audit_summary.json"),
            "hfss_audit_summary": str(hfss_audit / "touchstone_transformer_audit_summary.json"),
            "compare_summary": str(compare_out / "emx_hfss_ads_comparison_summary.json"),
            "target_marker_csv": str(compare_out / "emx_hfss_ads_target_marker_metrics.csv"),
            "ads_style_plot_summary": "" if args.skip_ads_style_plots else str(plot_out / "ads_style_metric_plot_summary.json"),
            "worst_metric": worst_metric,
            "worst_percent_error": worst_error,
            "command_records": command_records,
        }
    )
    record["_check_objects"] = checks
    return record


def _touchstone_audit_command(
    audit_script: Path,
    touchstone: Path,
    out_dir: Path,
    port_pairs: str,
    args: argparse.Namespace,
    *,
    expected_source_kind: str,
) -> list[str]:
    command = [
        sys.executable,
        str(audit_script),
        str(touchstone),
        "--out-dir",
        str(out_dir),
        "--expected-ports",
        "8",
        "--port-pairs",
        port_pairs,
        "--expected-frequency-start-ghz",
        f"{float(args.compare_start_ghz):g}",
        "--expected-frequency-stop-ghz",
        f"{float(args.compare_stop_ghz):g}",
        "--expected-frequency-step-ghz",
        f"{float(args.expected_frequency_step_ghz):g}",
        "--expected-frequency-points",
        str(int(args.expected_frequency_points)),
        "--required-sweep-start-ghz",
        f"{float(args.compare_start_ghz):g}",
        "--required-sweep-stop-ghz",
        f"{float(args.compare_stop_ghz):g}",
        "--target-frequency-ghz",
        f"{float(args.target_ghz):g}",
        "--target-frequency-tolerance-ghz",
        f"{float(args.target_frequency_tolerance_ghz):g}",
        "--min-target-inductance-nh",
        f"{float(args.min_target_inductance_nh):g}",
        "--min-target-q",
        f"{float(args.min_target_q):g}",
        "--min-target-abs-k",
        f"{float(args.min_target_abs_k):g}",
        "--min-window-abs-k",
        f"{float(args.min_window_abs_k):g}",
        "--max-target-abs-k",
        f"{float(args.max_target_abs_k):g}",
        "--positive-window-start-ghz",
        f"{float(args.compare_start_ghz):g}",
        "--positive-window-stop-ghz",
        f"{min(float(args.compare_stop_ghz), 30.0):g}",
        "--expected-source-kind",
        expected_source_kind,
    ]
    if int(args.expected_frequency_points) >= 3:
        command.extend(
            [
                "--shape-window-start-ghz",
                f"{float(args.compare_start_ghz):g}",
                "--shape-window-stop-ghz",
                f"{min(float(args.compare_stop_ghz), 30.0):g}",
            ]
        )
    if args.ground_unused_ports:
        command.append("--ground-unused-ports")
    command.extend(["--plot", "--no-fail-exit"])
    return command


def _compare_command(compare_script: Path, emx: Path, hfss: Path, out_dir: Path, port_pairs: str, args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(compare_script),
        "--emx",
        str(emx),
        "--hfss",
        str(hfss),
        "--out-dir",
        str(out_dir),
        "--emx-port-pairs",
        port_pairs,
        "--hfss-port-pairs",
        port_pairs,
        "--compare-start-ghz",
        f"{float(args.compare_start_ghz):g}",
        "--compare-stop-ghz",
        f"{float(args.compare_stop_ghz):g}",
        "--min-frequency-points",
        str(int(args.expected_frequency_points)),
        "--expected-frequency-step-ghz",
        f"{float(args.expected_frequency_step_ghz):g}",
        "--expected-frequency-points",
        str(int(args.expected_frequency_points)),
        "--frequency-tolerance-hz",
        f"{float(args.frequency_tolerance_hz):g}",
        "--require-matching-frequency-grid",
        "--require-touchstone-suffix",
        ".s8p",
        "--expected-port-count",
        "8",
        "--expected-reference-ohm",
        "50",
        "--target-ghz",
        f"{float(args.target_ghz):g}",
        "--target-frequency-tolerance-ghz",
        f"{float(args.target_frequency_tolerance_ghz):g}",
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--plot",
        "--no-fail-exit",
    ]
    if args.ground_unused_ports:
        command.insert(command.index("--max-percent-error"), "--ground-unused-ports")
    return command


def _plot_command(plot_script: Path, emx: Path, hfss: Path, out_dir: Path, port_pairs: str, args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(plot_script),
        "--emx-touchstone",
        str(emx),
        "--hfss-touchstone",
        str(hfss),
        "--out-dir",
        str(out_dir),
        "--port-pairs",
        port_pairs,
        "--hfss-start-ghz",
        f"{float(args.compare_start_ghz):g}",
        "--hfss-stop-ghz",
        f"{float(args.compare_stop_ghz):g}",
        "--target-ghz",
        f"{float(args.target_ghz):g}",
        "--core-only",
    ]
    if args.ground_unused_ports:
        command.append("--ground-unused-ports")
    return command


def _run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-2000:],
    }


def _read_hfss_map(path: Path) -> dict[str, Path]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    out = {}
    for row in rows:
        evaluation = row.get("evaluation") or row.get("sample_id")
        raw = row.get("hfss_touchstone") or row.get("hfss_path") or row.get("hfss_s8p")
        if evaluation and raw:
            out[str(evaluation)] = Path(raw).expanduser().resolve()
    return out


def _resolve_hfss_s8p(
    evaluation: str,
    sample: dict[str, Any],
    payload_path: Path,
    hfss_map: dict[str, Path],
    hfss_results_dir: Path | None,
    *,
    exclude_paths: set[Path] | None = None,
) -> Path | None:
    mapped = hfss_map.get(evaluation)
    if mapped is not None:
        return mapped
    excluded = {path.expanduser().resolve() for path in (exclude_paths or set()) if str(path)}
    roots: list[Path] = []
    if hfss_results_dir is not None:
        roots.append(hfss_results_dir)
    if payload_path.is_file():
        roots.extend([payload_path.parent / "hfss_solve_export_results", payload_path.parent])
    candidates = []
    tokens = {_slug(evaluation).lower(), evaluation.lower()}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.s8p"):
            resolved = path.resolve()
            if resolved in excluded:
                continue
            name = path.name.lower()
            score = 0
            if any(token and token in name for token in tokens):
                score += 10
            if "hfss" in name or "solve" in name:
                score += 3
            candidates.append((score, resolved))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], str(item[1])))
    return candidates[0][1]


def _resolve_hfss_port_manifest(sample: dict[str, Any], payload_path: Path, hfss_s8p: Path | None) -> Path | None:
    explicit = sample.get("hfss_port_manifest") or sample.get("port_manifest") or sample.get("build_port_manifest")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()
    roots: list[Path] = []
    if payload_path.is_file():
        roots.append(payload_path.parent)
    script_dir = sample.get("script_dir")
    if script_dir:
        roots.append(Path(str(script_dir)).expanduser())
    if hfss_s8p is not None:
        roots.append(hfss_s8p.expanduser().parent)
    seen: set[Path] = set()
    for root in roots:
        resolved_root = root.resolve()
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        direct = resolved_root / "hfss_s8p_build_port_manifest.json"
        if direct.is_file():
            return direct
        for path in resolved_root.rglob("hfss_s8p_build_port_manifest.json") if resolved_root.exists() else []:
            return path.resolve()
    return payload_path.parent / "hfss_s8p_build_port_manifest.json" if payload_path else None


def _hfss_port_manifest_checks(sample: str, evaluation: str, manifest_path: Path | None) -> list[Check]:
    checks = [
        _check(
            sample,
            evaluation,
            "HFSS build port manifest exists",
            manifest_path is not None and manifest_path.is_file(),
            "" if manifest_path is None else str(manifest_path),
        )
    ]
    if manifest_path is None or not manifest_path.is_file():
        return checks
    manifest = _read_json(manifest_path)
    ports = manifest.get("ports") if isinstance(manifest.get("ports"), list) else []
    expected_order = [f"P{idx:03d}" for idx in range(1, 9)]
    actual_order = [str(port.get("port_name", "")) for port in ports if isinstance(port, dict)]
    actual_ground = [str(port.get("ground_name", "")) for port in ports if isinstance(port, dict)]
    expected_ground = [f"{name}_G" for name in expected_order]
    use_terminal_reference = _manifest_env_bool(manifest, "HFSS_USE_PYAEDT_REFERENCE_PORT")
    expected_reference_count = _manifest_env_int(manifest, "HFSS_PORT_REFERENCE_EXPECTED_COUNT")
    checks.extend(
        [
            _check(
                sample,
                evaluation,
                "HFSS build port manifest schema",
                manifest.get("schema") == "rfic_transformer_hfss_s8p_build_port_manifest.v1",
                str(manifest.get("schema")),
            ),
            _check(sample, evaluation, "HFSS build port manifest has 8 ports", len(ports) == 8, f"ports={len(ports)}"),
            _check(
                sample,
                evaluation,
                "HFSS build port manifest port order is P001-P008",
                actual_order == expected_order,
                f"expected={expected_order}, actual={actual_order}",
            ),
            _check(
                sample,
                evaluation,
                "HFSS build port manifest ground names are P001_G-P008_G",
                actual_ground == expected_ground,
                f"expected={expected_ground}, actual={actual_ground}",
            ),
            _check(
                sample,
                evaluation,
                "HFSS build port manifest records integration lines",
                _port_manifest_lines_match_ports(ports),
                _port_manifest_line_error_detail(ports),
            ),
        ]
    )
    if use_terminal_reference is True:
        checks.append(
            _check(
                sample,
                evaluation,
                "HFSS build used terminal-reference port assignment",
                all(str(port.get("assignment_mode", "")) == "terminal_reference" for port in ports if isinstance(port, dict))
                and len(ports) == 8,
                json.dumps(
                    {
                        str(port.get("port_name", "")): str(port.get("assignment_mode", ""))
                        for port in ports
                        if isinstance(port, dict)
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
    if expected_reference_count is not None and expected_reference_count > 0:
        checks.append(
            _check(
                sample,
                evaluation,
                f"HFSS build port manifest has {expected_reference_count} local M5 reference per port",
                all(
                    isinstance(port.get("reference_conductors"), list)
                    and len([item for item in port.get("reference_conductors", []) if item]) == expected_reference_count
                    for port in ports
                    if isinstance(port, dict)
                )
                and len(ports) == 8,
                json.dumps(
                    {
                        str(port.get("port_name", "")): port.get("reference_conductors", [])
                        for port in ports
                        if isinstance(port, dict)
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
    return checks


def _manifest_env_value(manifest: dict[str, Any], name: str) -> str | None:
    effective = manifest.get("effective_hfss_env") if isinstance(manifest.get("effective_hfss_env"), dict) else {}
    item = effective.get(name) if isinstance(effective, dict) else None
    if isinstance(item, dict) and item.get("value") is not None:
        return str(item.get("value"))
    defaults = manifest.get("calibration_env_defaults") if isinstance(manifest.get("calibration_env_defaults"), dict) else {}
    if defaults.get(name) is not None:
        return str(defaults.get(name))
    if manifest.get(name) is not None:
        return str(manifest.get(name))
    return None


def _manifest_env_bool(manifest: dict[str, Any], name: str) -> bool | None:
    value = _manifest_env_value(manifest, name)
    if value is None:
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _manifest_env_int(manifest: dict[str, Any], name: str) -> int | None:
    value = _manifest_env_value(manifest, name)
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _port_manifest_lines_match_ports(ports: list[Any]) -> bool:
    return not _port_manifest_line_errors(ports)


def _port_manifest_line_error_detail(ports: list[Any]) -> str:
    return json.dumps(_port_manifest_line_errors(ports), ensure_ascii=False, sort_keys=True)


def _port_manifest_line_errors(ports: list[Any]) -> list[dict[str, Any]]:
    errors = []
    for port in ports:
        if not isinstance(port, dict):
            errors.append({"port": "", "error": "port record is not an object"})
            continue
        port_name = str(port.get("port_name", ""))
        signal = _xyz(port.get("signal_xyz_um"))
        ground = _xyz(port.get("ground_xyz_um"))
        line = port.get("integration_line") if isinstance(port.get("integration_line"), dict) else {}
        start = _xyz(line.get("start_xyz_um"))
        end = _xyz(line.get("end_xyz_um"))
        missing = []
        for label, value in (("signal_xyz_um", signal), ("ground_xyz_um", ground), ("start_xyz_um", start), ("end_xyz_um", end)):
            if value is None:
                missing.append(label)
        if missing:
            errors.append({"port": port_name, "error": "missing xyz", "fields": missing})
            continue
        if not _same_xyz(signal, start):
            errors.append({"port": port_name, "error": "integration start does not match signal xyz", "signal": signal, "start": start})
        if not _same_xyz(ground, end):
            errors.append({"port": port_name, "error": "integration end does not match ground xyz", "ground": ground, "end": end})
    return errors


def _xyz(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _same_xyz(left: list[float] | None, right: list[float] | None, *, tol: float = 1.0e-9) -> bool:
    if left is None or right is None:
        return False
    return len(left) == len(right) == 3 and all(abs(float(a) - float(b)) <= tol for a, b in zip(left, right))


def _payload_port_pairs(payload: dict[str, Any]) -> str:
    pairs = payload.get("differential_port_pairs") or []
    if isinstance(pairs, str):
        return pairs
    pieces = []
    for item in pairs:
        if not isinstance(item, dict):
            continue
        plus = item.get("plus_port_index")
        minus = item.get("minus_port_index")
        if plus is None or minus is None:
            continue
        pieces.append(f"{int(plus)},{int(minus)}")
    return ":".join(pieces)


def _formula_trace_checks(sample: str, evaluation: str, formula_trace: Path | None, port_pairs: str) -> list[Check]:
    if formula_trace is None or not formula_trace.is_file():
        return []
    try:
        text = formula_trace.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - exact failure is recorded as evidence.
        return [_check(sample, evaluation, "formula trace reads", False, f"{type(exc).__name__}: {exc}")]
    compact = _compact_formula_text(text)
    required_tokens = {
        "port_pair_syntax": port_pairs,
        "differential_transform": "Z_diff=transpose(T)*Z_single*T",
        "lp_formula": "Lp=imag(Zdiff[1,1])/omega",
        "ls_formula": "Ls=imag(Zdiff[2,2])/omega",
        "m_formula": "M=imag(Zdiff[2,1])/omega",
        "qp_formula": "Qp=imag(Zdiff[1,1])/real(Zdiff[1,1])",
        "qs_formula": "Qs=imag(Zdiff[2,2])/real(Zdiff[2,2])",
        "q_formula": "Q=min(Qp,Qs)",
        "k_formula": "K=M/sqrt(abs(Lp*Ls))",
        "kw_alias": "Kw=K",
    }
    checks = [
        _check(sample, evaluation, "formula trace reads", True, str(formula_trace)),
    ]
    for name, token in required_tokens.items():
        checks.append(
            _check(
                sample,
                evaluation,
                f"formula trace contains {name}",
                _compact_formula_text(token) in compact,
                token,
            )
        )
    return checks


def _compact_formula_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).replace("`", "")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _optional_source_path(raw: Any) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def _command_checks(sample: str, evaluation: str, records: list[dict[str, Any]]) -> list[Check]:
    checks = []
    for index, record in enumerate(records, start=1):
        checks.append(
            _check(
                sample,
                evaluation,
                f"subcommand {index} returned",
                int(record["returncode"]) == 0,
                f"returncode={record['returncode']}; stderr_tail={record.get('stderr_tail', '')}",
            )
        )
    return checks


def _audit_summary_checks(sample: str, evaluation: str, label: str, summary: dict[str, Any]) -> list[Check]:
    checks = [_check(sample, evaluation, f"{label} Touchstone audit summary exists", bool(summary), str(summary.get("overall_status", "")))]
    checks.append(_check(sample, evaluation, f"{label} Touchstone audit PASS", summary.get("overall_status") == "PASS", str(summary.get("overall_status"))))
    return checks


def _compare_summary_checks(sample: str, evaluation: str, summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks = [_check(sample, evaluation, "EMX-vs-HFSS compare summary exists", bool(summary), str(summary.get("overall_status", "")))]
    checks.append(_check(sample, evaluation, "EMX-vs-HFSS compare PASS", summary.get("overall_status") == "PASS", str(summary.get("overall_status"))))
    for metric in ("lp_nh", "ls_nh", "q", "k", "kw", "qp", "qs"):
        item = (summary.get("metrics") or {}).get(metric) or {}
        error = item.get("max_percent_error")
        passed = item.get("status") == "PASS" and isinstance(error, (int, float)) and float(error) <= float(args.max_percent_error)
        checks.append(_check(sample, evaluation, f"{metric} <= {args.max_percent_error:g}% max error", passed, str(error)))
    return checks


def _compare_frequency_contract_checks(sample: str, evaluation: str, summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    window = summary.get("frequency_window_hz") if isinstance(summary.get("frequency_window_hz"), dict) else {}
    window_min = _number_or_none(window.get("min"))
    window_max = _number_or_none(window.get("max"))
    window_count = _number_or_none(window.get("count"))
    start_hz = float(args.compare_start_ghz) * 1.0e9
    stop_hz = float(args.compare_stop_ghz) * 1.0e9
    tol_hz = float(args.frequency_tolerance_hz)
    checks = [
        _check(
            sample,
            evaluation,
            f"comparison frequency window matches requested {float(args.compare_start_ghz):g}-{float(args.compare_stop_ghz):g} GHz",
            window_min is not None
            and window_max is not None
            and abs(float(window_min) - start_hz) <= tol_hz
            and abs(float(window_max) - stop_hz) <= tol_hz,
            f"expected_hz={start_hz}-{stop_hz}, actual_hz={window_min}-{window_max}",
        ),
        _check(
            sample,
            evaluation,
            f"comparison frequency point count is {int(args.expected_frequency_points)}",
            window_count is not None and int(window_count) == int(args.expected_frequency_points),
            f"expected={int(args.expected_frequency_points)}, actual={window_count}",
        ),
    ]
    grid_checks = summary.get("frequency_grid_checks") if isinstance(summary.get("frequency_grid_checks"), dict) else {}
    for source_name, check_name in (
        ("ADS no-extrapolation coverage", "comparison window has no EMX/HFSS extrapolation"),
        ("expected frequency points", "comparison grid reports expected point count"),
        ("expected frequency step", f"comparison grid step is {float(args.expected_frequency_step_ghz):g} GHz"),
        ("expected window start point", f"comparison grid starts at {float(args.compare_start_ghz):g} GHz"),
        ("expected window stop point", f"comparison grid stops at {float(args.compare_stop_ghz):g} GHz"),
        ("matching HFSS/ADS frequency grid", "EMX and HFSS frequency grids match"),
    ):
        item = grid_checks.get(source_name) if isinstance(grid_checks.get(source_name), dict) else {}
        checks.append(
            _check(
                sample,
                evaluation,
                check_name,
                item.get("status") == "PASS",
                str(item.get("detail", "missing frequency_grid_checks entry")),
            )
        )
    return checks


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _target_marker_checks(sample: str, evaluation: str, summary: dict[str, Any], compare_out: Path, args: argparse.Namespace) -> list[Check]:
    marker = summary.get("target_marker") or {}
    marker_csv = compare_out / "emx_hfss_ads_target_marker_metrics.csv"
    checks = [
        _check(sample, evaluation, "target-frequency marker summary exists", bool(marker), str(args.target_ghz)),
        _check(sample, evaluation, "target-frequency marker CSV exists", marker_csv.is_file(), str(marker_csv)),
        _check(sample, evaluation, "target-frequency marker PASS", marker.get("status") == "PASS", str(marker.get("status"))),
    ]
    nearest = marker.get("nearest_frequency_ghz")
    freq_error = marker.get("frequency_error_ghz")
    checks.append(
        _check(
            sample,
            evaluation,
            "target-frequency marker is at requested GHz",
            isinstance(nearest, (int, float))
            and isinstance(freq_error, (int, float))
            and abs(float(nearest) - float(args.target_ghz)) <= float(args.target_frequency_tolerance_ghz),
            f"target={args.target_ghz}, nearest={nearest}, error={freq_error}",
        )
    )
    for metric in ("lp_nh", "ls_nh", "q", "k", "kw"):
        item = (marker.get("metrics") or {}).get(metric) or {}
        error = item.get("percent_error")
        checks.append(
            _check(
                sample,
                evaluation,
                f"{metric} target-marker <= {args.max_percent_error:g}% error",
                item.get("status") == "PASS"
                and isinstance(error, (int, float))
                and float(error) <= float(args.max_percent_error),
                str(error),
            )
        )
    return checks


def _plot_summary_checks(sample: str, evaluation: str, summary: dict[str, Any]) -> list[Check]:
    artifacts = summary.get("artifact_paths") or {}
    window_artifacts = summary.get("window_named_artifact_paths") or {}
    metric_errors = summary.get("metric_max_percent_errors_common_window") or {}
    return [
        _check(sample, evaluation, "ADS-style plot summary exists", bool(summary), str(summary.get("decision", ""))),
        _check(sample, evaluation, "ADS-style EMX physical plot exists", Path(str(artifacts.get("emx_common_plot", ""))).is_file(), str(artifacts.get("emx_common_plot", ""))),
        _check(sample, evaluation, "ADS-style HFSS physical plot exists", Path(str(artifacts.get("hfss_common_plot", ""))).is_file(), str(artifacts.get("hfss_common_plot", ""))),
        _check(sample, evaluation, "ADS-style EMX/HFSS overlay plot exists", Path(str(artifacts.get("overlay_common_plot", ""))).is_file(), str(artifacts.get("overlay_common_plot", ""))),
        _check(sample, evaluation, "ADS-style metric CSV exists", Path(str(artifacts.get("metric_csv", ""))).is_file(), str(artifacts.get("metric_csv", ""))),
        _check(sample, evaluation, "window-named EMX physical plot exists", Path(str(window_artifacts.get("emx_common_plot", ""))).is_file(), str(window_artifacts.get("emx_common_plot", ""))),
        _check(sample, evaluation, "window-named HFSS physical plot exists", Path(str(window_artifacts.get("hfss_common_plot", ""))).is_file(), str(window_artifacts.get("hfss_common_plot", ""))),
        _check(sample, evaluation, "window-named EMX/HFSS overlay plot exists", Path(str(window_artifacts.get("overlay_common_plot", ""))).is_file(), str(window_artifacts.get("overlay_common_plot", ""))),
        _check(sample, evaluation, "ADS-style EMX plot source is 8-port", int(summary.get("emx_n_ports") or 0) == 8, str(summary.get("emx_n_ports"))),
        _check(sample, evaluation, "ADS-style HFSS plot source is 8-port", int(summary.get("hfss_n_ports") or 0) == 8, str(summary.get("hfss_n_ports"))),
        _check(sample, evaluation, "ADS-style EMX/HFSS plot port pairs match", str(summary.get("emx_port_pairs")) == str(summary.get("hfss_port_pairs")), f"emx={summary.get('emx_port_pairs')}, hfss={summary.get('hfss_port_pairs')}"),
        _check(sample, evaluation, "ADS-style scalar Q error tracked", isinstance(metric_errors.get("q"), (int, float)), str(metric_errors.get("q"))),
        _check(sample, evaluation, "ADS-style Kw/K error tracked", isinstance(metric_errors.get("kw"), (int, float)) and isinstance(metric_errors.get("k"), (int, float)), f"k={metric_errors.get('k')}, kw={metric_errors.get('kw')}"),
    ]


def _worst_metric(summary: dict[str, Any]) -> tuple[str, float | None]:
    worst_metric = ""
    worst_error: float | None = None
    for metric, item in (summary.get("metrics") or {}).items():
        error = item.get("max_percent_error")
        if isinstance(error, (int, float)) and (worst_error is None or float(error) > worst_error):
            worst_metric = str(metric)
            worst_error = float(error)
    return worst_metric, worst_error


def _overall_status(records: list[dict[str, Any]], checks: list[Check], args: argparse.Namespace) -> str:
    if not records:
        return "NOT_READY"
    if any(check.status == "FAIL" for check in checks if not check.name.startswith("HFSS S8P exists")):
        # A missing HFSS file is treated separately below.
        non_hfss_failures = [
            check
            for check in checks
            if check.status == "FAIL" and check.name not in {"HFSS S8P exists", "HFSS Touchstone suffix is .s8p"}
        ]
        if non_hfss_failures:
            return "FAIL"
    statuses = {record.get("status") for record in records}
    if "FAIL" in statuses:
        return "FAIL"
    if "WAITING_FOR_HFSS" in statuses:
        return "WAITING_FOR_HFSS"
    if statuses == {"PASS"}:
        return "PASS"
    return "FAIL" if args.require_all_pass else "PARTIAL"


def _frequency_grid_mode(args: argparse.Namespace) -> str:
    if (
        abs(float(args.compare_start_ghz) - 5.0) <= 1.0e-12
        and abs(float(args.compare_stop_ghz) - 60.0) <= 1.0e-12
        and abs(float(args.expected_frequency_step_ghz) - 0.5) <= 1.0e-12
        and int(args.expected_frequency_points) == 111
    ):
        return "final_5_60_0p5_111"
    return "diagnostic_screening_only"


def _decision(status: str, frequency_grid_mode: str) -> str:
    if status == "PASS":
        if frequency_grid_mode != "final_5_60_0p5_111":
            return "ACCEPT_DIAGNOSTIC_S8P_EMX_HFSS_SCREENING_ONLY_NOT_FINAL"
        return "ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION"
    if status == "WAITING_FOR_HFSS":
        return "WAIT_FOR_EXPORTED_HFSS_S8P"
    return "DO_NOT_USE_S8P_HFSS_VALIDATION_YET"


def _status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _write_checks_csv(path: Path, checks: list[Check]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "evaluation", "status", "name", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.as_dict())


def _write_results_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "selection_rank",
        "evaluation",
        "status",
        "emx_s8p",
        "hfss_s8p",
        "hfss_port_manifest",
        "ads_formula_trace",
        "port_pairs",
        "worst_metric",
        "worst_percent_error",
        "compare_summary",
        "target_marker_csv",
        "ads_style_plot_summary",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P HFSS Postrun Validation",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- AEDT packet summary: `{summary['aedt_packet_summary']}`",
        f"- Frequency grid mode: `{summary.get('frequency_grid_mode', '')}`",
        f"- Final acceptance candidate: `{summary.get('final_acceptance_candidate', False)}`",
        f"- Sample count: `{summary['sample_count']}`",
        f"- Status counts: `{summary['status_counts']}`",
        "",
        "| Rank | Evaluation | Status | Worst metric | Worst err % | EMX S8P | HFSS S8P | Target marker | Compare summary |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for record in summary["records"]:
        worst = "" if record.get("worst_percent_error") is None else f"{float(record['worst_percent_error']):.4g}"
        lines.append(
            f"| {_cell(record.get('selection_rank', ''))} | {_cell(record.get('evaluation', ''))} | {_cell(record.get('status', ''))} | "
            f"{_cell(record.get('worst_metric', ''))} | {worst} | `{_cell(record.get('emx_s8p', ''))}` | "
            f"`{_cell(record.get('hfss_s8p', ''))}` | `{_cell(record.get('target_marker_csv', ''))}` | "
            f"`{_cell(record.get('compare_summary', ''))}` |"
        )
    lines.extend(["", "## Checks", "", "| Status | Sample | Evaluation | Check | Detail |", "| --- | --- | --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['status'])} | {_cell(check.get('sample', ''))} | {_cell(check.get('evaluation', ''))} | {_cell(check['name'])} | {_cell(check['detail'])} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _check(sample: str, evaluation: str, name: str, passed: bool, detail: Any) -> Check:
    return Check(str(sample), str(evaluation), "PASS" if passed else "FAIL", name, str(detail))


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")[:80] or "sample"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
