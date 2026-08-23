#!/usr/bin/env python3
"""Build a continue-on-error Windows runner for the HFSS V66 sweep.

The original V66 runner is strict and stops on the first PowerShell/Python
error.  That is useful for a clean all-variant run, but less useful for a
diagnostic sweep where a single HFSS setting can fail while other variants may
still produce a useful `.s8p` for the EMX/HFSS gate.  This generator creates a
second Windows runner that executes each variant independently, records
per-variant status, and continues after failures.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "hfss_v66_calibration_plan_summary.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    variants = [item for item in plan.get("variants") or [] if isinstance(item, dict)]

    windows_runner = out_dir / "run_hfss_v66_calibration_resilient.windows.ps1"
    cmd_launcher = out_dir / "run_hfss_v66_calibration_resilient.windows.cmd"
    summary_path = out_dir / "hfss_v66_resilient_runner_packet_summary.json"
    report_path = out_dir / "HFSS_V66_RESILIENT_RUNNER_PACKET_CN.md"
    checks = _checks(plan_path, plan, variants)
    overall_status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    decision = "USE_RESILIENT_RUNNER_FOR_UNATTENDED_HFSS_SWEEP" if overall_status == "PASS" else "FIX_RESILIENT_RUNNER_INPUTS"

    if overall_status == "PASS":
        windows_runner.write_text(_render_windows_runner(out_dir, variants), encoding="utf-8")
        cmd_launcher.write_text(_render_cmd_launcher(windows_runner), encoding="utf-8")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "plan_summary": str(plan_path),
        "out_dir": str(out_dir),
        "variant_count": len(variants),
        "windows_runner": str(windows_runner),
        "cmd_launcher": str(cmd_launcher),
        "status_dir": str(out_dir / "resilient_run_status"),
        "checks": checks,
        "method_notes": [
            "This runner does not change HFSS geometry, ports, or frequency payloads.",
            "Each V66 variant is executed in its own try/catch block and failures do not stop later variants.",
            "A runner-level PASS only means at least one variant exported .s8p and an export manifest; EMX/HFSS 10% validation is still handled by the postrun gate.",
            "The strict visible runner remains available when all-variant completion is required.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"windows_runner={windows_runner}")
    print(f"cmd_launcher={cmd_launcher}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", default=str(DEFAULT_PLAN))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _checks(plan_path: Path, plan: dict[str, Any], variants: list[dict[str, Any]]) -> list[dict[str, str]]:
    checks = [
        _check("V66 plan exists", plan_path.is_file(), str(plan_path)),
        _check("V66 plan status PASS", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check("V66 variants present", bool(variants), f"variants={len(variants)}"),
    ]
    required_fields = [
        "name",
        "variant_dir",
        "payload_json",
        "hfss_save_path",
        "hfss_solve_project",
        "hfss_results_dir",
        "hfss_build_log",
        "hfss_port_manifest",
        "hfss_export_manifest",
        "build_script",
        "solve_script",
        "env",
    ]
    for variant in variants:
        name = str(variant.get("name") or "unnamed")
        for field in required_fields:
            checks.append(_check(f"{name} has {field}", bool(variant.get(field)), str(variant.get(field) or "")))
        for field in ("payload_json", "build_script", "solve_script"):
            value = variant.get(field)
            checks.append(_check(f"{name} {field} exists", bool(value) and Path(str(value)).expanduser().is_file(), str(value or "")))
    return checks


def _render_windows_runner(out_dir: Path, variants: list[dict[str, Any]]) -> str:
    root = _windows_path(out_dir)
    lines = [
        "# Auto-generated resilient HFSS V66 calibration sweep.",
        "# This runner continues after individual variant failures and records per-variant status.",
        "$ErrorActionPreference = 'Continue'",
        f"$Root = '{root}'",
        "$StatusDir = Join-Path $Root 'resilient_run_status'",
        "$RunId = Get-Date -Format 'yyyyMMdd_HHmmss'",
        "$StatusPath = Join-Path $StatusDir 'hfss_v66_resilient_run_status.json'",
        "$TranscriptPath = Join-Path $StatusDir (\"hfss_v66_resilient_run_{0}.transcript.txt\" -f $RunId)",
        "New-Item -ItemType Directory -Force -Path $StatusDir | Out-Null",
        "",
        "function Write-V66Json {",
        "    param([string]$Path, [object]$Payload)",
        "    ($Payload | ConvertTo-Json -Depth 12) | Set-Content -Path $Path -Encoding UTF8",
        "}",
        "",
        "function Get-V66VariantExports {",
        "    param([string]$VariantDir)",
        "    $S8pFiles = @(Get-ChildItem -Path $VariantDir -Recurse -Filter '*.s8p' -ErrorAction SilentlyContinue)",
        "    $ManifestFiles = @(Get-ChildItem -Path $VariantDir -Recurse -Filter '*export*manifest*.json' -ErrorAction SilentlyContinue)",
        "    return [ordered]@{ S8pFiles = $S8pFiles; ManifestFiles = $ManifestFiles }",
        "}",
        "",
        "function Run-V66Variant {",
        "    param(",
        "        [string]$Name,",
        "        [string]$VariantDir,",
        "        [string]$Payload,",
        "        [string]$SavePath,",
        "        [string]$SolveProject,",
        "        [string]$ResultsDir,",
        "        [string]$BuildLog,",
        "        [string]$PortManifest,",
        "        [string]$ExportManifest,",
        "        [string]$BuildScript,",
        "        [string]$SolveScript,",
        "        [hashtable]$EnvMap",
        "    )",
        "    $StartedUtc = (Get-Date).ToUniversalTime().ToString('o')",
        "    $ErrorMessage = ''",
        "    $Status = 'FAIL'",
        "    Write-Host \"== V66 resilient variant: $Name ==\"",
        "    try {",
        "        New-Item -ItemType Directory -Force -Path $VariantDir | Out-Null",
        "        foreach ($Key in $EnvMap.Keys) {",
        "            [Environment]::SetEnvironmentVariable($Key, [string]$EnvMap[$Key], 'Process')",
        "        }",
        "        $env:HFSS_S8P_PAYLOAD = $Payload",
        "        $env:HFSS_SAVE_PATH = $SavePath",
        "        $env:HFSS_SOLVE_PROJECT = $SolveProject",
        "        $env:HFSS_SOLVE_RESULTS_DIR = $ResultsDir",
        "        $env:HFSS_BUILD_LOG = $BuildLog",
        "        $env:HFSS_PORT_MANIFEST = $PortManifest",
        "        $env:HFSS_EXPORT_MANIFEST = $ExportManifest",
        "        & 'python' $BuildScript",
        "        if ($LASTEXITCODE -ne 0) { throw \"build_hfss_s8p_from_payload.py exited with code $LASTEXITCODE\" }",
        "        & 'python' $SolveScript",
        "        if ($LASTEXITCODE -ne 0) { throw \"solve_export_hfss_s8p.py exited with code $LASTEXITCODE\" }",
        "        $Exports = Get-V66VariantExports -VariantDir $VariantDir",
        "        if (@($Exports.S8pFiles).Count -lt 1 -or @($Exports.ManifestFiles).Count -lt 1) {",
        "            throw 'Variant completed but did not produce both .s8p and export manifest.'",
        "        }",
        "        $Status = 'PASS'",
        "    }",
        "    catch {",
        "        $ErrorMessage = $_.Exception.Message",
        "        Write-Warning \"V66 variant failed: $Name :: $ErrorMessage\"",
        "    }",
        "    $Exports = Get-V66VariantExports -VariantDir $VariantDir",
        "    $Result = [ordered]@{",
        "        name = $Name",
        "        status = $Status",
        "        started_utc = $StartedUtc",
        "        finished_utc = (Get-Date).ToUniversalTime().ToString('o')",
        "        variant_dir = $VariantDir",
        "        exported_s8p_count = @($Exports.S8pFiles).Count",
        "        export_manifest_count = @($Exports.ManifestFiles).Count",
        "        exported_s8p_paths = @($Exports.S8pFiles | ForEach-Object { $_.FullName })",
        "        export_manifest_paths = @($Exports.ManifestFiles | ForEach-Object { $_.FullName })",
        "        error = $ErrorMessage",
        "    }",
        "    Write-V66Json -Path (Join-Path $StatusDir (\"variant_{0}_{1}.json\" -f $RunId, $Name)) -Payload $Result",
        "    return $Result",
        "}",
        "",
        "Start-Transcript -Path $TranscriptPath -Append | Out-Null",
        "$VariantResults = @()",
        "try {",
    ]
    for variant in variants:
        env_items = [f"        '{key}' = '{_ps_escape(str(value))}'" for key, value in sorted((variant.get("env") or {}).items())]
        lines.extend(
            [
                "    $VariantResults += Run-V66Variant `",
                f"        -Name '{_ps_escape(str(variant['name']))}' `",
                f"        -VariantDir '{_windows_path(Path(str(variant['variant_dir'])))}' `",
                f"        -Payload '{_windows_path(Path(str(variant['payload_json'])))}' `",
                f"        -SavePath '{_windows_path(Path(str(variant['hfss_save_path'])))}' `",
                f"        -SolveProject '{_windows_path(Path(str(variant['hfss_solve_project'])))}' `",
                f"        -ResultsDir '{_windows_path(Path(str(variant['hfss_results_dir'])))}' `",
                f"        -BuildLog '{_windows_path(Path(str(variant['hfss_build_log'])))}' `",
                f"        -PortManifest '{_windows_path(Path(str(variant['hfss_port_manifest'])))}' `",
                f"        -ExportManifest '{_windows_path(Path(str(variant['hfss_export_manifest'])))}' `",
                f"        -BuildScript '{_windows_path(Path(str(variant['build_script'])))}' `",
                f"        -SolveScript '{_windows_path(Path(str(variant['solve_script'])))}' `",
                "        -EnvMap @{",
                *env_items,
                "        }",
                "",
            ]
        )
    lines.extend(
        [
            "    $PassCount = @($VariantResults | Where-Object { $_.status -eq 'PASS' }).Count",
            "    $FailCount = @($VariantResults | Where-Object { $_.status -ne 'PASS' }).Count",
            "    $OverallStatus = if ($PassCount -gt 0) { 'PASS' } else { 'FAIL' }",
            "    $Decision = if ($PassCount -gt 0 -and $FailCount -gt 0) { 'HFSS_RESILIENT_RUNNER_PARTIAL_EXPORTS_RUN_POSTRUN_GATE' } elseif ($PassCount -gt 0) { 'HFSS_RESILIENT_RUNNER_EXPORTS_READY_RUN_POSTRUN_GATE' } else { 'HFSS_RESILIENT_RUNNER_NO_EXPORTS' }",
            "    $Payload = [ordered]@{",
            "        generated_utc = (Get-Date).ToUniversalTime().ToString('o')",
            "        run_id = $RunId",
            "        overall_status = $OverallStatus",
            "        decision = $Decision",
            f"        expected_variant_count = {len(variants)}",
            "        pass_count = $PassCount",
            "        fail_count = $FailCount",
            "        transcript = $TranscriptPath",
            "        variant_results = $VariantResults",
            "    }",
            "    Write-V66Json -Path $StatusPath -Payload $Payload",
            "    Write-Host \"== V66 resilient runner finished: $OverallStatus / $Decision ==\"",
            "    if ($PassCount -gt 0) { exit 0 } else { exit 2 }",
            "}",
            "finally {",
            "    try { Stop-Transcript | Out-Null } catch {}",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def _render_cmd_launcher(windows_runner: Path) -> str:
    return (
        "@echo off\r\n"
        "set SCRIPT=%~dp0run_hfss_v66_calibration_resilient.windows.ps1\r\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"\r\n'
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS V66 Resilient Runner Packet",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Variant count: `{summary['variant_count']}`",
        f"- Windows runner: `{summary['windows_runner']}`",
        f"- CMD launcher: `{summary['cmd_launcher']}`",
        f"- Status dir: `{summary['status_dir']}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {item['status']}: {item['name']} - {item['detail']}" for item in summary["checks"])
    lines.extend(["", "## Method Notes", ""])
    lines.extend(f"- {item}" for item in summary["method_notes"])
    return "\n".join(lines) + "\n"


def _windows_path(path: Path) -> str:
    text = str(path.expanduser().resolve())
    home = str(Path.home())
    if text == home:
        return r"\\Mac\Home"
    if text.startswith(home + "/"):
        return r"\\Mac\Home" + "\\" + text[len(home) + 1 :].replace("/", "\\")
    return text.replace("/", "\\")


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


if __name__ == "__main__":
    raise SystemExit(main())
