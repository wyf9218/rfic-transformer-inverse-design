#!/usr/bin/env python3
"""Audit progress and transfer readiness of a MARS dataset run.

This is a non-simulator monitor. It does not prove EM accuracy; it checks the
local filesystem evidence that should exist after a `sample-dataset` run:
manifest, rows CSV, evaluation directories, summaries, Touchstone files, layout
metadata, and optional GDS/preview files. Optional Touchstone frequency checks
sample real `.s*p` files so a finished run cannot be accepted with the wrong
ADS/HFSS sweep grid.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class EvalEvidence:
    key: str
    directory: Path | None
    row_ok: bool | None
    row_touchstone_path: Path | None
    summary_count: int
    summary_ok_count: int
    summary_fail_count: int
    summary_unknown_count: int
    summary_parse_error_count: int
    summary_error_examples: tuple[str, ...]
    touchstone_count: int
    touchstone_paths: tuple[Path, ...]
    emx_command_count: int
    emx_command_paths: tuple[Path, ...]
    layout_json_count: int
    gds_count: int
    preview_count: int


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else run_dir / "mars_run_progress_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = _read_json(run_dir / "dataset_manifest.json")
    rows = _read_rows(run_dir / "dataset_rows.csv")
    clearance_audit = _read_json(run_dir / "final500_ground_clearance_audit.json")
    evals = _collect_eval_evidence(run_dir, rows)
    touchstone_checks = _touchstone_frequency_checks(
        _all_touchstones(evals),
        args=args,
    )
    emx_command_checks = _emx_command_contract_checks(evals, args=args)
    checks = _build_checks(run_dir, manifest, rows, evals, touchstone_checks, emx_command_checks, clearance_audit, args)
    overall_status = _overall_status(checks)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "arguments": _argument_summary(args),
        "manifest": _manifest_summary(manifest),
        "rows": _rows_summary(rows),
        "evaluations": _eval_summary(evals),
        "touchstone_frequency_checks": touchstone_checks,
        "emx_command_checks": emx_command_checks,
        "clearance_audit": _clearance_audit_summary(clearance_audit),
        "checks": checks,
        "limitations": [
            "This audit checks filesystem evidence only; it does not run EMX, HFSS, or ADS.",
            "A PASS means the requested local run artifacts are complete enough for transfer and downstream gates.",
            "Physics acceptance still requires Touchstone preflight plus sampled EMX/HFSS/ADS correlation.",
        ],
    }
    summary_path = out_dir / "mars_run_progress_summary.json"
    report_path = out_dir / "mars_run_progress_report.md"
    rows_csv_path = out_dir / "mars_run_progress_rows.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_rows_csv(rows_csv_path, evals)

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"rows_csv={rows_csv_path}")
    for check in checks:
        print(f"{check['status']:10s} {check['name']}: {check['detail']}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--require-summary", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-touchstone", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-summary-ok", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-layout-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-gds", action="store_true")
    parser.add_argument("--require-layout-preview", action="store_true")
    parser.add_argument("--require-clearance-audit", action="store_true")
    parser.add_argument("--require-geometry-quality", action="store_true")
    parser.add_argument("--require-shield-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-geometry-pass-fraction", type=float, default=1.0)
    parser.add_argument("--internal-angle-deg", type=float, default=135.0)
    parser.add_argument("--terminal-angle-deg", type=float, default=90.0)
    parser.add_argument("--angle-tolerance-deg", type=float, default=1.0e-3)
    parser.add_argument("--require-emx-command", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--expected-port-mode")
    parser.add_argument("--expected-pin-purpose", type=int)
    parser.add_argument("--expected-touchstone-ports", type=int, default=4)
    parser.add_argument("--required-touchstone-extension", default=".s4p")
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-frequency-checks", type=int, default=25)
    parser.add_argument("--touchstone-seed", type=int, default=20260613)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - preserve exact audit detail.
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _collect_eval_evidence(run_dir: Path, rows: list[dict[str, str]]) -> list[EvalEvidence]:
    by_key: dict[str, dict[str, Any]] = {}
    evaluations_dir = run_dir / "evaluations"
    if evaluations_dir.exists():
        for directory in sorted(path for path in evaluations_dir.iterdir() if path.is_dir()):
            by_key.setdefault(directory.name, {})["directory"] = directory

    for index, row in enumerate(rows):
        key = _row_key(row, index)
        entry = by_key.setdefault(key, {})
        entry["row_ok"] = _truthy(row.get("ok", "true"))
        text = (row.get("touchstone_path") or row.get("sparameter_path") or "").strip()
        if text:
            entry["row_touchstone_path"] = _resolve(run_dir, text)

    evidence: list[EvalEvidence] = []
    for key, entry in sorted(by_key.items()):
        directory = entry.get("directory")
        directory = directory if isinstance(directory, Path) else None
        row_touchstone_path = entry.get("row_touchstone_path")
        row_touchstone_path = row_touchstone_path if isinstance(row_touchstone_path, Path) else None
        summary_status = _summary_status(directory)
        touchstone_paths = set(_touchstone_paths(directory))
        if row_touchstone_path and row_touchstone_path.exists():
            touchstone_paths.add(row_touchstone_path.resolve())
        emx_command_paths = _emx_command_paths(directory)
        evidence.append(
            EvalEvidence(
                key=key,
                directory=directory,
                row_ok=entry.get("row_ok"),
                row_touchstone_path=row_touchstone_path,
                summary_count=summary_status["count"],
                summary_ok_count=summary_status["ok_count"],
                summary_fail_count=summary_status["fail_count"],
                summary_unknown_count=summary_status["unknown_count"],
                summary_parse_error_count=summary_status["parse_error_count"],
                summary_error_examples=tuple(summary_status["error_examples"]),
                touchstone_count=len(touchstone_paths),
                touchstone_paths=tuple(sorted(touchstone_paths)),
                emx_command_count=len(emx_command_paths),
                emx_command_paths=tuple(sorted(emx_command_paths)),
                layout_json_count=_count_layout_json(directory),
                gds_count=_count_files(directory, ("*.gds", "layout/*.gds")),
                preview_count=_count_files(directory, ("*.png", "*.svg", "layout/*.png", "layout/*.svg")),
            )
        )
    return evidence


def _row_key(row: dict[str, str], index: int) -> str:
    for field in ("sample_id", "cache_key", "id", "name"):
        value = (row.get(field) or "").strip()
        if value:
            return value
    text = (row.get("touchstone_path") or row.get("sparameter_path") or "").strip()
    if text:
        parts = Path(text).parts
        if "evaluations" in parts:
            eval_index = parts.index("evaluations")
            if eval_index + 1 < len(parts):
                return parts[eval_index + 1]
    return f"row_{index:06d}"


def _resolve(run_dir: Path, text: str) -> Path:
    path = Path(text).expanduser()
    return path if path.is_absolute() else (run_dir / path).resolve()


def _path_is_inside(path: Path, directory: Path | None) -> bool:
    if directory is None:
        return False
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _count_files(directory: Path | None, patterns: tuple[str, ...]) -> int:
    if directory is None:
        return 0
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path for path in directory.glob(pattern) if path.is_file())
    return len(paths)


def _count_touchstones(directory: Path | None) -> int:
    return len(_touchstone_paths(directory))


def _touchstone_paths(directory: Path | None) -> set[Path]:
    if directory is None:
        return set()
    paths: set[Path] = set()
    for pattern in ("*.s*p", "emx/*.s*p"):
        paths.update(path.resolve() for path in directory.glob(pattern) if path.is_file())
    return paths


def _emx_command_paths(directory: Path | None) -> set[Path]:
    if directory is None:
        return set()
    paths: set[Path] = set()
    for pattern in ("emx/emx_command.json", "emx_command.json"):
        paths.update(path.resolve() for path in directory.glob(pattern) if path.is_file())
    return paths


def _summary_status(directory: Path | None) -> dict[str, Any]:
    paths = sorted(directory.glob("summary.json")) if directory is not None else []
    result = {
        "count": len(paths),
        "ok_count": 0,
        "fail_count": 0,
        "unknown_count": 0,
        "parse_error_count": 0,
        "error_examples": [],
    }
    for path in paths:
        data = _read_json(path)
        if "_parse_error" in data:
            result["parse_error_count"] += 1
            result["error_examples"].append(f"{path.name}: {data['_parse_error']}")
            continue
        ok_value = _summary_ok_value(data)
        if ok_value is True:
            result["ok_count"] += 1
        elif ok_value is False:
            result["fail_count"] += 1
            error_text = data.get("error")
            if error_text:
                result["error_examples"].append(f"{path.name}: {error_text}")
        else:
            result["unknown_count"] += 1
    result["error_examples"] = result["error_examples"][:5]
    return result


def _summary_ok_value(data: dict[str, Any]) -> bool | None:
    if "ok" in data:
        return _truthy_any(data.get("ok"))
    if data.get("error") not in (None, "", []):
        return False
    return None


def _count_layout_json(directory: Path | None) -> int:
    if directory is None:
        return 0
    paths: set[Path] = set()
    for pattern in ("*.layout.json", "layout/*.layout.json", "layout/*.json"):
        paths.update(path for path in directory.glob(pattern) if path.is_file())
    return len(paths)


def _all_touchstones(evals: list[EvalEvidence]) -> list[Path]:
    paths: set[Path] = set()
    for item in evals:
        paths.update(item.touchstone_paths)
    return sorted(paths)


def _touchstone_frequency_checks(paths: list[Path], *, args: argparse.Namespace) -> dict[str, Any]:
    selected = _select_touchstones(paths, args.max_touchstone_frequency_checks, args.touchstone_seed)
    checked = []
    for path in selected:
        checked.append(_check_one_touchstone(path, args))
    fail_count = sum(1 for item in checked if item["status"] == "FAIL")
    skipped_count = max(0, len(paths) - len(selected))
    status = "FAIL" if fail_count else "PASS"
    if not checked and _has_touchstone_sample_requirements(args):
        status = "FAIL"
    return {
        "status": status,
        "discovered_count": len(paths),
        "checked_count": len(checked),
        "skipped_count": skipped_count,
        "fail_count": fail_count,
        "max_checks": int(args.max_touchstone_frequency_checks),
        "seed": int(args.touchstone_seed),
        "checked": checked,
    }


def _emx_command_contract_checks(evals: list[EvalEvidence], *, args: argparse.Namespace) -> dict[str, Any]:
    required = _has_emx_command_requirements(args)
    if not required:
        return {
            "status": "SKIPPED",
            "required": False,
            "checked_count": 0,
            "fail_count": 0,
            "checked": [],
        }

    checked = [
        _check_one_emx_command(path, args)
        for item in evals
        if item.row_ok is not False
        for path in item.emx_command_paths
    ]
    fail_count = sum(1 for item in checked if item["status"] == "FAIL")
    status = "FAIL" if fail_count or not checked else "PASS"
    return {
        "status": status,
        "required": True,
        "checked_count": len(checked),
        "fail_count": fail_count,
        "checked": checked,
    }


def _check_one_emx_command(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    try:
        command = _read_emx_command(path)
        frequency_tokens = _command_frequency_tokens(command)
        mismatches = _emx_command_mismatches(command, args)
        status = "FAIL" if mismatches else "PASS"
        return {
            "status": status,
            "path": str(path),
            "argv_count": len(command),
            "port_count": len(_command_port_specs(command)),
            "frequency_token_count": len(frequency_tokens),
            "start_hz": float(frequency_tokens[0]) if frequency_tokens else None,
            "stop_hz": float(frequency_tokens[-1]) if frequency_tokens else None,
            "mismatches": mismatches,
        }
    except Exception as exc:  # noqa: BLE001 - audit should preserve exact failure.
        return {"status": "FAIL", "path": str(path), "mismatches": [f"{type(exc).__name__}: {exc}"]}


def _read_emx_command(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("command", "argv", "args"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list):
        raise ValueError(f"top-level JSON must be a command list, got {type(data).__name__}")
    if not all(isinstance(item, (str, int, float)) for item in data):
        bad_types = sorted({type(item).__name__ for item in data if not isinstance(item, (str, int, float))})
        raise ValueError(f"command contains non-scalar argv item types: {bad_types}")
    return [str(item) for item in data]


def _emx_command_mismatches(command: list[str], args: argparse.Namespace) -> list[str]:
    mismatches: list[str] = []
    mismatches.extend(_pin_purpose_mismatches(command, args))
    mismatches.extend(_port_mode_mismatches(command, args))
    mismatches.extend(_command_frequency_mismatches(command, args))
    return mismatches


def _pin_purpose_mismatches(command: list[str], args: argparse.Namespace) -> list[str]:
    if args.expected_pin_purpose is None:
        return []
    expected = str(int(args.expected_pin_purpose))
    values = _command_option_values(command, "--cadence-pins")
    if expected not in values:
        return [f"cadence pin purpose expected={expected} actual={values or 'missing'}"]
    return []


def _port_mode_mismatches(command: list[str], args: argparse.Namespace) -> list[str]:
    expected = str(args.expected_port_mode or "").strip()
    if not expected:
        return []
    specs = _command_port_specs(command)
    mismatches: list[str] = []
    if expected == "single_ended_shield_grounded":
        if not specs:
            mismatches.append("single_ended_shield_grounded requires --port entries with explicit grounds")
        ungrounded = [spec for spec in specs if ":" not in spec.split("=", 1)[-1]]
        if ungrounded:
            mismatches.append(f"single_ended_shield_grounded ports missing ground delimiter: {ungrounded[:4]}")
        if args.expected_touchstone_ports is not None and len(specs) < int(args.expected_touchstone_ports):
            mismatches.append(f"port entries expected>={args.expected_touchstone_ports} actual={len(specs)}")
    else:
        mismatches.append(f"unsupported expected port mode for command audit: {expected}")
    return mismatches


def _command_frequency_mismatches(command: list[str], args: argparse.Namespace) -> list[str]:
    if not _has_expected_frequency_args(args):
        return []
    freqs = np.asarray(_command_frequency_tokens(command), dtype=float)
    if len(freqs) == 0:
        return ["emx command has no explicit numeric frequency tokens"]
    return _frequency_mismatches(freqs, args)


def _command_option_values(command: list[str], option: str) -> list[str]:
    values: list[str] = []
    prefix = f"{option}="
    for index, token in enumerate(command):
        if token.startswith(prefix):
            values.append(token[len(prefix) :])
        elif token == option and index + 1 < len(command):
            values.append(command[index + 1])
    return values


def _command_port_specs(command: list[str]) -> list[str]:
    specs: list[str] = []
    for index, token in enumerate(command):
        if token.startswith("--port="):
            specs.append(token.split("=", 1)[1])
        elif token == "--port" and index + 1 < len(command):
            specs.append(command[index + 1])
    return specs


def _command_frequency_tokens(command: list[str]) -> list[float]:
    values: list[float] = []
    option_value_tokens = {"--cadence-pins", "--s-impedance", "-s", "--touchstone", "--proc", "--process"}
    for index, token in enumerate(command):
        stripped = token.strip()
        if not stripped or stripped.startswith("-"):
            continue
        if index > 0 and command[index - 1] in option_value_tokens:
            continue
        try:
            values.append(float(stripped))
        except ValueError:
            continue
    return values


def _select_touchstones(paths: list[Path], max_checks: int, seed: int) -> list[Path]:
    if max_checks <= 0:
        return []
    if len(paths) <= max_checks:
        return list(paths)
    rng = random.Random(seed)
    return sorted(rng.sample(paths, max_checks))


def _check_one_touchstone(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    try:
        freqs, ports = _read_touchstone_freqs_and_ports(path)
        mismatches = _touchstone_file_mismatches(path, ports, args)
        mismatches.extend(_frequency_mismatches(freqs, args))
        status = "FAIL" if mismatches else "PASS"
        step_hz = float(np.median(np.diff(freqs))) if len(freqs) >= 2 else None
        return {
            "status": status,
            "path": str(path),
            "ports": ports,
            "start_hz": float(freqs[0]) if len(freqs) else None,
            "stop_hz": float(freqs[-1]) if len(freqs) else None,
            "step_hz": step_hz,
            "points": int(len(freqs)),
            "mismatches": mismatches,
        }
    except Exception as exc:  # noqa: BLE001 - audit should capture exact failure.
        return {"status": "FAIL", "path": str(path), "mismatches": [f"{type(exc).__name__}: {exc}"]}


def _read_touchstone_freqs_and_ports(path: Path) -> tuple[np.ndarray, int]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\.s(\d+)p$", path.name, re.IGNORECASE)
    if match is None:
        raise ValueError(f"Cannot infer Touchstone port count from extension: {path.name}")
    ports = int(match.group(1))
    freq_unit = 1.0
    rows: list[list[float]] = []
    current_row: list[float] = []
    expected_values = 1 + 2 * ports * ports
    for line in text.splitlines():
        clean = line.split("!")[0].strip()
        if not clean:
            continue
        if clean.startswith("#"):
            parts = clean[1:].lower().split()
            for part in parts:
                if part in ("hz", "khz", "mhz", "ghz"):
                    freq_unit = {"hz": 1.0, "khz": 1.0e3, "mhz": 1.0e6, "ghz": 1.0e9}[part]
            continue
        current_row.extend(float(item) for item in clean.split())
        while len(current_row) >= expected_values:
            rows.append(current_row[:expected_values])
            current_row = current_row[expected_values:]
    if current_row:
        raise ValueError(f"Incomplete Touchstone row: trailing_values={len(current_row)}")
    if not rows:
        raise ValueError("No numeric Touchstone rows found")
    return np.asarray([row[0] * freq_unit for row in rows], dtype=float), ports


def _touchstone_file_mismatches(path: Path, ports: int, args: argparse.Namespace) -> list[str]:
    mismatches: list[str] = []
    required_extension = str(args.required_touchstone_extension or "").strip().lower()
    if required_extension and path.suffix.lower() != required_extension:
        mismatches.append(f"extension expected={required_extension} actual={path.suffix}")
    if args.expected_touchstone_ports is not None and int(ports) != int(args.expected_touchstone_ports):
        mismatches.append(f"ports expected={args.expected_touchstone_ports} actual={ports}")
    return mismatches


def _frequency_mismatches(freqs: np.ndarray, args: argparse.Namespace) -> list[str]:
    if not _has_expected_frequency_args(args):
        return []
    if len(freqs) == 0:
        return ["no frequency points"]
    tol = float(args.frequency_tolerance_hz)
    mismatches: list[str] = []
    expected_start = _ghz_to_hz(args.expected_frequency_start_ghz)
    expected_stop = _ghz_to_hz(args.expected_frequency_stop_ghz)
    expected_step = _ghz_to_hz(args.expected_frequency_step_ghz)
    if expected_start is not None and abs(float(freqs[0]) - expected_start) > tol:
        mismatches.append(f"start expected={expected_start} actual={float(freqs[0])}")
    if expected_stop is not None and abs(float(freqs[-1]) - expected_stop) > tol:
        mismatches.append(f"stop expected={expected_stop} actual={float(freqs[-1])}")
    if expected_step is not None and len(freqs) >= 2:
        actual_step = float(np.median(np.diff(freqs)))
        if abs(actual_step - expected_step) > tol:
            mismatches.append(f"step expected={expected_step} actual={actual_step}")
    if args.expected_frequency_points is not None and int(len(freqs)) != int(args.expected_frequency_points):
        mismatches.append(f"points expected={args.expected_frequency_points} actual={len(freqs)}")
    return mismatches


def _has_expected_frequency_args(args: argparse.Namespace) -> bool:
    return any(
        value is not None
        for value in (
            args.expected_frequency_start_ghz,
            args.expected_frequency_stop_ghz,
            args.expected_frequency_step_ghz,
            args.expected_frequency_points,
        )
    )


def _has_touchstone_sample_requirements(args: argparse.Namespace) -> bool:
    return (
        _has_expected_frequency_args(args)
        or args.expected_touchstone_ports is not None
        or bool(str(args.required_touchstone_extension or "").strip())
    )


def _has_emx_command_requirements(args: argparse.Namespace) -> bool:
    return (
        bool(args.require_emx_command)
        or bool(str(args.expected_port_mode or "").strip())
        or args.expected_pin_purpose is not None
    )


def _ghz_to_hz(value: float | None) -> float | None:
    return None if value is None else float(value) * 1.0e9


def _build_checks(
    run_dir: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    evals: list[EvalEvidence],
    touchstone_checks: dict[str, Any],
    emx_command_checks: dict[str, Any],
    clearance_audit: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    checks.append(_check(run_dir.is_dir(), "run directory", str(run_dir)))
    checks.append(_check(bool(manifest) and "_parse_error" not in manifest, "dataset manifest", _manifest_detail(manifest)))
    checks.append(_check(bool(rows), "dataset rows CSV", f"rows={len(rows)}"))
    checks.append(_check(bool(evals), "evaluation evidence", f"evaluations_or_rows={len(evals)}"))

    expected_count = _expected_count(args, manifest)
    ok_rows = sum(1 for row in rows if _truthy(row.get("ok", "true")))
    if expected_count is not None:
        checks.append(_check(ok_rows >= expected_count, "completed ok rows", f"expected>={expected_count}, actual={ok_rows}"))
        checks.append(_check(len(evals) >= expected_count, "evaluation count", f"expected>={expected_count}, actual={len(evals)}"))

    required_rows = [item for item in evals if item.row_ok is not False]
    if args.require_summary:
        missing = sum(1 for item in required_rows if item.summary_count <= 0)
        checks.append(_check(missing == 0 and bool(required_rows), "per-evaluation summary files", f"missing={missing}, checked={len(required_rows)}"))
    if args.require_summary and args.require_summary_ok:
        fail_count = sum(item.summary_fail_count for item in required_rows)
        unknown_count = sum(item.summary_unknown_count for item in required_rows)
        parse_error_count = sum(item.summary_parse_error_count for item in required_rows)
        ok_count = sum(item.summary_ok_count for item in required_rows)
        bad_count = fail_count + unknown_count + parse_error_count
        checks.append(
            _check(
                bad_count == 0 and ok_count >= len(required_rows) and bool(required_rows),
                "per-evaluation summary ok status",
                f"ok={ok_count}, fail={fail_count}, unknown={unknown_count}, parse_error={parse_error_count}",
            )
        )
    if args.require_touchstone:
        missing = sum(1 for item in required_rows if item.touchstone_count <= 0)
        checks.append(_check(missing == 0 and bool(required_rows), "per-evaluation Touchstone files", f"missing={missing}, checked={len(required_rows)}"))
        extension = str(args.required_touchstone_extension or "").strip().lower()
        if extension:
            bad_paths = [
                str(path)
                for item in required_rows
                for path in item.touchstone_paths
                if path.suffix.lower() != extension
            ]
            checks.append(
                _check(
                    not bad_paths and bool(required_rows),
                    "per-evaluation Touchstone extension",
                    f"required={extension}, bad_count={len(bad_paths)}",
                )
            )
    if args.require_layout_json:
        missing = sum(1 for item in required_rows if item.layout_json_count <= 0)
        checks.append(_check(missing == 0 and bool(required_rows), "per-evaluation layout JSON files", f"missing={missing}, checked={len(required_rows)}"))
    if args.require_gds:
        missing = sum(1 for item in required_rows if item.gds_count <= 0)
        checks.append(_check(missing == 0 and bool(required_rows), "per-evaluation GDS files", f"missing={missing}, checked={len(required_rows)}"))
    if args.require_layout_preview:
        missing = sum(1 for item in required_rows if item.preview_count <= 0)
        checks.append(_check(missing == 0 and bool(required_rows), "per-evaluation layout preview files", f"missing={missing}, checked={len(required_rows)}"))
    if args.require_geometry_quality:
        checks.extend(_manifest_geometry_quality_checks(manifest, args))
    if args.require_clearance_audit:
        checks.extend(_clearance_audit_checks(run_dir, clearance_audit, _expected_count(args, manifest)))
    if args.require_touchstone and _has_touchstone_sample_requirements(args):
        checks.append(
            _check(
                touchstone_checks.get("status") == "PASS" and touchstone_checks.get("checked_count", 0) > 0,
                "sampled Touchstone file/port/frequency",
                f"checked={touchstone_checks.get('checked_count')}, fail={touchstone_checks.get('fail_count')}",
            )
        )
    if _has_emx_command_requirements(args):
        missing = sum(1 for item in required_rows if item.emx_command_count <= 0)
        checks.append(
            _check(
                missing == 0 and bool(required_rows),
                "per-evaluation EMX command files",
                f"missing={missing}, checked={len(required_rows)}",
            )
        )
        failed_examples = [
            f"{Path(str(item.get('path', ''))).name}: {' | '.join(str(text) for text in item.get('mismatches', [])[:3])}"
            for item in emx_command_checks.get("checked", [])
            if item.get("status") == "FAIL"
        ]
        detail = f"checked={emx_command_checks.get('checked_count')}, fail={emx_command_checks.get('fail_count')}"
        if failed_examples:
            detail += f", examples={failed_examples[:3]}"
        checks.append(
            _check(
                emx_command_checks.get("status") == "PASS",
                "per-evaluation EMX command contract",
                detail,
            )
        )
    return checks


def _manifest_geometry_quality_checks(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    present = bool(manifest) and "_parse_error" not in manifest
    checks.append(_check(present, "manifest geometry quality evidence", _manifest_detail(manifest)))
    if not present:
        return checks

    expected_port_mode = str(args.expected_port_mode or "").strip()
    if expected_port_mode:
        checks.append(
            _check(
                manifest.get("port_mode") == expected_port_mode,
                "manifest port mode",
                f"expected={expected_port_mode}, actual={manifest.get('port_mode')}",
            )
        )
    if args.expected_pin_purpose is not None:
        actual_pin = _to_int(manifest.get("cadence_pin_purpose"))
        checks.append(
            _check(
                actual_pin == int(args.expected_pin_purpose),
                "manifest cadence pin purpose",
                f"expected={args.expected_pin_purpose}, actual={actual_pin}",
            )
        )
    if args.require_shield_enabled:
        checks.append(
            _check(
                bool(manifest.get("shield_enabled")),
                "manifest shield enabled",
                f"shield_enabled={manifest.get('shield_enabled')}",
            )
        )

    geometry = manifest.get("geometry_quality") or {}
    count = _to_int(geometry.get("geometry_check_count"))
    ok_count = _to_int(geometry.get("geometry_check_ok_count"))
    if count is None or ok_count is None or count <= 0:
        checks.append(_check(False, "manifest geometry check count", f"count={count}, ok={ok_count}"))
    else:
        fraction = ok_count / count
        checks.append(
            _check(
                fraction + 1.0e-12 >= float(args.min_geometry_pass_fraction),
                "manifest geometry check pass fraction",
                f"ok={ok_count}, count={count}, fraction={fraction:.6g}, required={args.min_geometry_pass_fraction:.6g}",
            )
        )

    angle_count = _to_int(geometry.get("angle_checked_count"))
    checks.append(
        _check(
            angle_count is not None and angle_count > 0,
            "manifest angle checked count",
            f"angle_checked_count={angle_count}",
        )
    )
    for key in ("primary_internal_angle_deg", "secondary_internal_angle_deg"):
        checks.append(
            _angle_range_check(
                geometry.get(key),
                f"manifest {key}",
                float(args.internal_angle_deg),
                float(args.angle_tolerance_deg),
            )
        )
    for key in ("primary_terminal_interface_angle_deg", "secondary_terminal_interface_angle_deg"):
        checks.append(
            _angle_range_check(
                geometry.get(key),
                f"manifest {key}",
                float(args.terminal_angle_deg),
                float(args.angle_tolerance_deg),
            )
        )
    return checks


def _angle_range_check(section: Any, name: str, expected: float, tolerance: float) -> dict[str, str]:
    values = _collect_angle_numbers(section)
    if not values:
        return _check(False, name, "no numeric angle evidence")
    low = min(values)
    high = max(values)
    ok = all(abs(value - expected) <= tolerance for value in values)
    detail = f"range={low:.12g}-{high:.12g} deg, expected={expected:g} +/- {tolerance:g}"
    return _check(ok, name, detail)


def _clearance_audit_checks(run_dir: Path, clearance_audit: dict[str, Any], expected_count: int | None) -> list[dict[str, str]]:
    path = run_dir / "final500_ground_clearance_audit.json"
    checks: list[dict[str, str]] = []
    present = path.is_file() and bool(clearance_audit) and "_parse_error" not in clearance_audit
    checks.append(_check(present, "raw clearance audit file", _clearance_audit_detail(path, clearance_audit)))
    if not present:
        return checks

    candidate_count = _to_int(clearance_audit.get("candidate_count"))
    pass_count = _to_int(clearance_audit.get("pass_count")) or 0
    reject_count = _to_int(clearance_audit.get("reject_count")) or 0
    missing_count = _to_int(clearance_audit.get("missing_or_other_count")) or 0
    records = list(clearance_audit.get("records") or [])
    checks.append(
        _check(
            candidate_count is not None and candidate_count > 0 and len(records) == candidate_count,
            "raw clearance audit records",
            f"candidate_count={candidate_count}, records={len(records)}",
        )
    )
    checks.append(
        _check(
            candidate_count is not None and pass_count + reject_count + missing_count == candidate_count,
            "raw clearance audit count accounting",
            f"pass={pass_count}, reject={reject_count}, missing={missing_count}, candidate_count={candidate_count}",
        )
    )
    if expected_count is not None:
        checks.append(
            _check(
                candidate_count == int(expected_count),
                "raw clearance audit expected count",
                f"expected={expected_count}, candidate_count={candidate_count}",
            )
        )
    checks.append(_check(missing_count == 0, "raw clearance audit missing/other count", f"missing_or_other_count={missing_count}"))
    return checks


def _expected_count(args: argparse.Namespace, manifest: dict[str, Any]) -> int | None:
    if args.expected_count is not None:
        return int(args.expected_count)
    for key in ("requested_count", "count"):
        value = manifest.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _check(condition: bool, name: str, detail: str) -> dict[str, str]:
    return {"status": "PASS" if condition else "FAIL", "name": name, "detail": detail}


def _overall_status(checks: list[dict[str, str]]) -> str:
    if any(check["status"] == "FAIL" for check in checks):
        return "INCOMPLETE"
    return "PASS"


def _manifest_detail(manifest: dict[str, Any]) -> str:
    if not manifest:
        return "missing"
    if "_parse_error" in manifest:
        return str(manifest["_parse_error"])
    bits = []
    for key in ("requested_count", "ok_count", "fail_count", "port_mode", "cadence_pin_purpose"):
        if key in manifest:
            bits.append(f"{key}={manifest[key]}")
    return ", ".join(bits) if bits else "present"


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "requested_count",
        "ok_count",
        "fail_count",
        "port_mode",
        "cadence_pin_purpose",
        "shield_enabled",
        "target_frequency_hz",
        "frequency_start_hz",
        "frequency_stop_hz",
        "frequency_step_hz",
        "band_points",
    )
    summary = {key: manifest.get(key) for key in keys if key in manifest}
    if isinstance(manifest.get("geometry_quality"), dict):
        geometry = manifest["geometry_quality"]
        summary["geometry_quality"] = {
            key: geometry.get(key)
            for key in (
                "geometry_check_count",
                "geometry_check_ok_count",
                "angle_checked_count",
                "primary_internal_angle_deg",
                "secondary_internal_angle_deg",
                "primary_terminal_interface_angle_deg",
                "secondary_terminal_interface_angle_deg",
            )
            if key in geometry
        }
    if "_parse_error" in manifest:
        summary["_parse_error"] = manifest["_parse_error"]
    return summary


def _rows_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    ok_count = sum(1 for row in rows if _truthy(row.get("ok", "true")))
    fail_count = len(rows) - ok_count
    touchstone_path_count = sum(1 for row in rows if (row.get("touchstone_path") or row.get("sparameter_path") or "").strip())
    return {
        "row_count": len(rows),
        "ok_count": ok_count,
        "fail_count": fail_count,
        "touchstone_path_count": touchstone_path_count,
    }


def _eval_summary(evals: list[EvalEvidence]) -> dict[str, Any]:
    required = [item for item in evals if item.row_ok is not False]
    return {
        "evidence_count": len(evals),
        "required_count": len(required),
        "directories_count": sum(1 for item in evals if item.directory is not None),
        "summary_file_count": sum(item.summary_count for item in evals),
        "summary_ok_count": sum(item.summary_ok_count for item in evals),
        "summary_fail_count": sum(item.summary_fail_count for item in evals),
        "summary_unknown_count": sum(item.summary_unknown_count for item in evals),
        "summary_parse_error_count": sum(item.summary_parse_error_count for item in evals),
        "touchstone_file_count": sum(item.touchstone_count for item in evals),
        "emx_command_file_count": sum(item.emx_command_count for item in evals),
        "layout_json_file_count": sum(item.layout_json_count for item in evals),
        "gds_file_count": sum(item.gds_count for item in evals),
        "preview_file_count": sum(item.preview_count for item in evals),
    }


def _clearance_audit_summary(clearance_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_count": clearance_audit.get("candidate_count"),
        "pass_count": clearance_audit.get("pass_count"),
        "reject_count": clearance_audit.get("reject_count"),
        "missing_or_other_count": clearance_audit.get("missing_or_other_count"),
        "record_count": len(clearance_audit.get("records") or []) if isinstance(clearance_audit.get("records"), list) else None,
        "selected_cache_key": (clearance_audit.get("selected") or {}).get("cache_key") if isinstance(clearance_audit.get("selected"), dict) else None,
        "selected_status": (clearance_audit.get("selected") or {}).get("status") if isinstance(clearance_audit.get("selected"), dict) else None,
        "_parse_error": clearance_audit.get("_parse_error"),
    }


def _clearance_audit_detail(path: Path, clearance_audit: dict[str, Any]) -> str:
    if not path.is_file():
        return f"missing: {path}"
    if "_parse_error" in clearance_audit:
        return str(clearance_audit["_parse_error"])
    return (
        f"candidate_count={clearance_audit.get('candidate_count')}, "
        f"pass={clearance_audit.get('pass_count')}, "
        f"reject={clearance_audit.get('reject_count')}, "
        f"missing={clearance_audit.get('missing_or_other_count')}"
    )


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_angle_numbers(value: Any) -> list[float]:
    return _collect_nested_numbers(value, skip_keys={"std"})


def _collect_nested_numbers(value: Any, *, skip_keys: set[str] | None = None) -> list[float]:
    numbers: list[float] = []
    skip = skip_keys or set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in skip:
                continue
            numbers.extend(_collect_nested_numbers(child, skip_keys=skip))
    elif isinstance(value, (list, tuple)):
        for child in value:
            numbers.extend(_collect_nested_numbers(child, skip_keys=skip))
    else:
        num = _to_float(value)
        if num is not None:
            numbers.append(num)
    return numbers


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "fail", "failed"}


def _truthy_any(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _truthy(None if value is None else str(value))


def _argument_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "expected_count": args.expected_count,
        "require_summary": args.require_summary,
        "require_summary_ok": args.require_summary_ok,
        "require_touchstone": args.require_touchstone,
        "require_layout_json": args.require_layout_json,
        "require_gds": args.require_gds,
        "require_layout_preview": args.require_layout_preview,
        "require_clearance_audit": args.require_clearance_audit,
        "require_geometry_quality": args.require_geometry_quality,
        "require_shield_enabled": args.require_shield_enabled,
        "min_geometry_pass_fraction": args.min_geometry_pass_fraction,
        "internal_angle_deg": args.internal_angle_deg,
        "terminal_angle_deg": args.terminal_angle_deg,
        "angle_tolerance_deg": args.angle_tolerance_deg,
        "require_emx_command": args.require_emx_command,
        "expected_port_mode": args.expected_port_mode,
        "expected_pin_purpose": args.expected_pin_purpose,
        "expected_touchstone_ports": args.expected_touchstone_ports,
        "required_touchstone_extension": args.required_touchstone_extension,
        "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
        "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
        "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
        "expected_frequency_points": args.expected_frequency_points,
        "max_touchstone_frequency_checks": args.max_touchstone_frequency_checks,
        "touchstone_seed": args.touchstone_seed,
    }


def _write_rows_csv(path: Path, evals: list[EvalEvidence]) -> None:
    fields = [
        "key",
        "directory",
        "row_ok",
        "row_touchstone_path",
        "summary_count",
        "summary_ok_count",
        "summary_fail_count",
        "summary_unknown_count",
        "summary_parse_error_count",
        "summary_error_examples",
        "touchstone_count",
        "touchstone_paths",
        "emx_command_count",
        "emx_command_paths",
        "layout_json_count",
        "gds_count",
        "preview_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in evals:
            writer.writerow(
                {
                    "key": item.key,
                    "directory": str(item.directory) if item.directory else "",
                    "row_ok": item.row_ok,
                    "row_touchstone_path": str(item.row_touchstone_path) if item.row_touchstone_path else "",
                    "summary_count": item.summary_count,
                    "summary_ok_count": item.summary_ok_count,
                    "summary_fail_count": item.summary_fail_count,
                    "summary_unknown_count": item.summary_unknown_count,
                    "summary_parse_error_count": item.summary_parse_error_count,
                    "summary_error_examples": " | ".join(item.summary_error_examples),
                    "touchstone_count": item.touchstone_count,
                    "touchstone_paths": " | ".join(str(path) for path in item.touchstone_paths),
                    "emx_command_count": item.emx_command_count,
                    "emx_command_paths": " | ".join(str(path) for path in item.emx_command_paths),
                    "layout_json_count": item.layout_json_count,
                    "gds_count": item.gds_count,
                    "preview_count": item.preview_count,
                }
            )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS Run Progress Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Run directory: `{summary['run_dir']}`",
        f"- Output directory: `{summary['out_dir']}`",
        "",
        "## Counts",
        "",
        f"- Rows: {summary['rows'].get('row_count', 0)} total, {summary['rows'].get('ok_count', 0)} ok, {summary['rows'].get('fail_count', 0)} failed",
        f"- Evaluation evidence: {summary['evaluations'].get('evidence_count', 0)} records",
        f"- Summary status: {summary['evaluations'].get('summary_ok_count', 0)} ok, {summary['evaluations'].get('summary_fail_count', 0)} failed, {summary['evaluations'].get('summary_unknown_count', 0)} unknown, {summary['evaluations'].get('summary_parse_error_count', 0)} parse errors",
        f"- Touchstones: {summary['evaluations'].get('touchstone_file_count', 0)} file references/files",
        f"- EMX command files: {summary['evaluations'].get('emx_command_file_count', 0)}",
        f"- Layout JSON: {summary['evaluations'].get('layout_json_file_count', 0)}",
        f"- GDS: {summary['evaluations'].get('gds_file_count', 0)}",
        f"- Layout previews: {summary['evaluations'].get('preview_file_count', 0)}",
        f"- Geometry quality: {summary['manifest'].get('geometry_quality', {})}",
        f"- Raw clearance audit: {summary['clearance_audit'].get('candidate_count')} candidates, {summary['clearance_audit'].get('pass_count')} pass, {summary['clearance_audit'].get('reject_count')} reject, {summary['clearance_audit'].get('missing_or_other_count')} missing/other",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(
        [
            "",
            "## EMX Command Contract",
            "",
            f"- Status: {summary['emx_command_checks']['status']}",
            f"- Checked: {summary['emx_command_checks']['checked_count']}",
            f"- Failed: {summary['emx_command_checks']['fail_count']}",
            "",
            "## Touchstone File/Port/Frequency Sample",
            "",
            f"- Status: {summary['touchstone_frequency_checks']['status']}",
            f"- Discovered: {summary['touchstone_frequency_checks']['discovered_count']}",
            f"- Checked: {summary['touchstone_frequency_checks']['checked_count']}",
            f"- Failed: {summary['touchstone_frequency_checks']['fail_count']}",
            "",
            "This audit is intentionally conservative: incomplete evidence is not a physics failure, but it is not ready for final transfer or training acceptance.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
