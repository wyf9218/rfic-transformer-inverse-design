#!/usr/bin/env python3
"""Build the next HFSS calibration queue from the measured failure diagnosis.

This script does not run HFSS and does not modify existing V66/V67 packets.  It
turns the historical EMX/HFSS failure diagnosis into a short, ordered queue of
already-generated HFSS variants to run next, then emits a Windows runner and a
postrun validation script for only that queue.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_DIAGNOSIS = (
    PROJECT_ROOT
    / "outputs"
    / "existing_hfss_s8p_failure_diagnosis_current"
    / "existing_hfss_s8p_failure_diagnosis_summary.json"
)
DEFAULT_V66_PLAN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "hfss_v66_calibration_plan_summary.json"
DEFAULT_V67_PLAN = (
    PROJECT_ROOT
    / "outputs"
    / "hfss_v67_material_mesh_calibration_plan_current"
    / "hfss_v67_material_mesh_calibration_plan_summary.json"
)
DEFAULT_INTAKE_MONITOR = (
    PROJECT_ROOT
    / "outputs"
    / "hfss_s8p_intake_to_validation_monitor_current"
    / "hfss_s8p_intake_to_validation_monitor_summary.json"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_next_calibration_queue_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnosis_path = Path(args.diagnosis_summary).expanduser().resolve()
    v66_path = Path(args.v66_plan_summary).expanduser().resolve()
    v67_path = Path(args.v67_plan_summary).expanduser().resolve()
    intake_path = Path(args.intake_monitor_summary).expanduser().resolve()
    diagnosis = _read_json(diagnosis_path)
    v66_plan = _read_json(v66_path)
    v67_plan = _read_json(v67_path)
    intake = _read_json(intake_path)
    windows_path_mapping = _parse_windows_path_mappings(args.windows_path_prefix)
    candidates = _candidate_variants(v66_plan, v67_plan)
    queue = _prioritize(candidates, diagnosis, include_diagnostic=bool(args.include_diagnostic), max_variants=int(args.max_variants))
    checks = _checks(diagnosis_path, diagnosis, v66_path, v66_plan, v67_path, v67_plan, candidates, queue)
    current_gate_count = _current_gate_count(intake)
    overall_status = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    if current_gate_count > 0:
        decision = "CURRENT_GATE_HFSS_S8P_EXISTS_RUN_VALIDATION_MONITOR_BEFORE_MORE_HFSS"
    elif overall_status == "PASS":
        decision = "RUN_PRIORITIZED_HFSS_QUEUE_THEN_POSTRUN_GATE"
    else:
        decision = "FIX_HFSS_QUEUE_INPUTS"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "diagnosis_summary": str(diagnosis_path),
        "v66_plan_summary": str(v66_path),
        "v67_plan_summary": str(v67_path),
        "intake_monitor_summary": str(intake_path),
        "current_gate_spec_pass_count": current_gate_count,
        "diagnosis_snapshot": _diagnosis_snapshot(diagnosis),
        "candidate_count": len(candidates),
        "selected_count": len(queue),
        "queue": queue,
        "checks": checks,
        "windows_path_mapping": {
            "status": "EXPLICIT_PREFIX_MAPPING" if windows_path_mapping else "DEFAULT_POSIX_BACKSLASH_ONLY",
            "mappings": windows_path_mapping,
            "note": (
                "Generated Windows runner paths use explicit POSIX-to-Windows prefix mapping."
                if windows_path_mapping
                else "Generated Windows runner paths only replace '/' with '\\'. Use --windows-path-prefix when Windows sees these files through a mounted drive or different root."
            ),
        },
        "artifacts": {
            "windows_runner": str(out_dir / "run_hfss_priority_calibration_queue.windows.ps1"),
            "cmd_launcher": str(out_dir / "run_hfss_priority_calibration_queue.windows.cmd"),
            "postrun_script": str(out_dir / "postrun_validate_hfss_priority_calibration_queue.sh"),
            "report": str(out_dir / "HFSS_NEXT_CALIBRATION_QUEUE_CN.md"),
        },
        "safety_notes": [
            "This queue does not run HFSS locally and does not generate proof of EMX reliability by itself.",
            "The million-sample EMX campaign remains locked until a current-gate HFSS .s8p passes the EMX/HFSS <=10% physical-metric gate.",
            "Diagnostic-only variants are excluded by default and cannot unlock production by themselves.",
            "HFSS export files must remain .s8p, 8-port, 50 ohm, 5-60 GHz, 1.0 GHz, 56-point Touchstone files before any comparison is accepted.",
        ],
    }
    summary_path = out_dir / "hfss_next_calibration_queue_summary.json"
    report_path = out_dir / "HFSS_NEXT_CALIBRATION_QUEUE_CN.md"
    runner_path = out_dir / "run_hfss_priority_calibration_queue.windows.ps1"
    cmd_path = out_dir / "run_hfss_priority_calibration_queue.windows.cmd"
    postrun_path = out_dir / "postrun_validate_hfss_priority_calibration_queue.sh"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    runner_path.write_text(_render_windows_runner(summary, args.python_command), encoding="utf-8-sig")
    cmd_path.write_text(_render_cmd_launcher(runner_path, summary), encoding="utf-8")
    postrun_path.write_text(_render_postrun_script(summary, args), encoding="utf-8")
    postrun_path.chmod(0o755)

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"selected_count={len(queue)}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"windows_runner={runner_path}")
    print(f"postrun={postrun_path}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnosis-summary", default=str(DEFAULT_DIAGNOSIS))
    parser.add_argument("--v66-plan-summary", default=str(DEFAULT_V66_PLAN))
    parser.add_argument("--v67-plan-summary", default=str(DEFAULT_V67_PLAN))
    parser.add_argument("--intake-monitor-summary", default=str(DEFAULT_INTAKE_MONITOR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--max-variants", type=int, default=8)
    parser.add_argument("--include-diagnostic", action="store_true")
    parser.add_argument("--python-command", default="python")
    parser.add_argument(
        "--windows-path-prefix",
        action="append",
        default=[],
        metavar="POSIX_PREFIX=WINDOWS_PREFIX",
        help=(
            "Map generated POSIX paths into the path namespace visible to Windows/HFSS. "
            "May be repeated; the longest matching POSIX prefix wins. "
            "If omitted, paths are rendered by replacing '/' with '\\'."
        ),
    )
    parser.add_argument("--compare-start-ghz", type=float, default=5.0)
    parser.add_argument("--compare-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _candidate_variants(v66_plan: dict[str, Any], v67_plan: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source, plan in (("v66", v66_plan), ("v67", v67_plan)):
        for variant in plan.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            item = dict(variant)
            item["source_plan"] = source
            item["plan_summary"] = plan.get("out_dir", "")
            item["name"] = str(item.get("name") or "")
            candidates.append(item)
    return candidates


def _prioritize(
    candidates: list[dict[str, Any]],
    diagnosis: dict[str, Any],
    *,
    include_diagnostic: bool,
    max_variants: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for candidate in candidates:
        if candidate.get("diagnostic_only") and not include_diagnostic:
            continue
        score, reasons = _score_candidate(candidate, diagnosis)
        item = _queue_item(candidate, score, reasons)
        scored.append((-score, str(candidate.get("name", "")), item))
    scored.sort()
    queue: list[dict[str, Any]] = []
    for rank, (_, _, item) in enumerate(scored[: max(0, int(max_variants))], start=1):
        item["queue_rank"] = rank
        queue.append(item)
    return queue


def _score_candidate(candidate: dict[str, Any], diagnosis: dict[str, Any]) -> tuple[float, list[str]]:
    name = str(candidate.get("name", "")).lower()
    source = str(candidate.get("source_plan", ""))
    errors = (diagnosis.get("best_target_marker") or {}).get("target15_core_percent_errors") or {}
    modes = diagnosis.get("failure_mode_counts") if isinstance(diagnosis.get("failure_mode_counts"), dict) else {}
    ratio_stats = diagnosis.get("hfss_to_emx_ratio_statistics") if isinstance(diagnosis.get("hfss_to_emx_ratio_statistics"), dict) else {}
    sign_counts = diagnosis.get("sign_mismatch_counts") if isinstance(diagnosis.get("sign_mismatch_counts"), dict) else {}
    score = 10.0 if source == "v67" else 0.0
    reasons: list[str] = []

    k_ok = _as_float(errors.get("k")) is not None and float(errors["k"]) <= 10.0
    lp_gap = _as_float(errors.get("lp_nh")) is not None and float(errors["lp_nh"]) > 10.0
    ls_gap = _as_float(errors.get("ls_nh")) is not None and float(errors["ls_nh"]) > 10.0
    q_gap = _as_float(errors.get("q")) is not None and float(errors["q"]) > 10.0
    lp_ratio_median = _as_float((ratio_stats.get("lp_nh") or {}).get("median")) if isinstance(ratio_stats.get("lp_nh"), dict) else None
    ls_ratio_median = _as_float((ratio_stats.get("ls_nh") or {}).get("median")) if isinstance(ratio_stats.get("ls_nh"), dict) else None

    if k_ok and (lp_gap or ls_gap or q_gap):
        if name.startswith("v67a"):
            score += 100
            reasons.append("baseline before changing physics because K is already close but Lp/Ls/Q fail")
        if name.startswith("v67b"):
            score += 115
            reasons.append("solve-inside directly tests finite-thickness conductor current distribution")
        if name.startswith("v67c") or name.startswith("v67d"):
            score += 88
            reasons.append("loss-stack variants target Q while preserving S8P geometry")
        if name.startswith("v67e"):
            score += 84
            reasons.append("no-unite tests whether geometry boolean operations suppress self-inductance")
        if name.startswith("v67f"):
            score += 78
            reasons.append("large airbox tests boundary loading on full-band curves")
        if name.startswith("v67g"):
            score += 74
            reasons.append("higher mesh/basis order tests convergence before accepting mismatch")
        if name.startswith("v66a"):
            score += 58
            reasons.append("V66 local-reference baseline remains useful as a reference check")

    if (
        modes.get("HFSS_INDUCTANCE_SCALE_TOO_SMALL_CHECK_GEOMETRY_UNITS_OR_METAL_STACK", 0) > 0
        or (lp_ratio_median is not None and lp_ratio_median < 0.35)
        or (ls_ratio_median is not None and ls_ratio_median < 0.35)
    ):
        if any(token in name for token in ("solve_inside", "no_unite", "large_airbox", "all_m5", "perfecte")):
            score += 22
            reasons.append("diagnosis says HFSS inductance magnitude is systematically low")

    if modes.get("NON_POSITIVE_Q_CHECK_LOSS_MODEL_TERMINAL_REFERENCE_OR_GROUND", 0) > 0:
        if any(token in name for token in ("loss_tangent", "conductivity", "pyaedt_terminal", "port_top", "port_mid")):
            score += 18
            reasons.append("non-positive Q points to loss/reference/terminal settings")

    if int(sign_counts.get("k") or 0) > 0 or int(sign_counts.get("kw") or 0) > 0:
        if any(token in name for token in ("pyaedt_terminal", "port_top", "port_mid", "all_m5")):
            score += 15
            reasons.append("some historical candidates show K/Kw sign mismatch, so port/reference variants remain diagnostic")

    if candidate.get("diagnostic_only"):
        score -= 40
        reasons.append("diagnostic-only variant cannot unlock final production")
    if not reasons:
        reasons.append("kept as lower-priority coverage variant")
    return score, reasons


def _queue_item(candidate: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    keys = [
        "name",
        "source_plan",
        "purpose",
        "diagnostic_only",
        "final_acceptance_candidate",
        "variant_dir",
        "hfss_results_dir",
        "hfss_save_path",
        "hfss_solve_project",
        "hfss_build_log",
        "hfss_port_manifest",
        "hfss_export_manifest",
        "build_script",
        "solve_script",
        "payload_json",
        "single_variant_packet_summary",
        "postrun_out_dir",
        "env",
    ]
    item = {key: candidate.get(key) for key in keys if key in candidate}
    item["priority_score"] = round(float(score), 6)
    item["priority_reasons"] = reasons
    item["required_files"] = [
        str(candidate.get("build_script", "")),
        str(candidate.get("solve_script", "")),
        str(candidate.get("payload_json", "")),
        str(candidate.get("single_variant_packet_summary", "")),
    ]
    return item


def _checks(
    diagnosis_path: Path,
    diagnosis: dict[str, Any],
    v66_path: Path,
    v66_plan: dict[str, Any],
    v67_path: Path,
    v67_plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    queue: list[dict[str, Any]],
) -> list[dict[str, str]]:
    checks = [
        _check("diagnosis summary exists", diagnosis_path.is_file(), str(diagnosis_path)),
        _check("diagnosis confirms no historical pass", int(diagnosis.get("pass_count") or 0) == 0, f"pass_count={diagnosis.get('pass_count')}"),
        _check("diagnosis has best target marker", isinstance(diagnosis.get("best_target_marker"), dict), str(type(diagnosis.get("best_target_marker")).__name__)),
        _check("V66 plan exists", v66_path.is_file(), str(v66_path)),
        _check("V66 plan status PASS", str(v66_plan.get("overall_status")) == "PASS", str(v66_plan.get("overall_status"))),
        _check("V67 plan exists", v67_path.is_file(), str(v67_path)),
        _check("V67 plan status PASS", str(v67_plan.get("overall_status")) == "PASS", str(v67_plan.get("overall_status"))),
        _check("candidate variants exist", len(candidates) > 0, str(len(candidates))),
        _check("priority queue is non-empty", len(queue) > 0, str(len(queue))),
    ]
    for item in queue:
        for file_path in item.get("required_files") or []:
            checks.append(_check(f"{item.get('name')} required file exists", Path(str(file_path)).is_file(), str(file_path)))
    return checks


def _current_gate_count(intake: dict[str, Any]) -> int:
    latest = intake.get("latest_intake_summary") if isinstance(intake.get("latest_intake_summary"), dict) else {}
    counts = latest.get("counts") if isinstance(latest.get("counts"), dict) else {}
    return int(counts.get("current_gate_spec_pass_count") or 0)


def _diagnosis_snapshot(diagnosis: dict[str, Any]) -> dict[str, Any]:
    best = diagnosis.get("best_target_marker") if isinstance(diagnosis.get("best_target_marker"), dict) else {}
    return {
        "pass_count": diagnosis.get("pass_count"),
        "best_target15_worst_percent_error": best.get("target15_worst_percent_error"),
        "best_target15_worst_metric": best.get("target15_worst_metric"),
        "best_target15_core_percent_errors": best.get("target15_core_percent_errors") or {},
        "dominant_failure_modes": diagnosis.get("dominant_failure_modes") or [],
        "sign_mismatch_counts": diagnosis.get("sign_mismatch_counts") or {},
        "hfss_to_emx_ratio_statistics": diagnosis.get("hfss_to_emx_ratio_statistics") or {},
    }


def _parse_windows_path_mappings(raw_specs: list[str]) -> list[dict[str, str]]:
    mappings: list[dict[str, str]] = []
    for raw in raw_specs or []:
        if "=" not in raw:
            raise ValueError(f"--windows-path-prefix must be POSIX_PREFIX=WINDOWS_PREFIX, got: {raw}")
        posix_prefix, windows_prefix = raw.split("=", 1)
        posix_prefix = str(Path(posix_prefix).expanduser().resolve())
        windows_prefix = windows_prefix.strip()
        if not posix_prefix or not windows_prefix:
            raise ValueError(f"--windows-path-prefix has an empty side: {raw}")
        mappings.append({"posix_prefix": posix_prefix, "windows_prefix": windows_prefix})
    mappings.sort(key=lambda item: len(item["posix_prefix"]), reverse=True)
    return mappings


def _render_windows_runner(summary: dict[str, Any], python_command: str) -> str:
    status_json = Path(summary["out_dir"]) / "priority_queue_run_status" / "hfss_priority_queue_run_status.json"
    mappings = (summary.get("windows_path_mapping") or {}).get("mappings") or []
    lines = [
        "# Auto-generated prioritized HFSS calibration queue. Run in Windows with HFSS/PyAEDT available.",
        f"# Path mapping status: {(summary.get('windows_path_mapping') or {}).get('status')}",
        f"# Path mapping note: {(summary.get('windows_path_mapping') or {}).get('note')}",
        "$ErrorActionPreference = 'Continue'",
        f"$PythonCommand = '{python_command}'",
        f"$StatusJson = '{_windows_path(status_json, mappings)}'",
        "New-Item -ItemType Directory -Force -Path (Split-Path $StatusJson) | Out-Null",
        "$VariantResults = @()",
        "",
        "function Run-HfssQueueVariant {",
        "    param([hashtable]$Spec)",
        "    Write-Host \"== HFSS queue $($Spec.rank): $($Spec.name) ==\"",
        "    foreach ($key in $Spec.env.Keys) { Set-Item -Path \"Env:$key\" -Value ([string]$Spec.env[$key]) }",
        "    $env:HFSS_S8P_PAYLOAD = $Spec.payload",
        "    $env:HFSS_SAVE_PATH = $Spec.save",
        "    $env:HFSS_SOLVE_PROJECT = $Spec.solve_project",
        "    $env:HFSS_SOLVE_RESULTS_DIR = $Spec.results",
        "    $env:HFSS_BUILD_LOG = $Spec.build_log",
        "    $env:HFSS_PORT_MANIFEST = $Spec.port_manifest",
        "    $env:HFSS_EXPORT_MANIFEST = $Spec.export_manifest",
        "    New-Item -ItemType Directory -Force -Path $Spec.results | Out-Null",
        "    try {",
        "        & $PythonCommand $Spec.build_script",
        "        $buildStarted = $?",
        "        $buildExitCode = $LASTEXITCODE",
        "        if (-not $buildStarted) { throw \"build command failed to start or was not recognized\" }",
        "        if ($null -ne $buildExitCode -and $buildExitCode -ne 0) { throw \"build failed with exit code $buildExitCode\" }",
        "        & $PythonCommand $Spec.solve_script",
        "        $solveStarted = $?",
        "        $solveExitCode = $LASTEXITCODE",
        "        if (-not $solveStarted) { throw \"solve/export command failed to start or was not recognized\" }",
        "        if ($null -ne $solveExitCode -and $solveExitCode -ne 0) { throw \"solve/export failed with exit code $solveExitCode\" }",
        "        $s8pCount = @(Get-ChildItem -Path $Spec.results -Filter '*.s8p' -Recurse -ErrorAction SilentlyContinue).Count",
        "        if ($s8pCount -lt 1) { throw \"solve/export completed but no .s8p was found in $($Spec.results)\" }",
        "        return [PSCustomObject]@{ rank=$Spec.rank; name=$Spec.name; status='PASS'; s8p_count=$s8pCount; results=$Spec.results; error='' }",
        "    } catch {",
        "        return [PSCustomObject]@{ rank=$Spec.rank; name=$Spec.name; status='FAIL'; s8p_count=0; results=$Spec.results; error=$_.Exception.Message }",
        "    }",
        "}",
        "",
    ]
    for item in summary["queue"]:
        env_items = "; ".join(f"{key}='{value}'" for key, value in sorted((item.get("env") or {}).items()))
        lines.extend(
            [
                "$VariantResults += Run-HfssQueueVariant @{",
                f"  rank='{item['queue_rank']}'; name='{item['name']}';",
                f"  payload='{_windows_path(Path(str(item.get('payload_json'))), mappings)}';",
                f"  save='{_windows_path(Path(str(item.get('hfss_save_path'))), mappings)}';",
                f"  solve_project='{_windows_path(Path(str(item.get('hfss_solve_project'))), mappings)}';",
                f"  results='{_windows_path(Path(str(item.get('hfss_results_dir'))), mappings)}';",
                f"  build_log='{_windows_path(Path(str(item.get('hfss_build_log'))), mappings)}';",
                f"  port_manifest='{_windows_path(Path(str(item.get('hfss_port_manifest'))), mappings)}';",
                f"  export_manifest='{_windows_path(Path(str(item.get('hfss_export_manifest'))), mappings)}';",
                f"  build_script='{_windows_path(Path(str(item.get('build_script'))), mappings)}';",
                f"  solve_script='{_windows_path(Path(str(item.get('solve_script'))), mappings)}';",
                f"  env=@{{ {env_items} }}",
                "}",
                "",
            ]
        )
    lines.extend(
        [
            "$passCount = @($VariantResults | Where-Object { $_.status -eq 'PASS' }).Count",
            "$overall = if ($passCount -gt 0) { 'EXPORTS_READY_FOR_POSTRUN' } else { 'FAIL_NO_EXPORTS' }",
            "[PSCustomObject]@{ generated_utc=(Get-Date).ToUniversalTime().ToString('s') + 'Z'; overall_status=$overall; pass_count=$passCount; variants=$VariantResults } | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $StatusJson",
            "Write-Host \"HFSS priority queue status JSON: $StatusJson\"",
            "if ($passCount -lt 1) { exit 2 }",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_cmd_launcher(runner_path: Path, summary: dict[str, Any]) -> str:
    mappings = (summary.get("windows_path_mapping") or {}).get("mappings") or []
    return "@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File " + f"\"{_windows_path(runner_path, mappings)}\"\r\n"


def _render_postrun_script(summary: dict[str, Any], args: argparse.Namespace) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"REPO_ROOT={_shell_quote(str(REPO_ROOT))}",
        'PYTHON="${REPO_ROOT}/.venv/bin/python"',
        'if [ ! -x "$PYTHON" ]; then PYTHON="python3"; fi',
        "",
    ]
    for item in summary["queue"]:
        lines.extend(
            [
                f"echo '== postrun queue {item['queue_rank']}: {item['name']} ==' ",
                '"$PYTHON" "${REPO_ROOT}/scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py" \\',
                f"  --aedt-packet-summary {_shell_quote(str(item.get('single_variant_packet_summary')))} \\",
                f"  --hfss-results-dir {_shell_quote(str(item.get('hfss_results_dir')))} \\",
                f"  --out-dir {_shell_quote(str(item.get('postrun_out_dir')))} \\",
                f"  --compare-start-ghz {float(args.compare_start_ghz):g} \\",
                f"  --compare-stop-ghz {float(args.compare_stop_ghz):g} \\",
                f"  --expected-frequency-step-ghz {float(args.expected_frequency_step_ghz):g} \\",
                f"  --expected-frequency-points {int(args.expected_frequency_points)} \\",
                f"  --target-ghz {float(args.target_ghz):g} \\",
                f"  --max-percent-error {float(args.max_percent_error):g} \\",
                "  --ground-unused-ports \\",
                "  --no-fail-exit",
                "",
            ]
        )
    return "\n".join(lines)


def _render_report(summary: dict[str, Any]) -> str:
    snap = summary["diagnosis_snapshot"]
    path_mapping = summary.get("windows_path_mapping") or {}
    lines = [
        "# HFSS Next Calibration Queue",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Current-gate spec-pass count: `{summary['current_gate_spec_pass_count']}`",
        f"- Historical pass count: `{snap.get('pass_count')}`",
        f"- Best 15GHz worst error: `{snap.get('best_target15_worst_percent_error')}` %",
        f"- Best 15GHz worst metric: `{snap.get('best_target15_worst_metric')}`",
        f"- Selected variants: `{summary['selected_count']}` / `{summary['candidate_count']}`",
        "",
        "## Priority Queue",
        "",
        "| Rank | Source | Variant | Score | Final candidate | Why |",
        "| ---: | --- | --- | ---: | --- | --- |",
    ]
    for item in summary["queue"]:
        reasons = "; ".join(item.get("priority_reasons") or [])
        lines.append(
            f"| {item['queue_rank']} | `{item.get('source_plan')}` | `{item.get('name')}` | {item.get('priority_score')} | `{item.get('final_acceptance_candidate')}` | {reasons} |"
        )
    lines.extend(
        [
            "",
            "## Execution Artifacts",
            "",
            f"- Windows runner: `{summary['artifacts']['windows_runner']}`",
            f"- CMD launcher: `{summary['artifacts']['cmd_launcher']}`",
            f"- Postrun validator: `{summary['artifacts']['postrun_script']}`",
            f"- Windows path mapping status: `{path_mapping.get('status')}`",
            f"- Windows path mapping note: {path_mapping.get('note')}",
            "",
            "## Safety Notes",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _check(name: str, condition: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if condition else "FAIL", "name": name, "detail": str(detail)}


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _windows_path(path: Path, mappings: list[dict[str, str]] | None = None) -> str:
    resolved = Path(path).expanduser().resolve()
    for mapping in mappings or []:
        prefix = Path(mapping["posix_prefix"]).expanduser().resolve()
        if _is_relative_to(resolved, prefix):
            relative = resolved.relative_to(prefix)
            relative_text = str(relative).replace("/", "\\")
            windows_prefix = str(mapping["windows_prefix"])
            if relative_text == ".":
                return windows_prefix
            separator = "" if windows_prefix.endswith(("\\", "/")) else "\\"
            return f"{windows_prefix}{separator}{relative_text}"
    return str(resolved).replace("/", "\\")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
