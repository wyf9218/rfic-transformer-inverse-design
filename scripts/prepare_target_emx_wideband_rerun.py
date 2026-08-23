#!/usr/bin/env python3
"""Prepare an auditable target-sample EMX wideband rerun command.

The current target sample has a saved narrowband EMX command in summary.json.
This helper keeps the original EMX binary, GDS, top cell, process file, Cadence
pin purpose, and port definitions, then changes only the output path and the
frequency list to the requested wideband grid.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_PACKAGE_DIR = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
DEFAULT_TARGET_SUMMARY = (
    DEFAULT_PROJECT_ROOT / "hfss_validation" / "final500_ec6698dfc575950b" / "summary.json"
)
DEFAULT_OUT_DIR = DEFAULT_PACKAGE_DIR / "target_emx_wideband_rerun_20260613"
EXPECTED_PORT_FLAGS = (
    "--port=P001=P001:P001_G",
    "--port=P002=P002:P002_G",
    "--port=P003=P003:P003_G",
    "--port=P004=P004:P004_G",
)
REQUIRED_STATIC_FLAGS = (
    "--touchstone",
    "--s-impedance=50",
    "--include-command-line",
    "--edge-width=1",
    "--accuracy=standard",
    "--verbose=2",
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-json", default=str(DEFAULT_TARGET_SUMMARY))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--sample-id", default="ec6698dfc575950b")
    parser.add_argument("--frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--expected-original-points", type=int, default=9)
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--output-s4p")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary_path = Path(args.summary_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    command_path = out_dir / "target_emx_wideband_rerun.commands.sh"
    command_json_path = out_dir / "target_emx_wideband_rerun_command.json"
    frequency_csv_path = out_dir / "target_emx_wideband_frequency_grid.csv"
    summary_out_path = out_dir / "target_emx_wideband_rerun_summary.json"
    report_path = out_dir / "target_emx_wideband_rerun_report.md"

    checks: list[Check] = []
    errors: list[str] = []
    original_command: list[str] = []
    generated_command: list[str] = []
    original_frequencies_hz: list[int] = []
    generated_frequencies_hz: list[int] = []
    original_output_s4p: str | None = None
    generated_output_s4p: str | None = None
    original_frequency_summary: dict[str, Any] = {}
    generated_frequency_summary: dict[str, Any] = {}
    source_summary: dict[str, Any] = {}

    try:
        source_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        original_command = [str(item) for item in source_summary.get("command", [])]
        checks.extend(_summary_checks(source_summary, original_command, args))
        prefix, original_frequencies_hz = _split_trailing_frequencies(original_command)
        original_output_s4p = _output_after_s_flag(prefix)
        generated_output_s4p = args.output_s4p or _default_wideband_output(original_output_s4p, args)
        generated_frequencies_hz = _frequency_grid_hz(
            args.frequency_start_ghz,
            args.frequency_stop_ghz,
            args.frequency_step_ghz,
        )
        generated_command = _replace_output(prefix, generated_output_s4p) + [
            str(freq_hz) for freq_hz in generated_frequencies_hz
        ]
        original_frequency_summary = _frequency_summary(original_frequencies_hz)
        generated_frequency_summary = _frequency_summary(generated_frequencies_hz)

        checks.extend(_original_command_checks(prefix, original_frequencies_hz, args))
        checks.extend(_generated_command_checks(generated_command, original_output_s4p, generated_output_s4p, args))

        _write_frequency_csv(frequency_csv_path, generated_frequencies_hz)
        command_json_path.write_text(json.dumps(generated_command, indent=2), encoding="utf-8")
        command_path.write_text(_render_shell_script(generated_command, source_summary, generated_output_s4p), encoding="utf-8")
        command_path.chmod(0o755)
    except Exception as exc:  # noqa: BLE001 - keep exact failure in audit trail.
        errors.append(f"{type(exc).__name__}: {exc}")
        checks.append(Check("FAIL", "command preparation", errors[-1]))

    status_counts = _status_counts(checks)
    overall_status = "PASS" if status_counts.get("FAIL", 0) == 0 else "FAIL"
    decision = (
        "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY"
        if overall_status == "PASS"
        else "DO_NOT_RUN_COMMAND_UNTIL_CHECKS_PASS"
    )
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "sample_id": args.sample_id,
        "summary_json": str(summary_path),
        "source_work_dir": source_summary.get("work_dir"),
        "source_touchstone_path": source_summary.get("touchstone_path"),
        "original_output_s4p": original_output_s4p,
        "generated_output_s4p": generated_output_s4p,
        "original_frequency_hz": original_frequency_summary,
        "generated_frequency_hz": generated_frequency_summary,
        "command_prefix_preserved_through_token": _last_non_frequency_token(original_command),
        "static_flags_required": list(REQUIRED_STATIC_FLAGS),
        "port_flags_required": list(EXPECTED_PORT_FLAGS),
        "checks": [check.__dict__ for check in checks],
        "status_counts": status_counts,
        "artifacts": {
            "summary": str(summary_out_path),
            "report": str(report_path),
            "command_shell": str(command_path) if command_path.exists() else None,
            "command_json": str(command_json_path) if command_json_path.exists() else None,
            "frequency_csv": str(frequency_csv_path) if frequency_csv_path.exists() else None,
        },
        "generated_command": generated_command,
        "method_notes": [
            "This file is not an EMX result and does not validate a Touchstone curve.",
            "The generated command is intentionally derived from the target sample's saved narrowband EMX command.",
            "Only the -s output path and the trailing frequency list are changed.",
            "After EMX finishes on MARS, the resulting .s4p must pass build_emx_first_validation_gate.py before HFSS-vs-EMX comparison.",
        ],
        "errors": errors,
    }
    summary_out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(result), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_out_path}")
    print(f"report={report_path}")
    if command_path.exists():
        print(f"command_shell={command_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _summary_checks(summary: dict[str, Any], command: list[str], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    cache_key = str(summary.get("cache_key", ""))
    if cache_key == args.sample_id:
        checks.append(Check("PASS", "target sample identity", f"cache_key={cache_key}"))
    else:
        checks.append(Check("FAIL", "target sample identity", f"expected {args.sample_id}, got {cache_key!r}"))
    if summary.get("ok") is True and summary.get("error") in (None, ""):
        checks.append(Check("PASS", "source summary run status", "original summary reports ok=true and error=null"))
    else:
        checks.append(Check("FAIL", "source summary run status", f"ok={summary.get('ok')!r}, error={summary.get('error')!r}"))
    if command:
        checks.append(Check("PASS", "source EMX command present", f"tokens={len(command)}"))
    else:
        checks.append(Check("FAIL", "source EMX command present", "summary.json has no command array"))
    return checks


def _original_command_checks(prefix: list[str], original_freqs: list[int], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    if len(original_freqs) == int(args.expected_original_points):
        checks.append(Check("PASS", "original narrowband frequency count", f"points={len(original_freqs)}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "original narrowband frequency count",
                f"expected {args.expected_original_points}, got {len(original_freqs)}",
            )
        )
    if len(prefix) >= 4 and PurePosixPath(prefix[0]).name == "emx":
        checks.append(Check("PASS", "EMX binary path", prefix[0]))
    else:
        checks.append(Check("FAIL", "EMX binary path", "first command token is not an emx binary path"))
    if len(prefix) >= 2 and prefix[1].endswith(".gds"):
        checks.append(Check("PASS", "GDS input path", prefix[1]))
    else:
        checks.append(Check("FAIL", "GDS input path", "second command token is not a .gds path"))
    if len(prefix) >= 3 and prefix[2].startswith("TRANSFORMER_"):
        checks.append(Check("PASS", "top cell", prefix[2]))
    else:
        checks.append(Check("FAIL", "top cell", "third command token is not a TRANSFORMER_* top cell"))
    if len(prefix) >= 4 and prefix[3].endswith(".proc"):
        checks.append(Check("PASS", "process file", prefix[3]))
    else:
        checks.append(Check("FAIL", "process file", "fourth command token is not a .proc file"))
    missing_static = [flag for flag in REQUIRED_STATIC_FLAGS if flag not in prefix]
    if missing_static:
        checks.append(Check("FAIL", "preserved static EMX flags", f"missing={missing_static}"))
    else:
        checks.append(Check("PASS", "preserved static EMX flags", ", ".join(REQUIRED_STATIC_FLAGS)))
    pin_flag = f"--cadence-pins={int(args.expected_pin_purpose)}"
    if pin_flag in prefix:
        checks.append(Check("PASS", "Cadence pin purpose", pin_flag))
    else:
        checks.append(Check("FAIL", "Cadence pin purpose", f"missing {pin_flag}"))
    missing_ports = [flag for flag in EXPECTED_PORT_FLAGS if flag not in prefix]
    if missing_ports:
        checks.append(Check("FAIL", "single-ended shield-grounded port flags", f"missing={missing_ports}"))
    else:
        checks.append(Check("PASS", "single-ended shield-grounded port flags", ", ".join(EXPECTED_PORT_FLAGS)))
    return checks


def _generated_command_checks(
    command: list[str],
    original_output: str | None,
    generated_output: str | None,
    args: argparse.Namespace,
) -> list[Check]:
    checks: list[Check] = []
    _, freqs = _split_trailing_frequencies(command)
    expected_points = _frequency_grid_hz(args.frequency_start_ghz, args.frequency_stop_ghz, args.frequency_step_ghz)
    if freqs == expected_points:
        checks.append(
            Check(
                "PASS",
                "generated wideband frequency grid",
                f"{freqs[0]}-{freqs[-1]} Hz, step={freqs[1] - freqs[0]} Hz, points={len(freqs)}",
            )
        )
    else:
        checks.append(Check("FAIL", "generated wideband frequency grid", "frequency list does not match requested inclusive grid"))
    if generated_output and original_output and generated_output != original_output:
        checks.append(Check("PASS", "output path is not old narrowband file", generated_output))
    else:
        checks.append(Check("FAIL", "output path is not old narrowband file", f"old={original_output}, new={generated_output}"))
    if generated_output and "emx_wideband" in generated_output:
        checks.append(Check("PASS", "output path labels wideband rerun", generated_output))
    else:
        checks.append(Check("FAIL", "output path labels wideband rerun", f"new={generated_output}"))
    command_text = "\n".join(command)
    if "/home/researcher" in command_text:
        checks.append(Check("FAIL", "MARS command has no local macOS path", "generated command contains /home/researcher"))
    else:
        checks.append(Check("PASS", "MARS command has no local macOS path", "no /home/researcher path in command"))
    if command.count("-s") == 1 and generated_output and command[command.index("-s") + 1] == generated_output:
        checks.append(Check("PASS", "Touchstone output flag", f"-s {generated_output}"))
    else:
        checks.append(Check("FAIL", "Touchstone output flag", "-s output path is missing or not replaced"))
    return checks


def _split_trailing_frequencies(command: list[str]) -> tuple[list[str], list[int]]:
    if not command:
        raise ValueError("empty EMX command")
    index = len(command)
    while index > 0 and _is_numeric_token(command[index - 1]):
        index -= 1
    freqs = [_numeric_hz_token(token) for token in command[index:]]
    if not freqs:
        raise ValueError("EMX command does not end with numeric frequency tokens")
    return command[:index], freqs


def _is_numeric_token(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _numeric_hz_token(token: str) -> int:
    value = float(token)
    rounded = round(value)
    if not math.isclose(value, rounded, rel_tol=0.0, abs_tol=1.0e-6):
        raise ValueError(f"frequency token is not an integer Hz value: {token}")
    return int(rounded)


def _output_after_s_flag(prefix: list[str]) -> str:
    if "-s" not in prefix:
        raise ValueError("EMX command prefix is missing -s output flag")
    index = prefix.index("-s")
    if index + 1 >= len(prefix):
        raise ValueError("-s output flag has no following output path")
    return prefix[index + 1]


def _default_wideband_output(original_output: str, args: argparse.Namespace) -> str:
    old = PurePosixPath(original_output)
    root = old.parent.parent if old.parent.name == "emx" else old.parent
    label = (
        f"emx_wideband_{_ghz_label(args.frequency_start_ghz)}_"
        f"{_ghz_label(args.frequency_stop_ghz)}_{_ghz_label(args.frequency_step_ghz)}"
    )
    return str(root / label / old.name)


def _ghz_label(value: float) -> str:
    text = f"{float(value):g}".replace(".", "p")
    return text


def _frequency_grid_hz(start_ghz: float, stop_ghz: float, step_ghz: float) -> list[int]:
    if step_ghz <= 0.0:
        raise ValueError("frequency step must be positive")
    if stop_ghz <= start_ghz:
        raise ValueError("frequency stop must be greater than start")
    intervals = (float(stop_ghz) - float(start_ghz)) / float(step_ghz)
    rounded = round(intervals)
    if not math.isclose(intervals, rounded, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError(f"frequency grid is not inclusive: intervals={intervals}")
    start_hz = int(round(float(start_ghz) * 1.0e9))
    step_hz = int(round(float(step_ghz) * 1.0e9))
    stop_hz = int(round(float(stop_ghz) * 1.0e9))
    points = [start_hz + index * step_hz for index in range(int(rounded) + 1)]
    if points[-1] != stop_hz:
        raise ValueError(f"frequency grid stop mismatch: expected {stop_hz}, got {points[-1]}")
    return points


def _replace_output(prefix: list[str], output_s4p: str) -> list[str]:
    updated = list(prefix)
    index = updated.index("-s")
    updated[index + 1] = output_s4p
    return updated


def _frequency_summary(freqs: list[int]) -> dict[str, Any]:
    if not freqs:
        return {"points": 0}
    steps = [b - a for a, b in zip(freqs, freqs[1:])]
    return {
        "start": freqs[0],
        "stop": freqs[-1],
        "step": steps[0] if steps and all(step == steps[0] for step in steps) else None,
        "points": len(freqs),
        "start_ghz": freqs[0] / 1.0e9,
        "stop_ghz": freqs[-1] / 1.0e9,
        "step_ghz": (steps[0] / 1.0e9) if steps and all(step == steps[0] for step in steps) else None,
    }


def _write_frequency_csv(path: Path, freqs_hz: list[int]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("index", "frequency_hz", "frequency_ghz"))
        writer.writeheader()
        for index, freq_hz in enumerate(freqs_hz):
            writer.writerow({"index": index, "frequency_hz": freq_hz, "frequency_ghz": freq_hz / 1.0e9})


def _render_shell_script(command: list[str], summary: dict[str, Any], output_s4p: str | None) -> str:
    output_dir = str(PurePosixPath(output_s4p).parent) if output_s4p else "."
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Recovered from the original target-sample summary.json.",
            f"# cache_key: {summary.get('cache_key', 'unknown')}",
            f"# original_work_dir: {summary.get('work_dir', 'unknown')}",
            "# This command only prepares the EMX rerun; the resulting .s4p still must pass validation gates.",
            f"mkdir -p {shlex.quote(output_dir)}",
            shlex.join(command),
            "",
        ]
    )


def _render_report(summary: dict[str, Any]) -> str:
    checks = "\n".join(
        f"- **{item['status']}** `{item['name']}`: {item['detail']}" for item in summary.get("checks", [])
    )
    return f"""# Target EMX Wideband Rerun Command Audit

Status: **{summary.get('overall_status')}**

Decision: `{summary.get('decision')}`

Sample: `{summary.get('sample_id')}`

This audit prepares a MARS-side EMX command for the target sample. It is not a
Touchstone result and must not be used as physical evidence until MARS produces
the new `.s4p` file and that file passes the EMX-first validation gate.

## Frequency Plan

- Original EMX grid: `{summary.get('original_frequency_hz')}`
- Generated EMX grid: `{summary.get('generated_frequency_hz')}`
- Original output: `{summary.get('original_output_s4p')}`
- Generated output: `{summary.get('generated_output_s4p')}`

## Checks

{checks}

## Required Next Step

Run `target_emx_wideband_rerun.commands.sh` on MARS from an environment where
the original GDS and proc paths exist. After EMX finishes, validate the generated
`.s4p` with `build_emx_first_validation_gate.py` before using it in ADS or any
HFSS-vs-EMX comparison.
"""


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _last_non_frequency_token(command: list[str]) -> str | None:
    if not command:
        return None
    try:
        prefix, _ = _split_trailing_frequencies(command)
    except ValueError:
        return None
    return prefix[-1] if prefix else None


if __name__ == "__main__":
    raise SystemExit(main())
