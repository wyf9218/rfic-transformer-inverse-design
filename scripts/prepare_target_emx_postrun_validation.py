#!/usr/bin/env python3
"""Prepare the post-run validation command for a target EMX wideband result.

Run this locally after preparing the target rerun command. The generated shell
script is intended to run on MARS after EMX creates the wideband .s4p. It does
not fabricate or validate data by itself; it fixes the exact validation sequence
that must be run before any ADS/HFSS comparison claim is made.
"""

from __future__ import annotations

import argparse
import json
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_PACKAGE_DIR = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
DEFAULT_TARGET_DIR = DEFAULT_PROJECT_ROOT / "hfss_validation" / "final500_ec6698dfc575950b"
DEFAULT_RERUN_DIR = DEFAULT_TARGET_DIR / "target_emx_wideband_rerun_20260613"
DEFAULT_RERUN_SUMMARY = DEFAULT_RERUN_DIR / "target_emx_wideband_rerun_summary.json"
DEFAULT_OUT_DIR = DEFAULT_RERUN_DIR
DEFAULT_PACKAGE_OUT_DIR = DEFAULT_PACKAGE_DIR / "target_emx_wideband_rerun_20260613"

REQUIRED_COMMAND_FRAGMENTS = (
    "scripts/audit_touchstone_transformer.py",
    "--expected-source-kind EMX",
    "--expected-frequency-start-ghz 5.0",
    "--expected-frequency-stop-ghz 50.0",
    "--expected-frequency-step-ghz 0.1",
    "--expected-frequency-points 451",
    "--min-window-abs-k 0.05",
    "--positive-window-start-ghz 5.0",
    "--positive-window-stop-ghz 30.0",
    "--shape-window-start-ghz 5.0",
    "--shape-window-stop-ghz 30.0",
    "scripts/build_emx_first_validation_gate.py",
    "--required-sweep-start-ghz 5.0",
    "--required-sweep-stop-ghz 50.0",
    "--required-sweep-step-ghz 0.1",
    "--required-sweep-points 451",
    "--photo-max-percent-error 5.0",
    "--physical-window-start-ghz 5.0",
    "--physical-window-stop-ghz 30.0",
    "--max-shape-spike-ratio 4",
    "--max-shape-relative-step 0.25",
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rerun-summary", default=str(DEFAULT_RERUN_SUMMARY))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--sample-id", default="ec6698dfc575950b")
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--frequency-points", type=int, default=451)
    parser.add_argument("--positive-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--positive-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--physical-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--physical-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--shape-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--shape-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--max-shape-spike-ratio", type=float, default=4.0)
    parser.add_argument("--max-shape-relative-step", type=float, default=0.25)
    parser.add_argument("--photo-max-percent-error", type=float, default=5.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rerun_summary_path = Path(args.rerun_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    command_path = out_dir / "target_emx_wideband_postrun_validation.commands.sh"
    summary_path = out_dir / "target_emx_wideband_postrun_validation_summary.json"
    report_path = out_dir / "target_emx_wideband_postrun_validation_report.md"

    checks: list[Check] = []
    errors: list[str] = []
    rerun_summary: dict[str, Any] = {}
    emx_s4p = ""
    validation_dir = ""
    command_text = ""

    try:
        rerun_summary = json.loads(rerun_summary_path.read_text(encoding="utf-8"))
        emx_s4p = str(rerun_summary.get("generated_output_s4p") or "")
        validation_dir = _default_validation_dir(emx_s4p)
        checks.extend(_rerun_summary_checks(rerun_summary, args, emx_s4p))
        command_text = _render_postrun_command(emx_s4p, validation_dir, args)
        checks.extend(_postrun_command_checks(command_text))
        command_path.write_text(command_text, encoding="utf-8")
        command_path.chmod(0o755)
    except Exception as exc:  # noqa: BLE001 - persist exact problem.
        errors.append(f"{type(exc).__name__}: {exc}")
        checks.append(Check("FAIL", "post-run command preparation", errors[-1]))

    status_counts = _status_counts(checks)
    overall_status = "PASS" if status_counts.get("FAIL", 0) == 0 else "FAIL"
    decision = "READY_FOR_MARS_POSTRUN_VALIDATION" if overall_status == "PASS" else "DO_NOT_USE_POSTRUN_COMMAND"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "sample_id": args.sample_id,
        "rerun_summary": str(rerun_summary_path),
        "expected_emx_s4p": emx_s4p,
        "default_validation_dir": validation_dir,
        "checks": [check.__dict__ for check in checks],
        "status_counts": status_counts,
        "artifacts": {
            "summary": str(summary_path),
            "report": str(report_path),
            "command_shell": str(command_path) if command_path.exists() else None,
        },
        "required_command_fragments": list(REQUIRED_COMMAND_FRAGMENTS),
        "method_notes": [
            "The generated command must be run after EMX creates the wideband .s4p.",
            "The Touchstone physical gate checks ports, frequency grid, passivity, reciprocity, finite ADS-equivalent metrics, and smooth positive L/Q/K windows.",
            "The EMX-first gate then checks the same file against the ADS-photo anchor, required 5-50 GHz / 0.1 GHz / 451-point sweep, and no-extrapolation ADS plotting grid before it can be treated as the golden EMX reference.",
            "If either gate fails, do not proceed to HFSS comparison; keep the failure report as evidence.",
        ],
        "errors": errors,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if command_path.exists():
        print(f"command_shell={command_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _rerun_summary_checks(summary: dict[str, Any], args: argparse.Namespace, emx_s4p: str) -> list[Check]:
    checks: list[Check] = []
    if summary.get("overall_status") == "PASS":
        checks.append(Check("PASS", "rerun command preparation status", "target EMX rerun command is prepared"))
    else:
        checks.append(Check("FAIL", "rerun command preparation status", f"overall_status={summary.get('overall_status')!r}"))
    if summary.get("decision") == "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY":
        checks.append(Check("PASS", "rerun command preparation decision", str(summary.get("decision"))))
    else:
        checks.append(Check("FAIL", "rerun command preparation decision", f"decision={summary.get('decision')!r}"))
    if summary.get("sample_id") == args.sample_id:
        checks.append(Check("PASS", "target sample identity", str(summary.get("sample_id"))))
    else:
        checks.append(Check("FAIL", "target sample identity", f"expected {args.sample_id}, got {summary.get('sample_id')!r}"))
    generated = summary.get("generated_frequency_hz", {})
    expected = {
        "start": int(round(args.frequency_start_ghz * 1.0e9)),
        "stop": int(round(args.frequency_stop_ghz * 1.0e9)),
        "step": int(round(args.frequency_step_ghz * 1.0e9)),
        "points": int(args.frequency_points),
    }
    mismatches = [f"{key}: expected {value}, got {generated.get(key)!r}" for key, value in expected.items() if generated.get(key) != value]
    if mismatches:
        checks.append(Check("FAIL", "rerun frequency grid", "; ".join(mismatches)))
    else:
        checks.append(Check("PASS", "rerun frequency grid", "5-50 GHz, 0.1 GHz, 451 points"))
    if emx_s4p and "emx_wideband_5_50_0p1/emx.s4p" in emx_s4p:
        checks.append(Check("PASS", "expected EMX output path", emx_s4p))
    else:
        checks.append(Check("FAIL", "expected EMX output path", f"unexpected generated_output_s4p={emx_s4p!r}"))
    if "/home/researcher" in emx_s4p:
        checks.append(Check("FAIL", "MARS output path", "generated output path contains local macOS /home/researcher"))
    else:
        checks.append(Check("PASS", "MARS output path", "no local macOS path in generated output"))
    return checks


def _postrun_command_checks(text: str) -> list[Check]:
    checks: list[Check] = []
    missing = [fragment for fragment in REQUIRED_COMMAND_FRAGMENTS if fragment not in text]
    if missing:
        checks.append(Check("FAIL", "post-run validation command fragments", f"missing={missing}"))
    else:
        checks.append(Check("PASS", "post-run validation command fragments", f"{len(REQUIRED_COMMAND_FRAGMENTS)} fragments present"))
    if "/home/researcher" in text:
        checks.append(Check("FAIL", "post-run command has no local macOS path", "command contains /home/researcher"))
    else:
        checks.append(Check("PASS", "post-run command has no local macOS path", "no /home/researcher path in command"))
    if "test -s \"$EMX_S4P\"" in text:
        checks.append(Check("PASS", "post-run command requires EMX file", "test -s \"$EMX_S4P\""))
    else:
        checks.append(Check("FAIL", "post-run command requires EMX file", "missing non-empty file check"))
    if "tar -czf" in text and "sha256sum" in text:
        checks.append(Check("PASS", "post-run evidence transfer package", "tarball and SHA commands present"))
    else:
        checks.append(Check("FAIL", "post-run evidence transfer package", "tarball or SHA command missing"))
    return checks


def _default_validation_dir(emx_s4p: str) -> str:
    if not emx_s4p:
        return ""
    path = PurePosixPath(emx_s4p)
    return str(path.parent / "validation_20260613")


def _render_postrun_command(emx_s4p: str, validation_dir: str, args: argparse.Namespace) -> str:
    if not emx_s4p:
        raise ValueError("rerun summary does not contain generated_output_s4p")
    if not validation_dir:
        raise ValueError("could not infer validation directory")
    touchstone_cmd = [
        ".venv/bin/python",
        "scripts/audit_touchstone_transformer.py",
        "$EMX_S4P",
        "--out-dir",
        "$OUT_DIR/touchstone_physical_gate",
        "--expected-ports",
        "4",
        "--expected-source-kind",
        "EMX",
        "--port-pairs",
        args.port_pairs,
        "--expected-frequency-start-ghz",
        f"{args.frequency_start_ghz:.1f}",
        "--expected-frequency-stop-ghz",
        f"{args.frequency_stop_ghz:.1f}",
        "--expected-frequency-step-ghz",
        f"{args.frequency_step_ghz:.1f}",
        "--expected-frequency-points",
        str(int(args.frequency_points)),
        "--required-sweep-start-ghz",
        f"{args.frequency_start_ghz:.1f}",
        "--required-sweep-stop-ghz",
        f"{args.frequency_stop_ghz:.1f}",
        "--target-frequency-ghz",
        f"{args.target_ghz:.1f}",
        "--target-frequency-tolerance-ghz",
        "0.05",
        "--min-target-inductance-nh",
        "0.05",
        "--min-target-q",
        "1.0",
        "--min-target-abs-k",
        "0.05",
        "--max-target-abs-k",
        "0.98",
        "--positive-window-start-ghz",
        f"{args.positive_window_start_ghz:.1f}",
        "--positive-window-stop-ghz",
        f"{args.positive_window_stop_ghz:.1f}",
        "--min-window-abs-k",
        "0.05",
        "--shape-window-start-ghz",
        f"{args.shape_window_start_ghz:.1f}",
        "--shape-window-stop-ghz",
        f"{args.shape_window_stop_ghz:.1f}",
        "--max-shape-spike-ratio",
        f"{args.max_shape_spike_ratio:g}",
        "--max-shape-relative-step",
        f"{args.max_shape_relative_step:g}",
        "--plot",
    ]
    emx_first_cmd = [
        ".venv/bin/python",
        "scripts/build_emx_first_validation_gate.py",
        "--emx-s4p",
        "$EMX_S4P",
        "--out-dir",
        "$OUT_DIR/emx_first_validation_gate_20260613",
        "--port-pairs",
        args.port_pairs,
        "--target-ghz",
        f"{args.target_ghz:.1f}",
        "--photo-max-percent-error",
        f"{args.photo_max_percent_error:.1f}",
        "--required-sweep-start-ghz",
        f"{args.frequency_start_ghz:.1f}",
        "--required-sweep-stop-ghz",
        f"{args.frequency_stop_ghz:.1f}",
        "--required-sweep-step-ghz",
        f"{args.frequency_step_ghz:.1f}",
        "--required-sweep-points",
        str(int(args.frequency_points)),
        "--physical-window-start-ghz",
        f"{args.physical_window_start_ghz:.1f}",
        "--physical-window-stop-ghz",
        f"{args.physical_window_stop_ghz:.1f}",
        "--min-window-abs-k",
        "0.05",
        "--shape-window-start-ghz",
        f"{args.shape_window_start_ghz:.1f}",
        "--shape-window-stop-ghz",
        f"{args.shape_window_stop_ghz:.1f}",
        "--max-shape-spike-ratio",
        f"{args.max_shape_spike_ratio:g}",
        "--max-shape-relative-step",
        f"{args.max_shape_relative_step:g}",
    ]
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Run on MARS only after target_emx_wideband_rerun.commands.sh creates the wideband EMX .s4p.",
            f"EMX_S4P=${{1:-{shlex.quote(emx_s4p)}}}",
            f"OUT_DIR=${{2:-{shlex.quote(validation_dir)}}}",
            'TRANSFER_TARBALL="${OUT_DIR%/}_transfer.tar.gz"',
            "",
            'test -s "$EMX_S4P"',
            'mkdir -p "$OUT_DIR"',
            'sha256sum "$EMX_S4P" | tee "$OUT_DIR/emx_wideband.s4p.sha256"',
            "",
            _shell_join_preserving_vars(touchstone_cmd),
            "",
            _shell_join_preserving_vars(emx_first_cmd),
            "",
            'tar -czf "$TRANSFER_TARBALL" -C "$(dirname "$OUT_DIR")" "$(basename "$OUT_DIR")"',
            'sha256sum "$TRANSFER_TARBALL" | tee "${TRANSFER_TARBALL}.sha256"',
            "",
        ]
    )


def _shell_join_preserving_vars(parts: list[str]) -> str:
    rendered: list[str] = []
    for part in parts:
        if part == "$EMX_S4P":
            rendered.append('"$EMX_S4P"')
        elif part == "$OUT_DIR" or part.startswith("$OUT_DIR/"):
            rendered.append(f'"{part}"')
        else:
            rendered.append(shlex.quote(part))
    return " ".join(rendered)


def _render_report(summary: dict[str, Any]) -> str:
    checks = "\n".join(
        f"- **{item['status']}** `{item['name']}`: {item['detail']}" for item in summary.get("checks", [])
    )
    return f"""# Target EMX Wideband Post-Run Validation Command

Status: **{summary.get('overall_status')}**

Decision: `{summary.get('decision')}`

Expected EMX S4P:

```text
{summary.get('expected_emx_s4p')}
```

Default validation directory:

```text
{summary.get('default_validation_dir')}
```

## Checks

{checks}

## Meaning

This prepares the MARS command that must run after EMX finishes. It does not
prove that the S4P exists or that the curves are correct. The generated command
will fail unless the EMX file exists and both validation gates pass.
"""


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
