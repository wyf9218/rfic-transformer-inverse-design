#!/usr/bin/env python3
"""Plan the next HFSS Lp/Ls root-cause diagnostic sweep.

The planner does not run HFSS.  It turns the current error-pattern diagnosis
into a small, auditable Windows/HFSS experiment matrix that changes one
reference/environment variable at a time.  The matrix is intentionally scoped
to a 15.0/15.5 GHz diagnostic grid; final acceptance still requires the normal
5-60 GHz S8P gate.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIAGNOSIS = PROJECT_ROOT / "outputs" / "hfss_variant_error_pattern_diagnosis_current" / "hfss_variant_error_pattern_diagnosis.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnosis_path = Path(args.diagnosis_summary).expanduser().resolve()
    aedt_packet_path = Path(args.aedt_packet_summary).expanduser().resolve()
    diagnosis = _read_json(diagnosis_path)
    aedt_packet = _read_json(aedt_packet_path)

    checks = _checks(diagnosis_path, diagnosis, aedt_packet_path, aedt_packet, args)
    variants = _build_variants()
    samples = [sample for sample in aedt_packet.get("sample_results", []) if isinstance(sample, dict) and sample.get("overall_status") == "PASS"]
    plan_records = [
        _variant_record(variant, samples, out_dir, aedt_packet_path, args)
        for variant in variants
    ]
    ready = all(item["status"] == "PASS" for item in checks)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if ready else "FAIL",
        "decision": "READY_TO_RUN_HFSS_LP_LS_DIAGNOSTIC_SWEEP" if ready else "DO_NOT_RUN_HFSS_DIAGNOSTIC_SWEEP_UNTIL_INPUTS_PASS",
        "diagnosis_summary": str(diagnosis_path),
        "aedt_packet_summary": str(aedt_packet_path),
        "out_dir": str(out_dir),
        "diagnostic_frequency_grid": {
            "start_ghz": float(args.compare_start_ghz),
            "stop_ghz": float(args.compare_stop_ghz),
            "step_ghz": float(args.expected_frequency_step_ghz),
            "points": int(args.expected_frequency_points),
            "target_ghz": float(args.target_ghz),
        },
        "gate": {
            "max_percent_error": float(args.max_percent_error),
            "final_acceptance_still_requires": "5-60 GHz / 1.0 GHz / 56 points / S8P / Lp,Ls,Qp,Qs,Kw <= gate",
        },
        "reason_from_diagnosis": {
            "best_overall_target_max_pct": (diagnosis.get("best_overall") or {}).get("target_max_pct"),
            "lp_min_error_pct": ((diagnosis.get("final_gate_metric_floor") or {}).get("Lp") or {}).get("min_error_pct"),
            "ls_min_error_pct": ((diagnosis.get("final_gate_metric_floor") or {}).get("Ls") or {}).get("min_error_pct"),
            "kw_min_error_pct": ((diagnosis.get("final_gate_metric_floor") or {}).get("Kw") or {}).get("min_error_pct"),
        },
        "sample_count": len(samples),
        "variant_count": len(plan_records),
        "variants": plan_records,
        "checks": checks,
        "limitations": [
            "This is a diagnostic plan only; it does not run HFSS and does not prove EMX reliability.",
            "A good 15/15.5 GHz result is only a screening signal.  Final acceptance still requires full 5-60 GHz comparison.",
            "K-only agreement is insufficient; accepted evidence must pass Lp/Ls/Qp/Qs/Kw together from the same EMX/HFSS S8P pair.",
        ],
    }

    summary_path = out_dir / "hfss_lp_ls_reference_sweep_plan_summary.json"
    report_path = out_dir / "HFSS_LP_LS_REFERENCE_SWEEP_PLAN_CN.md"
    windows_path = out_dir / "run_hfss_lp_ls_reference_sweep.windows.ps1"
    postrun_path = out_dir / "postrun_validate_hfss_lp_ls_reference_sweep.sh"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    windows_path.write_text(_render_windows_script(summary, args), encoding="utf-8")
    postrun_path.write_text(_render_postrun_script(summary, args), encoding="utf-8")
    postrun_path.chmod(0o755)

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"windows_script={windows_path}")
    print(f"postrun_script={postrun_path}")
    return 0 if summary["overall_status"] == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnosis-summary", default=str(DEFAULT_DIAGNOSIS))
    parser.add_argument("--aedt-packet-summary", required=True)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--compare-start-ghz", type=float, default=15.0)
    parser.add_argument("--compare-stop-ghz", type=float, default=15.5)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=2)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--windows-python-command", default="python")
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _checks(
    diagnosis_path: Path,
    diagnosis: dict[str, Any],
    aedt_packet_path: Path,
    aedt_packet: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    floors = diagnosis.get("final_gate_metric_floor") if isinstance(diagnosis.get("final_gate_metric_floor"), dict) else {}
    lp_floor = _as_float((floors.get("Lp") or {}).get("min_error_pct"))
    ls_floor = _as_float((floors.get("Ls") or {}).get("min_error_pct"))
    samples = [sample for sample in aedt_packet.get("sample_results", []) if isinstance(sample, dict) and sample.get("overall_status") == "PASS"]
    return [
        _check("diagnosis summary exists", diagnosis_path.is_file(), str(diagnosis_path)),
        _check("diagnosis summary says current variants fail", diagnosis.get("overall_status") == "FAIL", str(diagnosis.get("overall_status"))),
        _check("diagnosis shows Lp still above gate", lp_floor is not None and lp_floor > float(args.max_percent_error), f"Lp floor={lp_floor}"),
        _check("diagnosis shows Ls still above gate", ls_floor is not None and ls_floor > float(args.max_percent_error), f"Ls floor={ls_floor}"),
        _check("AEDT packet summary exists", aedt_packet_path.is_file(), str(aedt_packet_path)),
        _check("AEDT packet summary passed", aedt_packet.get("overall_status") == "PASS", str(aedt_packet.get("overall_status"))),
        _check("AEDT packet has at least one PASS sample", bool(samples), f"pass_samples={len(samples)}"),
        _check("diagnostic grid is 15.0/15.5 GHz two-point", _diagnostic_grid_ok(args), _diagnostic_grid_detail(args)),
    ]


def _build_variants() -> list[dict[str, Any]]:
    base = {
        "HFSS_M5_SHIELD_BOUNDARY": "finite",
        "HFSS_UNITE_STRATEGY": "connected_by_bbox",
        "HFSS_UNITE_CONNECTED_M5": "0",
        "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
        "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
        "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
        "HFSS_PORT_SIGNAL_Z_MODE": "payload",
        "HFSS_PORT_GROUND_Z_MODE": "payload",
        "HFSS_PORT_DEEMBED": "0",
        "HFSS_AIR_MARGIN_UM": "250",
        "HFSS_RADIATION_MARGIN_UM": "350",
    }
    specs = [
        ("v65a_baseline_local_bbox_smallest", "Baseline nearest-success family; finite M5, direct integration-line ports, smallest local ground bbox.", {}),
        ("v65b_local_bbox_all_containing", "Only change local reference selection from smallest bbox to all containing local M5 boxes.", {"HFSS_PORT_REFERENCE_MODE": "local_ground_bbox"}),
        ("v65c_no_unite_local_reference", "Only stop conductor unite to test whether object union suppresses inductance.", {"HFSS_UNITE_STRATEGY": "no_unite"}),
        ("v65d_unite_connected_m5", "Only unite connected M5 frame objects to test local ground continuity.", {"HFSS_UNITE_CONNECTED_M5": "1"}),
        ("v65e_m5_perfecte_local_reference", "Only force M5 shield/frame to PerfectE ground.", {"HFSS_M5_SHIELD_BOUNDARY": "perfecte"}),
        ("v65f_port_deembed_direct", "Only enable lumped-port deembed.", {"HFSS_PORT_DEEMBED": "1"}),
        ("v65g_terminal_reference_local", "Only switch from direct integration-line port to PyAEDT terminal reference port.", {"HFSS_USE_PYAEDT_REFERENCE_PORT": "1"}),
        ("v65h_port_z_top", "Only move signal and ground port integration points to conductor top surfaces.", {"HFSS_PORT_SIGNAL_Z_MODE": "top", "HFSS_PORT_GROUND_Z_MODE": "top"}),
        ("v65i_port_z_mid", "Only move signal and ground port integration points to conductor midplanes.", {"HFSS_PORT_SIGNAL_Z_MODE": "mid", "HFSS_PORT_GROUND_Z_MODE": "mid"}),
        ("v65j_large_airbox_same_reference", "Only increase air/radiation margins to test boundary loading.", {"HFSS_AIR_MARGIN_UM": "500", "HFSS_RADIATION_MARGIN_UM": "700"}),
    ]
    variants = []
    for name, reason, overrides in specs:
        env = dict(base)
        env.update(overrides)
        variants.append({"name": name, "reason": reason, "env": env})
    return variants


def _variant_record(
    variant: dict[str, Any],
    samples: list[dict[str, Any]],
    out_dir: Path,
    aedt_packet_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    variant_dir = out_dir / "variants" / variant["name"]
    windows_steps = []
    for sample in samples:
        evaluation = str(sample.get("evaluation") or "sample")
        script_dir = Path(str(sample.get("script_dir") or "")).expanduser()
        build_script = Path(str(sample.get("build_script") or script_dir / "build_hfss_s8p_from_payload.py")).expanduser()
        solve_script = Path(str(sample.get("solve_script") or script_dir / "solve_export_hfss_s8p.py")).expanduser()
        sample_dir = variant_dir / evaluation
        windows_steps.append(
            {
                "evaluation": evaluation,
                "build_script": str(build_script),
                "solve_script": str(solve_script),
                "variant_work_dir": str(sample_dir),
                "hfss_save_path": str(sample_dir / f"{evaluation}_{variant['name']}.aedt"),
                "hfss_solve_project": str(sample_dir / f"{evaluation}_{variant['name']}_solve.aedt"),
                "hfss_results_dir": str(sample_dir / "hfss_solve_export_results"),
                "hfss_build_log": str(sample_dir / "hfss_s8p_build.log"),
                "hfss_port_manifest": str(sample_dir / "hfss_s8p_build_port_manifest.json"),
                "hfss_export_manifest": str(sample_dir / "hfss_s8p_export_manifest.json"),
            }
        )
    return {
        "name": variant["name"],
        "reason": variant["reason"],
        "env": variant["env"],
        "variant_dir": str(variant_dir),
        "postrun_out_dir": str(variant_dir / "postrun_validation"),
        "postrun_command": _postrun_command(aedt_packet_path, variant_dir, args),
        "windows_steps": windows_steps,
    }


def _postrun_command(aedt_packet_path: Path, variant_dir: Path, args: argparse.Namespace) -> list[str]:
    return [
        str(args.python_command),
        "scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py",
        "--aedt-packet-summary",
        str(aedt_packet_path),
        "--hfss-results-dir",
        str(variant_dir),
        "--out-dir",
        str(variant_dir / "postrun_validation"),
        "--compare-start-ghz",
        f"{float(args.compare_start_ghz):g}",
        "--compare-stop-ghz",
        f"{float(args.compare_stop_ghz):g}",
        "--expected-frequency-step-ghz",
        f"{float(args.expected_frequency_step_ghz):g}",
        "--expected-frequency-points",
        str(int(args.expected_frequency_points)),
        "--target-ghz",
        f"{float(args.target_ghz):g}",
        "--target-frequency-tolerance-ghz",
        f"{float(args.target_frequency_tolerance_ghz):g}",
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--ground-unused-ports",
        "--skip-ads-style-plots",
        "--no-fail-exit",
    ]


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS Lp/Ls Reference Sweep Plan",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Diagnosis: `{summary['diagnosis_summary']}`",
        f"- AEDT packet: `{summary['aedt_packet_summary']}`",
        f"- Diagnostic grid: `{summary['diagnostic_frequency_grid']}`",
        f"- Variant count: `{summary['variant_count']}`",
        "",
        "## Why This Sweep",
        "",
        f"- Best current overall target max error: `{summary['reason_from_diagnosis']['best_overall_target_max_pct']}` %",
        f"- Lp/Ls floors: `{summary['reason_from_diagnosis']['lp_min_error_pct']}` % / `{summary['reason_from_diagnosis']['ls_min_error_pct']}` %",
        "- This sweep targets HFSS reference/ground/environment choices because Lp/Ls remain systematically low while some Q/K channels can be close.",
        "",
        "## Checks",
        "",
    ]
    for check in summary["checks"]:
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    lines.extend(["", "## Variants", "", "| Variant | Reason | Key env changes |", "|---|---|---|"])
    baseline_env = summary["variants"][0]["env"] if summary["variants"] else {}
    for variant in summary["variants"]:
        changed = [
            f"{key}={value}"
            for key, value in variant["env"].items()
            if baseline_env.get(key) != value or variant["name"].endswith("baseline_local_bbox_smallest")
        ]
        lines.append(f"| `{variant['name']}` | {variant['reason']} | `{'; '.join(changed[:6])}` |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


def _render_windows_script(summary: dict[str, Any], args: argparse.Namespace) -> str:
    lines = [
        "# Auto-generated HFSS diagnostic sweep. Run in Windows with HFSS/PyAEDT available.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    if summary["overall_status"] != "PASS":
        lines.extend([
            "Write-Error 'Plan inputs did not pass; refusing to run HFSS diagnostic sweep.'",
            "exit 2",
            "",
        ])
        return "\n".join(lines)
    for variant in summary["variants"]:
        lines.append(f"Write-Host '== {variant['name']} =='")
        for key, value in variant["env"].items():
            lines.append(f"$env:{key} = {_ps_quote(value)}")
        for step in variant["windows_steps"]:
            lines.append(f"New-Item -ItemType Directory -Force -Path {_ps_quote(_windows_path(step['variant_work_dir']))} | Out-Null")
            lines.append(f"$env:HFSS_SAVE_PATH = {_ps_quote(_windows_path(step['hfss_save_path']))}")
            lines.append(f"$env:HFSS_SOLVE_PROJECT = {_ps_quote(_windows_path(step['hfss_solve_project']))}")
            lines.append(f"$env:HFSS_SOLVE_RESULTS_DIR = {_ps_quote(_windows_path(step['hfss_results_dir']))}")
            lines.append(f"$env:HFSS_BUILD_LOG = {_ps_quote(_windows_path(step['hfss_build_log']))}")
            lines.append(f"$env:HFSS_PORT_MANIFEST = {_ps_quote(_windows_path(step['hfss_port_manifest']))}")
            lines.append(f"$env:HFSS_EXPORT_MANIFEST = {_ps_quote(_windows_path(step['hfss_export_manifest']))}")
            lines.append(f"& {_ps_quote(args.windows_python_command)} {_ps_quote(_windows_path(step['build_script']))}")
            lines.append(f"& {_ps_quote(args.windows_python_command)} {_ps_quote(_windows_path(step['solve_script']))}")
        lines.append("")
    return "\n".join(lines)


def _render_postrun_script(summary: dict[str, Any], args: argparse.Namespace) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "cd " + shlex.quote(str(REPO_ROOT)),
        "",
    ]
    if summary["overall_status"] != "PASS":
        lines.extend([
            "echo 'Plan inputs did not pass; refusing postrun validation.' >&2",
            "exit 2",
            "",
        ])
        return "\n".join(lines)
    for variant in summary["variants"]:
        lines.append(f"echo '== postrun {variant['name']} =='")
        lines.append(_shell_join(variant["postrun_command"]))
        lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _diagnostic_grid_ok(args: argparse.Namespace) -> bool:
    return (
        abs(float(args.compare_start_ghz) - 15.0) <= 1.0e-12
        and abs(float(args.compare_stop_ghz) - 15.5) <= 1.0e-12
        and abs(float(args.expected_frequency_step_ghz) - 0.5) <= 1.0e-12
        and int(args.expected_frequency_points) == 2
    )


def _diagnostic_grid_detail(args: argparse.Namespace) -> str:
    return (
        f"start={float(args.compare_start_ghz):g}, stop={float(args.compare_stop_ghz):g}, "
        f"step={float(args.expected_frequency_step_ghz):g}, points={int(args.expected_frequency_points)}"
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _windows_path(path: str) -> str:
    text = str(path)
    prefix = "/home/researcher/"
    if text.startswith(prefix):
        return "\\\\Mac\\Home\\" + text[len(prefix):].replace("/", "\\")
    return text.replace("/", "\\")


def _ps_quote(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in command)


if __name__ == "__main__":
    raise SystemExit(main())
