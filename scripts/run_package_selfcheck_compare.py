#!/usr/bin/env python3
"""Run the desktop package's self-contained EMX-vs-HFSS compare gate.

This refreshes the narrowband package selfcheck used by audit_delivery_package.py.
It intentionally verifies only the packaged single sample and does not claim
MARS final-500, 5-50 GHz EMX wideband, HFSS batch validation, or 248k readiness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package_dir = Path(args.package_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else package_dir / "package_selfcheck_compare_window_20260613"
    out_dir.mkdir(parents=True, exist_ok=True)

    compare_script = Path(args.compare_script).expanduser().resolve() if args.compare_script else Path(__file__).resolve().parent / "compare_emx_hfss_ads.py"
    emx_path = package_dir / args.emx_file
    hfss_path = package_dir / args.hfss_file
    summary_path = out_dir / "emx_hfss_ads_comparison_summary.json"
    report_path = out_dir / "emx_hfss_ads_comparison_report.md"

    cmd = [
        sys.executable,
        str(compare_script),
        "--emx",
        str(emx_path),
        "--hfss",
        str(hfss_path),
        "--out-dir",
        str(out_dir),
        "--emx-port-pairs",
        args.emx_port_pairs,
        "--hfss-port-pairs",
        args.hfss_port_pairs,
        "--compare-start-ghz",
        str(args.compare_start_ghz),
        "--compare-stop-ghz",
        str(args.compare_stop_ghz),
        "--min-frequency-points",
        str(args.min_frequency_points),
        "--expected-frequency-step-ghz",
        str(args.expected_frequency_step_ghz),
        "--expected-frequency-points",
        str(args.expected_frequency_points),
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--max-percent-error",
        str(args.max_percent_error),
        "--plot",
    ]
    if args.require_matching_frequency_grid:
        cmd.append("--require-matching-frequency-grid")
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True, cwd=package_dir)
    summary = _read_json(summary_path)
    status = "PASS" if completed.returncode == 0 and summary.get("overall_status") == "PASS" else "FAIL"
    wrapper = {
        "overall_status": status,
        "scope": "NARROWBAND_PACKAGE_SELF_CONSISTENCY_ONLY",
        "decision": "NOT_A_GOLDEN_EMX_REFERENCE_GATE",
        "evidence_use": "NOT_FINAL_LP_LS_Q_K_EVIDENCE",
        "package_dir": str(package_dir),
        "compare_command": cmd,
        "compare_returncode": completed.returncode,
        "compare_stdout_tail": completed.stdout.strip()[-2000:],
        "compare_stderr_tail": completed.stderr.strip()[-2000:],
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "frequency_window_expected": {
            "start_ghz": args.compare_start_ghz,
            "stop_ghz": args.compare_stop_ghz,
            "min_points": args.min_frequency_points,
            "expected_step_ghz": args.expected_frequency_step_ghz,
            "expected_points": args.expected_frequency_points,
            "frequency_tolerance_hz": args.frequency_tolerance_hz,
            "require_matching_frequency_grid": args.require_matching_frequency_grid,
        },
        "limitations": [
            "This is a package selfcheck for one known EMX/HFSS sample only.",
            "It is limited to the original narrowband overlap window and must not be cited as EMX-first approval.",
            "It must not be used as final Lp/Ls/Q/K evidence; final evidence requires an accepted EMX-first gate and accepted EMX/HFSS ADS validation chain.",
            "A PASS does not prove final-500, wideband 500, 248k, MARS, HFSS batch, or ADS completion.",
        ],
    }
    wrapper_path = out_dir / "package_selfcheck_compare_run_summary.json"
    wrapper_path.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"run_summary={wrapper_path}")
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if status != "PASS" and completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    return 2 if status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_package = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(default_package))
    parser.add_argument("--out-dir")
    parser.add_argument("--compare-script")
    parser.add_argument("--emx-file", default="ec6698dfc575950b_EMX_reference_NARROWBAND_13p5_16p5GHz.s4p")
    parser.add_argument("--hfss-file", default="ec6698dfc575950b_HFSS_WIDEBAND_0p1_50GHz_step0p1.s4p")
    parser.add_argument("--emx-port-pairs", default="1,2:3,4")
    parser.add_argument("--hfss-port-pairs", default="1,2:3,4")
    parser.add_argument("--compare-start-ghz", type=float, default=13.5)
    parser.add_argument("--compare-stop-ghz", type=float, default=16.5)
    parser.add_argument("--min-frequency-points", type=int, default=9)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.375)
    parser.add_argument("--expected-frequency-points", type=int, default=9)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--require-matching-frequency-grid", action="store_true")
    parser.add_argument("--max-percent-error", type=float, default=5.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


if __name__ == "__main__":
    raise SystemExit(main())
