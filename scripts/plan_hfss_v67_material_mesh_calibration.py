#!/usr/bin/env python3
"""Plan a V67 HFSS material/mesh calibration sweep from the V66 packet.

This script does not run HFSS. It creates a follow-up execution packet that
keeps the approved S8P geometry, port order, and frequency contract, while
isolating modeling knobs that can explain the current systematic Lp/Ls/Q gap:
conductor solve-inside, dielectric loss mode, mesh convergence, airbox extent,
object unite strategy, and pin/fixture influence.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_V66_PLAN = (
    PROJECT_ROOT
    / "outputs"
    / "hfss_v66_calibration_plan_current"
    / "hfss_v66_calibration_plan_summary.json"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v67_material_mesh_calibration_plan_current"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


BASE_ENV = {
    "HFSS_M5_SHIELD_BOUNDARY": "finite",
    "HFSS_UNITE_STRATEGY": "connected_by_bbox",
    "HFSS_UNITE_CONNECTED_M5": "0",
    "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
    "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
    "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
    "HFSS_PORT_SIGNAL_Z_MODE": "payload",
    "HFSS_PORT_GROUND_Z_MODE": "payload",
    "HFSS_PORT_DEEMBED": "0",
    "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
    "HFSS_AIR_MARGIN_UM": "500",
    "HFSS_RADIATION_MARGIN_UM": "700",
}


V67_VARIANTS: list[dict[str, Any]] = [
    {
        "name": "v67a_tight_mesh_baseline",
        "purpose": "Keep the best V66 local-reference baseline, but tighten convergence before changing physics.",
        "env": {
            **BASE_ENV,
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "40",
            "HFSS_SWEEP_TYPE": "Discrete",
        },
    },
    {
        "name": "v67b_solve_inside_conductors",
        "purpose": "Enable conductor solve-inside to test whether finite-thickness current distribution is depressing Lp/Ls/Q.",
        "env": {
            **BASE_ENV,
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
        },
    },
    {
        "name": "v67c_solve_inside_loss_tangent",
        "purpose": "Combine conductor solve-inside with the lossy dielectric stack to separate conductor and dielectric Q effects.",
        "env": {
            **BASE_ENV,
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "loss_tangent",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
        },
    },
    {
        "name": "v67d_dielectric_conductivity_stack",
        "purpose": "Use dielectric conductivity rather than loss tangent to test the foundry-stack loss interpretation.",
        "env": {
            **BASE_ENV,
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "conductivity",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
        },
    },
    {
        "name": "v67e_no_unite_solve_inside",
        "purpose": "Disable object uniting while keeping solve-inside, checking whether HFSS unite operations suppress self-inductance.",
        "env": {
            **BASE_ENV,
            "HFSS_CONDUCTOR_SOLVE_INSIDE": "1",
            "HFSS_UNITE_STRATEGY": "no_unite",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
        },
    },
    {
        "name": "v67f_large_airbox_tight_mesh",
        "purpose": "Increase airbox/radiation margins to test boundary loading on extracted inductance and Q.",
        "env": {
            **BASE_ENV,
            "HFSS_AIR_MARGIN_UM": "800",
            "HFSS_RADIATION_MARGIN_UM": "1200",
            "HFSS_AIR_BELOW_UM": "150",
            "HFSS_AIR_ABOVE_UM": "1500",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
        },
    },
    {
        "name": "v67g_basis_order2_mesh",
        "purpose": "Increase basis order with tight convergence to catch mesh-order sensitivity before accepting HFSS/EMX mismatch.",
        "env": {
            **BASE_ENV,
            "HFSS_SETUP_MAX_DELTA_S": "0.008",
            "HFSS_SETUP_MAX_PASSES": "16",
            "HFSS_SETUP_MIN_PASSES": "3",
            "HFSS_SETUP_MIN_CONVERGED_PASSES": "2",
            "HFSS_SETUP_PERCENT_REFINEMENT": "45",
            "HFSS_SETUP_BASIS_ORDER": "2",
        },
    },
    {
        "name": "v67h_skip_pin_fixture_diagnostic",
        "purpose": "Diagnostic only: remove pin-purpose conductors to estimate how much the port fixture/lead geometry drives Lp/Ls.",
        "diagnostic_only": True,
        "env": {
            **BASE_ENV,
            "HFSS_SKIP_PIN_CONDUCTORS": "1",
            "HFSS_SETUP_MAX_DELTA_S": "0.01",
            "HFSS_SETUP_MAX_PASSES": "14",
        },
    },
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    v66_plan_path = Path(args.v66_plan_summary).expanduser().resolve()
    v66_plan = _read_json(v66_plan_path)
    source = _source_from_v66(v66_plan)
    evaluation = str(source.get("evaluation") or args.evaluation)
    build_script = Path(str(source.get("build_script", ""))).expanduser().resolve()
    solve_script = Path(str(source.get("solve_script", ""))).expanduser().resolve()
    payload_json = Path(str(source.get("payload_json", ""))).expanduser().resolve()
    checks = _checks(v66_plan_path, v66_plan, build_script, solve_script, payload_json)
    variants = [
        _variant_record(item, index, out_dir, evaluation, build_script, solve_script, payload_json, args)
        for index, item in enumerate(V67_VARIANTS, start=1)
    ]
    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    if overall_status == "PASS":
        for record in variants:
            _write_variant_packet(record)

    decision = (
        "RUN_V67_IF_V66_FAILS_OR_RUN_IN_PARALLEL_FOR_MATERIAL_MESH_DIAGNOSIS"
        if overall_status == "PASS"
        else "FIX_V67_PLAN_INPUTS"
    )
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "v66_plan_summary": str(v66_plan_path),
        "out_dir": str(out_dir),
        "evaluation": evaluation,
        "source_build_script": str(build_script),
        "source_solve_script": str(solve_script),
        "source_payload_json": str(payload_json),
        "v66_gate_status": v66_plan.get("gate_status") or {},
        "v66_diagnosis": v66_plan.get("diagnosis") or {},
        "v67_diagnosis_response": _diagnosis_response(v66_plan),
        "variant_count": len(variants),
        "variants": variants,
        "postrun_validation_contract": {
            "hfss_touchstone_suffix": ".s8p",
            "expected_ports": 8,
            "compare_start_ghz": float(args.compare_start_ghz),
            "compare_stop_ghz": float(args.compare_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "target_ghz": float(args.target_ghz),
            "ground_unused_ports": True,
            "required_metrics": ["lp_nh", "ls_nh", "q", "k", "kw"],
            "max_percent_error": float(args.max_percent_error),
            "final_acceptance_candidate": _is_final_contract(args),
        },
        "checks": [check.as_dict() for check in checks],
        "artifacts": {
            "windows_runner": str(out_dir / "run_hfss_v67_material_mesh_calibration_resilient.windows.ps1"),
            "cmd_launcher": str(out_dir / "run_hfss_v67_material_mesh_calibration_resilient.windows.cmd"),
            "postrun_script": str(out_dir / "postrun_validate_hfss_v67_material_mesh_calibration.sh"),
            "report": str(out_dir / "HFSS_V67_MATERIAL_MESH_CALIBRATION_PLAN_CN.md"),
        },
        "limitations": [
            "This packet plans HFSS runs only; it does not create measured HFSS `.s8p` evidence.",
            "V67 is a material/mesh diagnostic sweep. It cannot unlock the million-sample EMX campaign until exported HFSS `.s8p` files pass the EMX/HFSS <=10% gate.",
            "The skip-pin variant is diagnostic only and cannot be the final accepted engineering structure.",
        ],
    }
    summary_path = out_dir / "hfss_v67_material_mesh_calibration_plan_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path = out_dir / "HFSS_V67_MATERIAL_MESH_CALIBRATION_PLAN_CN.md"
    report_path.write_text(_render_report(summary), encoding="utf-8")
    if overall_status == "PASS":
        runner_path = out_dir / "run_hfss_v67_material_mesh_calibration_resilient.windows.ps1"
        runner_path.write_text(_render_resilient_runner(summary, args.python_command), encoding="utf-8")
        cmd_path = out_dir / "run_hfss_v67_material_mesh_calibration_resilient.windows.cmd"
        cmd_path.write_text(_render_cmd_launcher(runner_path), encoding="utf-8")
        postrun_path = out_dir / "postrun_validate_hfss_v67_material_mesh_calibration.sh"
        postrun_path.write_text(_render_postrun_script(summary, args), encoding="utf-8")
        postrun_path.chmod(0o755)

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if overall_status == "PASS":
        print(f"windows_runner={summary['artifacts']['windows_runner']}")
        print(f"cmd_launcher={summary['artifacts']['cmd_launcher']}")
        print(f"postrun={summary['artifacts']['postrun_script']}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v66-plan-summary", default=str(DEFAULT_V66_PLAN))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--evaluation", default="26cb45d70af3cfd0")
    parser.add_argument("--python-command", default="python")
    parser.add_argument("--compare-start-ghz", type=float, default=5.0)
    parser.add_argument("--compare-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _source_from_v66(plan: dict[str, Any]) -> dict[str, Any]:
    for variant in plan.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        build = variant.get("build_script")
        solve = variant.get("solve_script")
        payload = variant.get("payload_json")
        if build and solve and payload:
            return {
                "evaluation": variant.get("evaluation"),
                "build_script": build,
                "solve_script": solve,
                "payload_json": payload,
            }
    return {}


def _checks(
    v66_plan_path: Path,
    v66_plan: dict[str, Any],
    build_script: Path,
    solve_script: Path,
    payload_json: Path,
) -> list[Check]:
    historical_pass_count = _path_get(v66_plan, ("gate_status", "historical_pass_count"))
    best_target15 = _path_get_float(v66_plan, ("gate_status", "best_target15_worst_percent_error"))
    return [
        _check("V66 plan summary exists", v66_plan_path.is_file(), str(v66_plan_path)),
        _check("V66 plan is usable", str(v66_plan.get("overall_status")) == "PASS", str(v66_plan.get("overall_status"))),
        _check("V66 variants exist", len(v66_plan.get("variants") or []) > 0, str(len(v66_plan.get("variants") or []))),
        _check("V66 historical pass count is zero", int(historical_pass_count or 0) == 0, f"historical_pass_count={historical_pass_count}"),
        _check("V66 best 15 GHz marker exists", best_target15 is not None and math.isfinite(best_target15), str(best_target15)),
        _check("source build script exists", build_script.is_file(), str(build_script)),
        _check("source solve script exists", solve_script.is_file(), str(solve_script)),
        _check("source payload JSON exists", payload_json.is_file(), str(payload_json)),
    ]


def _diagnosis_response(v66_plan: dict[str, Any]) -> dict[str, Any]:
    diagnosis = v66_plan.get("diagnosis") or {}
    gate = v66_plan.get("gate_status") or {}
    return {
        "primary_target": "systematic Lp/Ls/Q error after K/Kw is comparatively closer",
        "best_target15_worst_percent_error": gate.get("best_target15_worst_percent_error"),
        "why_v67_exists": [
            "V66 mainly sweeps reference conductor, port z, deembed, and M5 boundary assumptions.",
            "Historical diagnostics show Lp/Ls stay outside gate even when K/Kw can be close.",
            "V67 therefore sweeps material, conductor current distribution, mesh convergence, and airbox variables before changing the approved S8P geometry.",
        ],
        "inherits_v66_hypothesis": diagnosis.get("primary_root_cause_hypothesis"),
    }


def _variant_record(
    item: dict[str, Any],
    index: int,
    out_dir: Path,
    evaluation: str,
    build_script: Path,
    solve_script: Path,
    payload_json: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    name = str(item["name"])
    variant_dir = out_dir / "variants" / name / evaluation
    diagnostic_only = bool(item.get("diagnostic_only", False))
    patched_frequency_grid = {
        "setup_frequency_ghz": float(args.target_ghz),
        "start_ghz": float(args.compare_start_ghz),
        "stop_ghz": float(args.compare_stop_ghz),
        "step_ghz": float(args.expected_frequency_step_ghz),
        "points": int(args.expected_frequency_points),
        "expected_points": int(args.expected_frequency_points),
    }
    return {
        "name": name,
        "purpose": str(item["purpose"]),
        "diagnostic_only": diagnostic_only,
        "final_acceptance_candidate": _is_final_contract(args) and not diagnostic_only,
        "variant_dir": str(variant_dir),
        "hfss_results_dir": str(variant_dir / "hfss_solve_export_results"),
        "hfss_save_path": str(variant_dir / f"{evaluation}_{name}.aedt"),
        "hfss_solve_project": str(variant_dir / f"{evaluation}_{name}_solve.aedt"),
        "hfss_build_log": str(variant_dir / "hfss_s8p_build.log"),
        "hfss_port_manifest": str(variant_dir / "hfss_s8p_build_port_manifest.json"),
        "hfss_export_manifest": str(variant_dir / "hfss_s8p_export_manifest.json"),
        "build_script": str(variant_dir / "build_hfss_s8p_from_payload.py"),
        "solve_script": str(variant_dir / "solve_export_hfss_s8p.py"),
        "payload_json": str(variant_dir / "hfss_s8p_build_payload.json"),
        "source_build_script": str(build_script),
        "source_solve_script": str(solve_script),
        "source_payload_json": str(payload_json),
        "patched_frequency_grid": patched_frequency_grid,
        "single_variant_packet_summary": str(variant_dir / "hfss_v67_single_variant_packet_summary.json"),
        "postrun_out_dir": str(variant_dir / "postrun_validation"),
        "env": dict(item["env"]),
        "selection_rank": str(index),
        "evaluation": evaluation,
    }


def _write_variant_packet(record: dict[str, Any]) -> None:
    variant_dir = Path(record["variant_dir"])
    variant_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(record["source_build_script"]), Path(record["build_script"]))
    shutil.copy2(Path(record["source_solve_script"]), Path(record["solve_script"]))
    payload = _read_json(Path(record["source_payload_json"]))
    payload["frequency_grid"] = dict(record["patched_frequency_grid"])
    payload.setdefault("hfss", {})
    payload["hfss"]["setup_name"] = payload["hfss"].get("setup_name") or "Setup_15GHz"
    payload["hfss"]["sweep_name"] = "Sweep_5_60_1p0"
    payload["v67_patch"] = {
        "source_payload_json": record["source_payload_json"],
        "reason": "V67 material/mesh calibration keeps the final S8P frequency contract while sweeping HFSS modeling assumptions.",
        "variant": record["name"],
        "diagnostic_only": record["diagnostic_only"],
        "patched_frequency_grid": dict(record["patched_frequency_grid"]),
    }
    Path(record["payload_json"]).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    packet = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "decision": "HFSS_V67_SINGLE_VARIANT_READY_FOR_POSTRUN_AFTER_EXPORT",
        "sample_results": [
            {
                "overall_status": "PASS",
                "selection_rank": record["selection_rank"],
                "evaluation": record["evaluation"],
                "script_dir": str(variant_dir),
                "payload_json": record["payload_json"],
                "build_script": record["build_script"],
                "solve_script": record["solve_script"],
                "hfss_port_manifest": record["hfss_port_manifest"],
            }
        ],
        "limitations": [
            "Single-variant postrun packet only. HFSS must export the matching `.s8p` before validation can pass.",
        ],
    }
    Path(record["single_variant_packet_summary"]).write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")


def _render_resilient_runner(summary: dict[str, Any], python_command: str) -> str:
    status_json = Path(summary["out_dir"]) / "resilient_run_status" / "hfss_v67_resilient_run_status.json"
    transcript_path = Path(summary["out_dir"]) / "resilient_run_status" / "hfss_v67_resilient_run_transcript.txt"
    lines = [
        "# Auto-generated HFSS V67 material/mesh calibration runner. Run inside Windows with HFSS/PyAEDT available.",
        "$ErrorActionPreference = 'Continue'",
        f"$PythonCommand = '{python_command}'",
        f"$StatusJson = '{_windows_path(status_json)}'",
        f"$TranscriptPath = '{_windows_path(transcript_path)}'",
        "New-Item -ItemType Directory -Force -Path (Split-Path $StatusJson) | Out-Null",
        "Start-Transcript -Path $TranscriptPath -Append",
        "$VariantResults = @()",
        "",
        "function Run-V67Variant {",
        "    param(",
        "        [string]$Name, [string]$Payload, [string]$SavePath, [string]$SolveProject,",
        "        [string]$ResultsDir, [string]$BuildLog, [string]$PortManifest, [string]$ExportManifest,",
        "        [string]$BuildScript, [string]$SolveScript, [hashtable]$EnvMap",
        "    )",
        "    Write-Host \"== V67 $Name ==\"",
        "    foreach ($key in $EnvMap.Keys) { Set-Item -Path \"Env:$key\" -Value ([string]$EnvMap[$key]) }",
        "    $env:HFSS_S8P_PAYLOAD = $Payload",
        "    $env:HFSS_SAVE_PATH = $SavePath",
        "    $env:HFSS_SOLVE_PROJECT = $SolveProject",
        "    $env:HFSS_SOLVE_RESULTS_DIR = $ResultsDir",
        "    $env:HFSS_BUILD_LOG = $BuildLog",
        "    $env:HFSS_PORT_MANIFEST = $PortManifest",
        "    $env:HFSS_EXPORT_MANIFEST = $ExportManifest",
        "    New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null",
        "    try {",
        "        & $PythonCommand $BuildScript",
        "        if ($LASTEXITCODE -ne 0) { throw \"build failed with exit code $LASTEXITCODE\" }",
        "        & $PythonCommand $SolveScript",
        "        if ($LASTEXITCODE -ne 0) { throw \"solve/export failed with exit code $LASTEXITCODE\" }",
        "        $s8pCount = @(Get-ChildItem -Path $ResultsDir -Filter '*.s8p' -Recurse -ErrorAction SilentlyContinue).Count",
        "        $manifestExists = Test-Path $ExportManifest",
        "        if (($s8pCount -lt 1) -or (-not $manifestExists)) { throw 'Variant completed but did not produce both .s8p and export manifest.' }",
        "        return [PSCustomObject]@{ name=$Name; status='PASS'; s8p_count=$s8pCount; export_manifest=$ExportManifest; error='' }",
        "    } catch {",
        "        Write-Host \"V67 variant failed: $Name :: $($_.Exception.Message)\"",
        "        return [PSCustomObject]@{ name=$Name; status='FAIL'; s8p_count=0; export_manifest=$ExportManifest; error=$_.Exception.Message }",
        "    }",
        "}",
        "",
    ]
    for variant in summary["variants"]:
        env_lines = "; ".join(f"{key}='{value}'" for key, value in sorted(variant["env"].items()))
        lines.extend(
            [
                "$VariantResults += Run-V67Variant `",
                f"    -Name '{variant['name']}' `",
                f"    -Payload '{_windows_path(Path(variant['payload_json']))}' `",
                f"    -SavePath '{_windows_path(Path(variant['hfss_save_path']))}' `",
                f"    -SolveProject '{_windows_path(Path(variant['hfss_solve_project']))}' `",
                f"    -ResultsDir '{_windows_path(Path(variant['hfss_results_dir']))}' `",
                f"    -BuildLog '{_windows_path(Path(variant['hfss_build_log']))}' `",
                f"    -PortManifest '{_windows_path(Path(variant['hfss_port_manifest']))}' `",
                f"    -ExportManifest '{_windows_path(Path(variant['hfss_export_manifest']))}' `",
                f"    -BuildScript '{_windows_path(Path(variant['build_script']))}' `",
                f"    -SolveScript '{_windows_path(Path(variant['solve_script']))}' `",
                f"    -EnvMap @{{ {env_lines} }}",
                "",
            ]
        )
    lines.extend(
        [
            "$passCount = @($VariantResults | Where-Object { $_.status -eq 'PASS' }).Count",
            "$overall = if ($passCount -gt 0) { 'PARTIAL_OR_FULL_EXPORTS_READY_FOR_POSTRUN' } else { 'FAIL_NO_EXPORTS' }",
            "[PSCustomObject]@{ generated_utc=(Get-Date).ToUniversalTime().ToString('s') + 'Z'; overall_status=$overall; pass_count=$passCount; variants=$VariantResults } | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $StatusJson",
            "Write-Host \"V67 status JSON: $StatusJson\"",
            "Stop-Transcript",
            "if ($passCount -lt 1) { exit 2 }",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_cmd_launcher(runner_path: Path) -> str:
    return (
        "@echo off\r\n"
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
        f"\"{_windows_path(runner_path)}\"\r\n"
    )


def _render_postrun_script(summary: dict[str, Any], args: argparse.Namespace) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"REPO_ROOT={_shell_quote(str(REPO_ROOT))}",
        'PYTHON="${REPO_ROOT}/.venv/bin/python"',
        'if [ ! -x "$PYTHON" ]; then PYTHON="python3"; fi',
        "",
    ]
    for variant in summary["variants"]:
        lines.extend(
            [
                f"echo '== postrun {variant['name']} ==' ",
                '"$PYTHON" "${REPO_ROOT}/scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py" \\',
                f"  --aedt-packet-summary {_shell_quote(variant['single_variant_packet_summary'])} \\",
                f"  --hfss-results-dir {_shell_quote(variant['hfss_results_dir'])} \\",
                f"  --out-dir {_shell_quote(variant['postrun_out_dir'])} \\",
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
    gate = summary["v66_gate_status"]
    response = summary["v67_diagnosis_response"]
    lines = [
        "# HFSS V67 Material/Mesh Calibration Plan",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Source V66 plan: `{summary['v66_plan_summary']}`",
        f"- Historical pass count inherited from V66: `{gate.get('historical_pass_count')}`",
        f"- Best 15 GHz inherited worst error: `{_fmt(gate.get('best_target15_worst_percent_error'))}%`",
        "",
        "## Why V67 Exists",
        "",
        f"- Primary target: {response['primary_target']}",
    ]
    for reason in response["why_v67_exists"]:
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## V67 Variants",
            "",
            "| Variant | Final candidate | Purpose | Key controls |",
            "| --- | --- | --- | --- |",
        ]
    )
    keys = [
        "HFSS_CONDUCTOR_SOLVE_INSIDE",
        "HFSS_DIELECTRIC_CONDUCTIVITY_MODE",
        "HFSS_UNITE_STRATEGY",
        "HFSS_SETUP_MAX_DELTA_S",
        "HFSS_SETUP_MAX_PASSES",
        "HFSS_SETUP_BASIS_ORDER",
        "HFSS_SKIP_PIN_CONDUCTORS",
        "HFSS_AIR_MARGIN_UM",
        "HFSS_RADIATION_MARGIN_UM",
    ]
    for variant in summary["variants"]:
        controls = ", ".join(f"{key}={variant['env'].get(key, '')}" for key in keys if key in variant["env"])
        lines.append(
            f"| `{variant['name']}` | `{variant['final_acceptance_candidate']}` | {variant['purpose']} | `{controls}` |"
        )
    lines.extend(
        [
            "",
            "## Execution",
            "",
            f"- Windows resilient runner: `{summary['artifacts']['windows_runner']}`",
            f"- CMD launcher: `{summary['artifacts']['cmd_launcher']}`",
            f"- Postrun validator: `{summary['artifacts']['postrun_script']}`",
            "",
            "## Acceptance Rule",
            "",
            "- HFSS export must be `.s8p`, 8 ports, 5-60 GHz, 1.0 GHz step, 56 points.",
            "- Final acceptance still requires the same exported HFSS `.s8p` to match EMX with Lp/Ls/Q/K/Kw error <= 10%.",
            "- The skip-pin variant is diagnostic only; it can explain fixture sensitivity but cannot unlock production data by itself.",
            "- Million-sample EMX generation remains locked until the final EMX/HFSS gate passes.",
        ]
    )
    return "\n".join(lines) + "\n"


def _is_final_contract(args: argparse.Namespace) -> bool:
    return (
        abs(float(args.compare_start_ghz) - 5.0) < 1e-9
        and abs(float(args.compare_stop_ghz) - 60.0) < 1e-9
        and abs(float(args.expected_frequency_step_ghz) - 0.5) < 1e-9
        and int(args.expected_frequency_points) == 111
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _check(name: str, condition: bool, detail: Any) -> Check:
    return Check("PASS" if condition else "FAIL", name, _detail(detail))


def _detail(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _path_get(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    cur: Any = payload
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _path_get_float(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    return _to_float(_path_get(payload, keys))


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _fmt(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "NA"
    return f"{number:.4g}"


def _windows_path(path: Path) -> str:
    text = str(path)
    if text.startswith("/Users/"):
        return "\\\\Mac\\Home\\" + text[len("/home/researcher/") :].replace("/", "\\")
    return text.replace("/", "\\")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
