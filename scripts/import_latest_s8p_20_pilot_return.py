#!/usr/bin/env python3
"""Import the latest 20-sample S8P MARS pilot return and prepare HFSS gates.

This is the local entry point for the guarded ``MARS_S8P_20_AFTER_UNLOCK``
pilot.  It deliberately uses the 20-row pilot contract, not the production
500-row/default contract, so a valid pilot return is not rejected by the wrong
expected-count setting.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "latest_s8p_20_pilot_return_import_current"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    checks: list[Check] = []

    tarball = _resolve_return_tarball(args)
    checks.append(
        Check(
            "PASS" if tarball and tarball.is_file() else "WAITING",
            "20-pilot return tarball",
            str(tarball) if tarball else "not found in configured search roots",
        )
    )

    import_result: dict[str, Any] | None = None
    after_import_result: dict[str, Any] | None = None
    postrun_result: dict[str, Any] | None = None
    if tarball and tarball.is_file():
        import_result = _run_import(tarball, out_dir, args)
        checks.append(_import_check(import_result))
        after_import_result = _maybe_run_after_import(import_result, args)
        if after_import_result is not None:
            checks.append(_subprocess_check("after-import local gates", after_import_result))
        postrun_result = _maybe_run_postrun(import_result, out_dir, args)
        if postrun_result is not None:
            checks.append(_subprocess_check("HFSS postrun S8P comparison", postrun_result))
    else:
        checks.append(Check("WAITING", "return import", "waiting for MARS next_gen_s8p_mars_return_latest.tar.gz"))

    overall_status, decision = _decision(checks, import_result, postrun_result)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "return_tarball": "" if tarball is None else str(tarball),
        "import_result": import_result,
        "after_import_result": after_import_result,
        "postrun_result": postrun_result,
        "checks": [check.as_dict() for check in checks],
        "arguments": {
            "expected_count": int(args.expected_count),
            "expected_jobs": int(args.expected_jobs),
            "expected_ports": int(args.expected_ports),
            "expected_frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "expected_frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "max_percent_error": float(args.max_percent_error),
            "run_after_import": bool(args.run_after_import),
            "hfss_results_dir": "" if not args.hfss_results_dir else str(Path(args.hfss_results_dir).expanduser()),
        },
        "method_notes": [
            "This script does not run EMX, HFSS, ADS, Cadence, or Guacamole.",
            "It imports the already-returned 20-sample MARS pilot with the correct expected-count gate.",
            "Final acceptance still requires at least one final-valid EMX S8P, a matching HFSS S8P export, and <=10% Lp/Ls/Q/Kw comparison.",
        ],
    }
    summary_path = out_dir / "latest_s8p_20_pilot_return_import_summary.json"
    report_path = out_dir / "LATEST_S8P_20_PILOT_RETURN_IMPORT_REPORT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "READY_FOR_HFSS", "WAITING_FOR_HFSS_EXPORT", "WAITING_FOR_RETURN"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--return-tarball", help="Explicit next_gen_s8p_mars_return_latest.tar.gz")
    parser.add_argument("--search-root", action="append", help="Directory to search when --return-tarball is omitted")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-count", type=int, default=20)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--max-touchstone-checks", type=int, default=20)
    parser.add_argument("--max-touchstone-frequency-checks", type=int, default=20)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--run-after-import", action="store_true", help="Run the generated next_gen_s8p_after_import_next_steps.commands.sh")
    parser.add_argument("--hfss-results-dir", help="Directory containing exported HFSS .s8p files for postrun validation")
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _resolve_return_tarball(args: argparse.Namespace) -> Path | None:
    if args.return_tarball:
        return Path(args.return_tarball).expanduser().resolve()
    roots = [Path(item).expanduser().resolve() for item in (args.search_root or [])]
    if not roots:
        roots = [
            PROJECT_ROOT,
            PROJECT_ROOT / "outputs",
            Path.home() / "Downloads",
            Path.home() / "Desktop",
        ]
    patterns = (
        "next_gen_s8p_mars_return_latest.tar.gz",
        "next_gen_s8p_mars_return_20_after_unlock_*.tar.gz",
    )
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            candidates.extend(path for path in root.rglob(pattern) if path.is_file())
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
    return candidates[0].resolve()


def _run_import(tarball: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    import_dir = out_dir / "return_import"
    command = [
        str(Path(args.python).expanduser()),
        str(SCRIPT_DIR / "import_next_gen_s8p_mars_return_package.py"),
        str(tarball),
        "--out-dir",
        str(import_dir),
        "--expected-count",
        str(int(args.expected_count)),
        "--expected-jobs",
        str(int(args.expected_jobs)),
        "--expected-ports",
        str(int(args.expected_ports)),
        "--expected-frequency-start-ghz",
        f"{float(args.expected_frequency_start_ghz):g}",
        "--expected-frequency-stop-ghz",
        f"{float(args.expected_frequency_stop_ghz):g}",
        "--expected-frequency-step-ghz",
        f"{float(args.expected_frequency_step_ghz):g}",
        "--expected-frequency-points",
        str(int(args.expected_frequency_points)),
        "--frequency-tolerance-hz",
        f"{float(args.frequency_tolerance_hz):g}",
        "--max-touchstone-checks",
        str(int(args.max_touchstone_checks)),
        "--max-touchstone-frequency-checks",
        str(int(args.max_touchstone_frequency_checks)),
        "--no-fail-exit",
    ]
    _append_existing_sidecar(command, "--sha256-file", tarball.with_suffix(tarball.suffix + ".sha256"))
    _append_existing_sidecar(command, "--inventory", Path(str(tarball) + ".inventory.json"))
    _append_existing_sidecar(command, "--inventory-report", Path(str(tarball) + ".inventory.md"))
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    summary_path = import_dir / "next_gen_s8p_mars_return_import_summary.json"
    return _completed_record(command, completed, summary_path)


def _append_existing_sidecar(command: list[str], flag: str, path: Path) -> None:
    if path.is_file():
        command.extend([flag, str(path)])


def _maybe_run_after_import(import_result: dict[str, Any] | None, args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.run_after_import or not import_result:
        return None
    summary = import_result.get("summary") if isinstance(import_result.get("summary"), dict) else {}
    script_path_text = ((summary.get("next_steps_result") or {}).get("script_path") or "").strip()
    if not script_path_text:
        return {
            "returncode": 2,
            "command": [],
            "stdout_tail": "",
            "stderr_tail": "next_steps_result.script_path missing; import did not reach local next-gates readiness",
            "summary_path": "",
            "summary": {},
        }
    command = ["bash", script_path_text]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return _completed_record(command, completed, None)


def _maybe_run_postrun(import_result: dict[str, Any] | None, out_dir: Path, args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.hfss_results_dir or not import_result:
        return None
    summary = import_result.get("summary") if isinstance(import_result.get("summary"), dict) else {}
    run_dir = _import_run_dir(summary)
    if run_dir is None:
        return {
            "returncode": 2,
            "command": [],
            "stdout_tail": "",
            "stderr_tail": "could not resolve imported run_dir",
            "summary_path": "",
            "summary": {},
        }
    aedt_summary = (
        run_dir
        / "dataset_quality_gates_s8p_physical_feature"
        / "selected_s8p_hfss_aedt_scripts"
        / "hfss_s8p_aedt_script_packet_summary.json"
    )
    command = [
        str(Path(args.python).expanduser()),
        str(SCRIPT_DIR / "run_s8p_hfss_postrun_validation_from_aedt_packet.py"),
        "--aedt-packet-summary",
        str(aedt_summary),
        "--hfss-results-dir",
        str(Path(args.hfss_results_dir).expanduser().resolve()),
        "--out-dir",
        str(out_dir / "hfss_postrun_validation"),
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--no-fail-exit",
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    summary_path = out_dir / "hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json"
    return _completed_record(command, completed, summary_path)


def _import_run_dir(summary: dict[str, Any]) -> Path | None:
    raw = ((summary.get("discovery_result") or {}).get("summary") or {}).get("selected_candidate", {}).get("run_dir")
    if not raw:
        raw = (summary.get("next_steps_result") or {}).get("run_dir")
    return Path(str(raw)).expanduser().resolve() if raw else None


def _completed_record(command: list[str], completed: subprocess.CompletedProcess[str], summary_path: Path | None) -> dict[str, Any]:
    summary = _read_json(summary_path) if summary_path else {}
    return {
        "returncode": int(completed.returncode),
        "command": command,
        "stdout_tail": (completed.stdout or "")[-4000:],
        "stderr_tail": (completed.stderr or "")[-4000:],
        "summary_path": "" if summary_path is None else str(summary_path),
        "summary": summary,
    }


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _import_check(result: dict[str, Any]) -> Check:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    status = summary.get("overall_status")
    if result.get("returncode") == 0 and status in {"PASS", "READY_FOR_LOCAL_NEXT_GATES"}:
        return Check("PASS", "20-pilot return import", f"overall_status={status}; summary={result.get('summary_path')}")
    return Check(
        "FAIL",
        "20-pilot return import",
        f"returncode={result.get('returncode')}; overall_status={status}; summary={result.get('summary_path')}",
    )


def _subprocess_check(name: str, result: dict[str, Any]) -> Check:
    status = "PASS" if int(result.get("returncode") or 0) == 0 else "FAIL"
    return Check(status, name, f"returncode={result.get('returncode')}; summary={result.get('summary_path', '')}")


def _decision(
    checks: list[Check],
    import_result: dict[str, Any] | None,
    postrun_result: dict[str, Any] | None,
) -> tuple[str, str]:
    if any(check.status == "FAIL" for check in checks):
        return "FAIL", "FIX_20_PILOT_IMPORT_OR_VALIDATION_FAILURE"
    if any(check.name == "20-pilot return tarball" and check.status == "WAITING" for check in checks):
        return "WAITING_FOR_RETURN", "WAIT_FOR_MARS_20_PILOT_RETURN"
    if postrun_result:
        postrun_summary = postrun_result.get("summary") if isinstance(postrun_result.get("summary"), dict) else {}
        if postrun_summary.get("overall_status") == "PASS":
            return "PASS", "ACCEPT_EMX_HFSS_S8P_20_PILOT_VALIDATION"
        return "WAITING_FOR_HFSS_EXPORT", str(postrun_summary.get("decision") or "RUN_OR_FIX_HFSS_S8P_EXPORT")
    import_summary = import_result.get("summary") if import_result and isinstance(import_result.get("summary"), dict) else {}
    if import_summary.get("overall_status") in {"PASS", "READY_FOR_LOCAL_NEXT_GATES"}:
        return "READY_FOR_HFSS", "RUN_AFTER_IMPORT_GATES_AND_HFSS_EXPORT"
    return "FAIL", "UNEXPECTED_20_PILOT_IMPORT_STATE"


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Latest S8P 20-Pilot Return Import",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Return tarball: `{summary.get('return_tarball')}`",
        f"- Expected pilot count: `{summary['arguments']['expected_count']}`",
        f"- Frequency grid: `{summary['arguments']['expected_frequency_start_ghz']}-{summary['arguments']['expected_frequency_stop_ghz']} GHz`, step `{summary['arguments']['expected_frequency_step_ghz']} GHz`, points `{summary['arguments']['expected_frequency_points']}`",
        "",
        "## Checks",
        "",
    ]
    for check in summary.get("checks", []):
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    lines.extend(["", "## Next", ""])
    if summary["overall_status"] == "WAITING_FOR_RETURN":
        lines.append("Wait for or download `next_gen_s8p_mars_return_latest.tar.gz`, then rerun this script.")
    elif summary["overall_status"] == "READY_FOR_HFSS":
        lines.append("Run the generated after-import command script, then run HFSS solve/export and rerun with `--hfss-results-dir`.")
    elif summary["overall_status"] == "WAITING_FOR_HFSS_EXPORT":
        lines.append("HFSS export or postrun validation is still pending; inspect the postrun summary before reporting curves.")
    elif summary["overall_status"] == "PASS":
        lines.append("The selected S8P sample passed the EMX-HFSS comparison gate.")
    else:
        lines.append("Fix the failed check before proceeding.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
