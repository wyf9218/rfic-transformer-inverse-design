#!/usr/bin/env python3
"""Plan the next HFSS V66 calibration sweep from strict EMX/HFSS recompare data.

This script does not run HFSS. It reads the unified recompare summary, confirms
that the EMX/HFSS gate is still failing, and writes a controlled V66 diagnostic
execution packet that focuses on the observed Lp/Ls/Q gap while preserving the
approved S8P port map.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECOMPARE = (
    PROJECT_ROOT
    / "outputs"
    / "existing_hfss_s8p_strict_recompare_current"
    / "existing_hfss_s8p_strict_recompare_summary.json"
)
DEFAULT_V65_PLAN = (
    PROJECT_ROOT
    / "outputs"
    / "hfss_lp_ls_reference_sweep_plan_current"
    / "hfss_lp_ls_reference_sweep_plan_summary.json"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


V66_VARIANTS: list[dict[str, Any]] = [
    {
        "name": "v66a_best_marker_reference_bbox",
        "purpose": "Rebuild the best historical marker direction with the current audited payload and local ground bbox reference.",
        "env": {
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
        },
    },
    {
        "name": "v66b_pyaedt_terminal_reference",
        "purpose": "Test whether AEDT terminal reference handling fixes the Lp/Ls/Q depression without changing geometry.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "1",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
        },
    },
    {
        "name": "v66c_all_m5_reference",
        "purpose": "Use the whole M5 shield as the reference conductor to diagnose whether local M5 reference selection is over-shorting flux.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_UNITE_STRATEGY": "all_by_metal",
            "HFSS_UNITE_CONNECTED_M5": "1",
            "HFSS_PORT_REFERENCE_MODE": "all_m5",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "0",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
        },
    },
    {
        "name": "v66d_port_top_z_reference",
        "purpose": "Move both signal and ground integration-line z to conductor tops; isolates vertical port-sheet z placement.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "top",
            "HFSS_PORT_GROUND_Z_MODE": "top",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
        },
    },
    {
        "name": "v66e_port_mid_z_reference",
        "purpose": "Move integration-line z to conductor midplanes; tests whether payload bottom/top mismatch depresses extracted inductance.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox_smallest",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "mid",
            "HFSS_PORT_GROUND_Z_MODE": "mid",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
        },
    },
    {
        "name": "v66f_port_deembed_marker",
        "purpose": "Enable de-embedding while keeping the best local-reference setup; diagnoses port fixture length error.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "1",
            "HFSS_PORT_MODE_RENORM_IMP": "1",
            "HFSS_PORT_RENORM_IMPEDANCE": "50ohm",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "ignore",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
        },
    },
    {
        "name": "v66g_loss_tangent_stack",
        "purpose": "Reintroduce lossy dielectric stack after port-reference controls; checks if Q mismatch is stack-loss dominated.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "finite",
            "HFSS_UNITE_STRATEGY": "connected_by_bbox",
            "HFSS_UNITE_CONNECTED_M5": "0",
            "HFSS_PORT_REFERENCE_MODE": "local_ground_bbox",
            "HFSS_REQUIRE_LOCAL_GROUND_REFERENCE": "1",
            "HFSS_USE_PYAEDT_REFERENCE_PORT": "0",
            "HFSS_PORT_SIGNAL_Z_MODE": "payload",
            "HFSS_PORT_GROUND_Z_MODE": "payload",
            "HFSS_PORT_DEEMBED": "0",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE": "loss_tangent",
            "HFSS_AIR_MARGIN_UM": "500",
            "HFSS_RADIATION_MARGIN_UM": "700",
        },
    },
    {
        "name": "v66h_m5_perfecte_reference",
        "purpose": "Force the M5 shield/reference to ideal E boundary; diagnoses finite M5 loss/current return modeling.",
        "env": {
            "HFSS_M5_SHIELD_BOUNDARY": "perfecte",
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
        },
    },
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    recompare_path = Path(args.recompare_summary).expanduser().resolve()
    v65_plan_path = Path(args.v65_plan_summary).expanduser().resolve()
    recompare = _read_json(recompare_path)
    v65_plan = _read_json(v65_plan_path)
    source_step = _first_windows_step(v65_plan)
    build_script = Path(str(source_step.get("build_script", ""))).expanduser().resolve()
    solve_script = Path(str(source_step.get("solve_script", ""))).expanduser().resolve()
    evaluation = str(source_step.get("evaluation") or args.evaluation)
    payload_json = build_script.parent / "hfss_s8p_build_payload.json"

    checks = _checks(recompare_path, recompare, v65_plan_path, v65_plan, build_script, solve_script, payload_json)
    variants = [
        _variant_record(item, index, out_dir, evaluation, build_script, solve_script, payload_json, args)
        for index, item in enumerate(V66_VARIANTS, start=1)
    ]
    for record in variants:
        _write_variant_packet(record)

    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    diagnosis = _diagnosis(recompare)
    decision = "RUN_V66_HFSS_DIAGNOSTIC_SWEEP_BEFORE_FULL_VALIDATION" if overall_status == "PASS" else "FIX_V66_PLAN_INPUTS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "recompare_summary": str(recompare_path),
        "v65_plan_summary": str(v65_plan_path),
        "out_dir": str(out_dir),
        "evaluation": evaluation,
        "source_build_script": str(build_script),
        "source_solve_script": str(solve_script),
        "source_payload_json": str(payload_json),
        "gate_status": {
            "historical_candidate_count": int(recompare.get("candidate_count") or 0),
            "historical_pass_count": int(recompare.get("pass_count") or 0),
            "best_full_band_worst_percent_error": _path_get_float(recompare, ("best", "worst_percent_error")),
            "best_full_band_worst_metric": _path_get(recompare, ("best", "worst_metric")),
            "best_target15_worst_percent_error": _path_get_float(recompare, ("target15_best", "target15_worst_percent_error")),
            "best_target15_worst_metric": _path_get(recompare, ("target15_best", "target15_worst_metric")),
            "best_target15_core_percent_errors": _path_get(recompare, ("target15_best", "target15_core_percent_errors")) or {},
        },
        "diagnosis": diagnosis,
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
            "final_acceptance_candidate": (
                abs(float(args.compare_start_ghz) - 5.0) < 1e-9
                and abs(float(args.compare_stop_ghz) - 60.0) < 1e-9
                and abs(float(args.expected_frequency_step_ghz) - 0.5) < 1e-9
                and int(args.expected_frequency_points) == 111
            ),
        },
        "checks": [check.as_dict() for check in checks],
        "artifacts": {
            "windows_runner": str(out_dir / "run_hfss_v66_calibration.windows.ps1"),
            "postrun_script": str(out_dir / "postrun_validate_hfss_v66_calibration.sh"),
            "report": str(out_dir / "HFSS_V66_CALIBRATION_PLAN_CN.md"),
        },
        "limitations": [
            "This script plans HFSS runs only; it does not run HFSS or create measured `.s8p` evidence.",
            "V66 is a diagnostic calibration sweep. Final acceptance still requires a full 5-60 GHz exported HFSS `.s8p` with EMX/HFSS Lp/Ls/Q/K/Kw error <= 10%.",
            "Million-sample EMX generation remains locked until the final EMX/HFSS gate passes.",
        ],
    }
    summary_path = out_dir / "hfss_v66_calibration_plan_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "HFSS_V66_CALIBRATION_PLAN_CN.md").write_text(_render_report(summary), encoding="utf-8")
    (out_dir / "run_hfss_v66_calibration.windows.ps1").write_text(_render_windows_runner(summary), encoding="utf-8")
    postrun_path = out_dir / "postrun_validate_hfss_v66_calibration.sh"
    postrun_path.write_text(_render_postrun_script(summary, args), encoding="utf-8")
    postrun_path.chmod(0o755)

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={out_dir / 'HFSS_V66_CALIBRATION_PLAN_CN.md'}")
    print(f"windows_runner={out_dir / 'run_hfss_v66_calibration.windows.ps1'}")
    print(f"postrun={postrun_path}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recompare-summary", default=str(DEFAULT_RECOMPARE))
    parser.add_argument("--v65-plan-summary", default=str(DEFAULT_V65_PLAN))
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


def _checks(
    recompare_path: Path,
    recompare: dict[str, Any],
    v65_plan_path: Path,
    v65_plan: dict[str, Any],
    build_script: Path,
    solve_script: Path,
    payload_json: Path,
) -> list[Check]:
    pass_count = int(recompare.get("pass_count") or 0)
    target_best = _path_get_float(recompare, ("target15_best", "target15_worst_percent_error"))
    return [
        _check("strict recompare summary exists", recompare_path.is_file(), str(recompare_path)),
        _check("strict recompare scanned HFSS candidates", int(recompare.get("candidate_count") or 0) > 0, str(recompare.get("candidate_count"))),
        _check("strict recompare confirms no passing historical HFSS", pass_count == 0, f"pass_count={pass_count}"),
        _check("strict recompare has 15 GHz best marker", target_best is not None and math.isfinite(target_best), str(target_best)),
        _check("V65 plan summary exists", v65_plan_path.is_file(), str(v65_plan_path)),
        _check("V65 plan is ready or waiting", str(v65_plan.get("overall_status")) in {"PASS", "WAITING_FOR_DIAGNOSTIC_HFSS"}, str(v65_plan.get("overall_status"))),
        _check("source build script exists", build_script.is_file(), str(build_script)),
        _check("source solve script exists", solve_script.is_file(), str(solve_script)),
        _check("source payload JSON exists", payload_json.is_file(), str(payload_json)),
    ]


def _diagnosis(recompare: dict[str, Any]) -> dict[str, Any]:
    target_errors = _path_get(recompare, ("target15_best", "target15_core_percent_errors")) or {}
    lp = _to_float(target_errors.get("lp_nh"))
    ls = _to_float(target_errors.get("ls_nh"))
    q = _to_float(target_errors.get("q"))
    k = _to_float(target_errors.get("k"))
    geometry_coupling_likely_ok = k is not None and k <= 10.0
    lq_gap = any(value is not None and value > 10.0 for value in (lp, ls, q))
    return {
        "best_target15_errors": target_errors,
        "geometry_coupling_likely_ok": geometry_coupling_likely_ok,
        "lp_ls_q_gap_present": lq_gap,
        "primary_root_cause_hypothesis": (
            "HFSS stack/reference-ground/port treatment mismatch, not EMX Touchstone format or differential port order"
            if geometry_coupling_likely_ok and lq_gap
            else "Needs additional HFSS diagnostic evidence before assigning root cause"
        ),
        "reasoning": [
            "K/Kw near the 15 GHz marker is much closer than Lp/Ls/Q, so relative coil coupling direction is not the dominant error.",
            "Lp/Ls/Q remain outside the 10% gate, which points to conductor stack, shield-return reference, port integration line, de-embedding, or dielectric loss settings.",
            "V66 variants vary one HFSS modeling assumption at a time while keeping the EMX sample, port order, and differential pair convention fixed.",
        ],
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
    results_dir = variant_dir / "hfss_solve_export_results"
    packet_summary = variant_dir / "hfss_v66_single_variant_packet_summary.json"
    variant_payload_json = variant_dir / "hfss_s8p_build_payload.json"
    variant_build_script = variant_dir / "build_hfss_s8p_from_payload.py"
    variant_solve_script = variant_dir / "solve_export_hfss_s8p.py"
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
        "variant_dir": str(variant_dir),
        "hfss_results_dir": str(results_dir),
        "hfss_save_path": str(variant_dir / f"{evaluation}_{name}.aedt"),
        "hfss_solve_project": str(variant_dir / f"{evaluation}_{name}_solve.aedt"),
        "hfss_build_log": str(variant_dir / "hfss_s8p_build.log"),
        "hfss_port_manifest": str(variant_dir / "hfss_s8p_build_port_manifest.json"),
        "hfss_export_manifest": str(variant_dir / "hfss_s8p_export_manifest.json"),
        "build_script": str(variant_build_script),
        "solve_script": str(variant_solve_script),
        "payload_json": str(variant_payload_json),
        "source_build_script": str(build_script),
        "source_solve_script": str(solve_script),
        "source_payload_json": str(payload_json),
        "patched_frequency_grid": patched_frequency_grid,
        "single_variant_packet_summary": str(packet_summary),
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
    if not payload:
        payload = {}
    payload["frequency_grid"] = dict(record["patched_frequency_grid"])
    payload.setdefault("hfss", {})
    payload["hfss"]["setup_name"] = payload["hfss"].get("setup_name") or "Setup_15GHz"
    payload["hfss"]["sweep_name"] = "Sweep_5_60_1p0"
    payload.setdefault("v66_patch", {})
    payload["v66_patch"] = {
        "source_payload_json": record["source_payload_json"],
        "reason": "V66 final-gate HFSS export must match EMX .s8p contract: 5-60 GHz, 1.0 GHz, 56 points.",
        "variant": record["name"],
        "patched_frequency_grid": dict(record["patched_frequency_grid"]),
    }
    Path(record["payload_json"]).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    packet = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "decision": "HFSS_V66_SINGLE_VARIANT_READY_FOR_POSTRUN_AFTER_EXPORT",
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


def _render_windows_runner(summary: dict[str, Any]) -> str:
    lines = [
        "# Auto-generated HFSS V66 diagnostic calibration sweep. Run in Windows with HFSS/PyAEDT available.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for variant in summary["variants"]:
        lines.append(f"Write-Host '== {variant['name']} =='")
        for key, value in variant["env"].items():
            lines.append(f"$env:{key} = '{value}'")
        lines.extend(
            [
                f"New-Item -ItemType Directory -Force -Path '{_windows_path(Path(variant['variant_dir']))}' | Out-Null",
                f"$env:HFSS_S8P_PAYLOAD = '{_windows_path(Path(variant['payload_json']))}'",
                f"$env:HFSS_SAVE_PATH = '{_windows_path(Path(variant['hfss_save_path']))}'",
                f"$env:HFSS_SOLVE_PROJECT = '{_windows_path(Path(variant['hfss_solve_project']))}'",
                f"$env:HFSS_SOLVE_RESULTS_DIR = '{_windows_path(Path(variant['hfss_results_dir']))}'",
                f"$env:HFSS_BUILD_LOG = '{_windows_path(Path(variant['hfss_build_log']))}'",
                f"$env:HFSS_PORT_MANIFEST = '{_windows_path(Path(variant['hfss_port_manifest']))}'",
                f"$env:HFSS_EXPORT_MANIFEST = '{_windows_path(Path(variant['hfss_export_manifest']))}'",
                f"& 'python' '{_windows_path(Path(variant['build_script']))}'",
                f"& 'python' '{_windows_path(Path(variant['solve_script']))}'",
                "",
            ]
        )
    return "\n".join(lines)


def _render_postrun_script(summary: dict[str, Any], args: argparse.Namespace) -> str:
    repo = REPO_ROOT
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"REPO_ROOT={_shell_quote(str(repo))}",
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
    gate = summary["gate_status"]
    diagnosis = summary["diagnosis"]
    lines = [
        "# HFSS V66 Calibration Plan",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Historical HFSS candidates: `{gate['historical_candidate_count']}`",
        f"- Historical pass count: `{gate['historical_pass_count']}`",
        f"- Best full-band worst error: `{_fmt(gate['best_full_band_worst_percent_error'])}%`",
        f"- Best 15 GHz marker worst error: `{_fmt(gate['best_target15_worst_percent_error'])}%`",
        "",
        "## Diagnosis",
        "",
        f"- Primary hypothesis: {diagnosis['primary_root_cause_hypothesis']}",
        f"- Geometry/coupling likely OK: `{diagnosis['geometry_coupling_likely_ok']}`",
        f"- Lp/Ls/Q gap present: `{diagnosis['lp_ls_q_gap_present']}`",
        "",
        "15 GHz best-marker errors:",
        "",
    ]
    for key, value in (diagnosis.get("best_target15_errors") or {}).items():
        lines.append(f"- `{key}`: `{_fmt(value)}%`")
    lines.extend(
        [
            "",
            "## V66 Variants",
            "",
            "| Variant | Purpose | Key controls |",
            "| --- | --- | --- |",
        ]
    )
    for variant in summary["variants"]:
        keys = [
            "HFSS_PORT_REFERENCE_MODE",
            "HFSS_USE_PYAEDT_REFERENCE_PORT",
            "HFSS_PORT_SIGNAL_Z_MODE",
            "HFSS_PORT_GROUND_Z_MODE",
            "HFSS_PORT_DEEMBED",
            "HFSS_DIELECTRIC_CONDUCTIVITY_MODE",
            "HFSS_M5_SHIELD_BOUNDARY",
        ]
        controls = ", ".join(f"{key}={variant['env'].get(key, '')}" for key in keys if key in variant["env"])
        lines.append(f"| `{variant['name']}` | {variant['purpose']} | `{controls}` |")
    lines.extend(
        [
            "",
            "## Execution",
            "",
            f"- Windows runner: `{summary['artifacts']['windows_runner']}`",
            f"- Postrun validator: `{summary['artifacts']['postrun_script']}`",
            "- Each V66 variant is self-contained: it has its own patched `hfss_s8p_build_payload.json`, copied build script, and copied solve script.",
            "- The patched payload is used by both HFSS build and HFSS solve/export so the exported `.s8p` frequency contract matches the postrun validator.",
            "",
            "## Postrun Validation Contract",
            "",
            f"- HFSS Touchstone suffix: `{summary['postrun_validation_contract']['hfss_touchstone_suffix']}`",
            f"- Ports: `{summary['postrun_validation_contract']['expected_ports']}`",
            f"- Frequency: `{summary['postrun_validation_contract']['compare_start_ghz']:g}-{summary['postrun_validation_contract']['compare_stop_ghz']:g} GHz`",
            f"- Step: `{summary['postrun_validation_contract']['expected_frequency_step_ghz']:g} GHz`",
            f"- Points: `{summary['postrun_validation_contract']['expected_frequency_points']}`",
            f"- Required metrics: `{', '.join(summary['postrun_validation_contract']['required_metrics'])}`",
            f"- Max percent error: `{summary['postrun_validation_contract']['max_percent_error']:g}%`",
            f"- Final acceptance candidate: `{summary['postrun_validation_contract']['final_acceptance_candidate']}`",
            "",
            "Acceptance rule remains unchanged: do not unlock million-sample EMX generation until a final 5-60 GHz HFSS `.s8p` passes Lp/Ls/Q/K/Kw <= 10% against EMX.",
        ]
    )
    return "\n".join(lines) + "\n"


def _first_windows_step(plan: dict[str, Any]) -> dict[str, Any]:
    for variant in plan.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        steps = variant.get("windows_steps")
        if isinstance(steps, list) and steps:
            step = steps[0]
            if isinstance(step, dict):
                return step
    return {}


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


def _path_get(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _path_get_float(data: dict[str, Any], path: tuple[str, ...]) -> float | None:
    return _to_float(_path_get(data, path))


def _to_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _fmt(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}"


def _windows_path(path: Path) -> str:
    text = str(path)
    if text.startswith("/home/researcher/"):
        return "\\\\Mac\\Home\\" + text[len("/home/researcher/") :].replace("/", "\\")
    return text.replace("/", "\\")


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
