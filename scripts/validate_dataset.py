#!/usr/bin/env python3
"""Validate RFIC transformer dataset artifacts.

The validator is intentionally read-only: it consumes dataset_manifest.json and
dataset_rows.csv from one sample-dataset output directory, then writes a compact
Markdown report and machine-readable JSON summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


@dataclass(frozen=True)
class FrequencySpec:
    start_hz: float | None = None
    stop_hz: float | None = None
    step_hz: float | None = None
    points: int | None = None

    def available(self) -> bool:
        return self.start_hz is not None or self.stop_hz is not None or self.step_hz is not None or self.points is not None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    manifest_path = dataset_dir / "dataset_manifest.json"
    rows_path = dataset_dir / "dataset_rows.csv"

    checks: list[Check] = []
    manifest = _load_json(manifest_path, checks)
    rows = _load_rows(rows_path, checks)

    if manifest is None:
        _write_outputs(dataset_dir, checks, {}, rows, args)
        return 2

    expected_count = _as_int(manifest.get("requested_count"))
    ok_count = _as_int(manifest.get("ok_count"))
    fail_count = _as_int(manifest.get("fail_count"))
    _check_counts(checks, manifest, rows, expected_count, ok_count, fail_count)
    _check_config(checks, manifest, args)
    _check_uniformity(checks, manifest, args)
    _check_geometry(checks, manifest, ok_count, args)
    _check_sparameters(checks, manifest, ok_count, args)
    _check_zin(checks, manifest, ok_count, args)
    _check_rows(checks, rows, manifest, args)

    _write_outputs(dataset_dir, checks, manifest, rows, args)
    status = _overall_status(checks)
    return 2 if status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="sample-dataset output directory")
    parser.add_argument("--require-emx", action="store_true", help="Fail when EMX/S-parameter labels are absent")
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--max-passivity-excess", type=float, default=1.0e-3)
    parser.add_argument("--max-reciprocity-error", type=float, default=1.0e-9)
    parser.add_argument("--max-passivity-sigma", type=float, default=1.001)
    parser.add_argument("--max-angle-dev-deg", type=float, default=1.0e-6)
    parser.add_argument("--max-correlation", type=float, default=0.35)
    parser.add_argument("--max-histogram-imbalance-frac", type=float, default=0.20)
    parser.add_argument("--min-zin-real-span", type=float, default=None)
    parser.add_argument("--min-zin-imag-span", type=float, default=None)
    parser.add_argument("--min-zin-abs-span", type=float, default=None)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-points", type=int, default=None)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument(
        "--max-touchstone-frequency-checks",
        type=int,
        default=50,
        help="Maximum accessible Touchstone files to open for frequency-grid checks; use 0 to check all.",
    )
    parser.add_argument("--report", default=None, help="Markdown report path")
    parser.add_argument("--summary", default=None, help="JSON summary path")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _load_json(path: Path, checks: list[Check]) -> dict[str, Any] | None:
    if not path.exists():
        checks.append(Check("FAIL", "manifest exists", f"Missing {path}"))
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive report path
        checks.append(Check("FAIL", "manifest parses", f"{path}: {exc}"))
        return None
    checks.append(Check("PASS", "manifest exists", str(path)))
    return data


def _load_rows(path: Path, checks: list[Check]) -> list[dict[str, str]]:
    if not path.exists():
        checks.append(Check("FAIL", "CSV exists", f"Missing {path}"))
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:  # pragma: no cover - defensive report path
        checks.append(Check("FAIL", "CSV parses", f"{path}: {exc}"))
        return []
    checks.append(Check("PASS", "CSV exists", f"{path} ({len(rows)} rows)"))
    return rows


def _check_counts(
    checks: list[Check],
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    expected_count: int | None,
    ok_count: int | None,
    fail_count: int | None,
) -> None:
    if expected_count is None or ok_count is None or fail_count is None:
        checks.append(Check("FAIL", "manifest counts", "requested_count/ok_count/fail_count missing"))
        return
    if ok_count + fail_count != expected_count:
        checks.append(Check("FAIL", "manifest counts", f"ok+fail={ok_count + fail_count}, requested={expected_count}"))
    else:
        checks.append(Check("PASS", "manifest counts", f"requested={expected_count}, ok={ok_count}, fail={fail_count}"))
    if len(rows) != expected_count:
        checks.append(Check("FAIL", "CSV row count", f"rows={len(rows)}, requested={expected_count}"))
    else:
        checks.append(Check("PASS", "CSV row count", f"rows={len(rows)}"))
    row_ok = sum(1 for row in rows if _as_bool(row.get("ok")))
    if rows and row_ok != ok_count:
        checks.append(Check("FAIL", "CSV ok count", f"CSV ok={row_ok}, manifest ok={ok_count}"))
    elif rows:
        checks.append(Check("PASS", "CSV ok count", f"CSV ok={row_ok}"))


def _check_config(checks: list[Check], manifest: dict[str, Any], args: argparse.Namespace) -> None:
    port = manifest.get("port_mode")
    pin = _as_int(manifest.get("cadence_pin_purpose"))
    if port == args.expected_port_mode:
        checks.append(Check("PASS", "port mode", str(port)))
    else:
        checks.append(Check("FAIL", "port mode", f"got {port!r}, expected {args.expected_port_mode!r}"))
    if pin == args.expected_pin_purpose:
        checks.append(Check("PASS", "cadence pin purpose", str(pin)))
    else:
        checks.append(Check("FAIL", "cadence pin purpose", f"got {pin}, expected {args.expected_pin_purpose}"))


def _check_uniformity(checks: list[Check], manifest: dict[str, Any], args: argparse.Namespace) -> None:
    uniformity = _as_dict(manifest.get("uniformity"))
    fields = _as_dict(uniformity.get("fields"))
    bins = _as_int(uniformity.get("bins")) or 10
    count = _as_int(uniformity.get("count")) or 0
    if not fields:
        checks.append(Check("FAIL", "uniformity fields", "No per-field histogram data"))
        return
    expected = count / bins if bins else 0.0
    worst_name = ""
    worst_imbalance = -1.0
    for name, item in fields.items():
        info = _as_dict(item)
        lo = _as_int(info.get("histogram_min"))
        hi = _as_int(info.get("histogram_max"))
        if lo is None or hi is None:
            continue
        imbalance = max(abs(lo - expected), abs(hi - expected))
        if imbalance > worst_imbalance:
            worst_name = str(name)
            worst_imbalance = imbalance
    allowed = max(1.0, expected * float(args.max_histogram_imbalance_frac))
    if worst_imbalance <= allowed:
        checks.append(Check("PASS", "marginal uniformity", f"worst={worst_name}, imbalance={worst_imbalance:.3g}, allowed={allowed:.3g}"))
    else:
        checks.append(Check("WARN", "marginal uniformity", f"worst={worst_name}, imbalance={worst_imbalance:.3g}, allowed={allowed:.3g}"))

    space = _as_dict(uniformity.get("space_filling"))
    corr_max = _summary_value(space, "pairwise_abs_correlation", "max")
    if corr_max is None:
        checks.append(Check("WARN", "space-filling correlation", "Not available"))
    elif count < 50:
        checks.append(Check("WARN", "space-filling correlation", f"max={corr_max:.4g}; sample count {count} is too small for a hard decision"))
    elif corr_max <= args.max_correlation:
        checks.append(Check("PASS", "space-filling correlation", f"max={corr_max:.4g}, limit={args.max_correlation:.4g}"))
    else:
        checks.append(Check("WARN", "space-filling correlation", f"max={corr_max:.4g}, limit={args.max_correlation:.4g}"))


def _check_geometry(checks: list[Check], manifest: dict[str, Any], ok_count: int | None, args: argparse.Namespace) -> None:
    geom = _as_dict(manifest.get("geometry_quality"))
    if not geom:
        checks.append(Check("FAIL", "geometry manifest", "Missing geometry_quality"))
        return
    checked = _as_int(geom.get("angle_checked_count"))
    geom_ok = _as_int(geom.get("geometry_check_ok_count"))
    if ok_count is not None and checked == ok_count and geom_ok == ok_count:
        checks.append(Check("PASS", "geometry check count", f"angle_checked={checked}, geometry_ok={geom_ok}"))
    else:
        checks.append(Check("FAIL", "geometry check count", f"angle_checked={checked}, geometry_ok={geom_ok}, ok_count={ok_count}"))

    for key in ("primary_internal_angle_deg", "secondary_internal_angle_deg"):
        _check_angle_summary(checks, geom, key, allowed=(135.0,), tolerance=args.max_angle_dev_deg)
    for key in ("primary_terminal_interface_angle_deg", "secondary_terminal_interface_angle_deg"):
        _check_angle_summary(checks, geom, key, allowed=(90.0, 135.0), tolerance=args.max_angle_dev_deg)


def _check_angle_summary(
    checks: list[Check],
    geom: dict[str, Any],
    key: str,
    *,
    allowed: tuple[float, ...],
    tolerance: float,
) -> None:
    section = _as_dict(geom.get(key))
    values = []
    for side in ("min", "max"):
        summary = _as_dict(section.get(side))
        for metric in ("min", "max"):
            value = _as_float(summary.get(metric))
            if value is not None:
                values.append(value)
    if not values:
        checks.append(Check("WARN", key, "No angle values"))
        return
    bad = [value for value in values if min(abs(value - item) for item in allowed) > tolerance]
    if bad:
        checks.append(Check("FAIL", key, f"bad={bad[:4]}, allowed={allowed}, tolerance={tolerance}"))
    else:
        checks.append(Check("PASS", key, f"range={min(values):.6g}..{max(values):.6g}, allowed={allowed}"))


def _check_sparameters(checks: list[Check], manifest: dict[str, Any], ok_count: int | None, args: argparse.Namespace) -> None:
    spq = _as_dict(manifest.get("sparameter_quality"))
    valid = _as_int(spq.get("valid_sparameter_count"))
    if valid is None or valid == 0:
        status = "FAIL" if args.require_emx else "WARN"
        checks.append(Check(status, "S-parameter labels", "No valid S-parameter rows"))
        return
    if ok_count is not None and valid != ok_count:
        checks.append(Check("FAIL", "S-parameter label count", f"valid={valid}, ok_count={ok_count}"))
    else:
        checks.append(Check("PASS", "S-parameter label count", f"valid={valid}"))

    reciprocity_max = _summary_value(spq, "reciprocity_error_abs", "max")
    _limit_check(checks, "reciprocity error", reciprocity_max, args.max_reciprocity_error, fail=True)
    excess_max = _summary_value(spq, "passivity_excess", "max")
    _limit_check(checks, "passivity excess", excess_max, args.max_passivity_excess, fail=True)
    sigma_max = _summary_value(spq, "passivity_sigma_max", "max")
    _limit_check(checks, "passivity sigma max", sigma_max, args.max_passivity_sigma, fail=True)
    excess_count = _as_int(spq.get("passivity_excess_count_gt_1e_3"))
    if excess_count == 0:
        checks.append(Check("PASS", "passivity excess count", "0 above 1e-3"))
    else:
        checks.append(Check("FAIL", "passivity excess count", f"{excess_count} above 1e-3"))


def _check_zin(checks: list[Check], manifest: dict[str, Any], ok_count: int | None, args: argparse.Namespace) -> None:
    zin = _as_dict(manifest.get("zin_coverage"))
    valid = _as_int(zin.get("valid_zin_count"))
    if valid is None or valid == 0:
        status = "FAIL" if args.require_emx else "WARN"
        checks.append(Check(status, "Zin labels", "No valid Zin rows"))
        return
    if ok_count is not None and valid != ok_count:
        checks.append(Check("FAIL", "Zin label count", f"valid={valid}, ok_count={ok_count}"))
    else:
        checks.append(Check("PASS", "Zin label count", f"valid={valid}"))
    _span_check(checks, "center Zin real span", zin, "real_ohm", args.min_zin_real_span)
    _span_check(checks, "center Zin imag span", zin, "imag_ohm", args.min_zin_imag_span)
    _span_check(checks, "center Zin abs span", zin, "abs_ohm", args.min_zin_abs_span)
    _span_check(checks, "full-band Zin real span", zin, "band_real_max_ohm", None)
    _span_check(checks, "full-band Zin imag span", zin, "band_imag_max_ohm", None)
    _span_check(checks, "full-band Zin abs span", zin, "band_abs_max_ohm", None)


def _check_rows(checks: list[Check], rows: list[dict[str, str]], manifest: dict[str, Any], args: argparse.Namespace) -> None:
    if not rows:
        return
    numeric_prefixes = (
        "geometry_check__",
        "zin_",
        "zdd",
        "sparam_",
        "sdd",
        "metrics__",
        "objective__",
    )
    bad_numeric = []
    for index, row in enumerate(rows):
        for key, value in row.items():
            if not value or not key.startswith(numeric_prefixes):
                continue
            if _is_allowed_non_numeric_validation_value(key, value):
                continue
            if _as_float(value) is None:
                bad_numeric.append((index, key, value))
                if len(bad_numeric) >= 5:
                    break
        if len(bad_numeric) >= 5:
            break
    if bad_numeric:
        checks.append(Check("FAIL", "CSV numeric fields", f"Non-numeric examples: {bad_numeric}"))
    else:
        checks.append(Check("PASS", "CSV numeric fields", "All populated numeric validation fields parse"))

    if args.require_emx:
        ok_rows = [(index, row) for index, row in enumerate(rows) if _as_bool(row.get("ok"))]
        if not ok_rows:
            checks.append(Check("WARN", "touchstone files", "No ok rows; Touchstone path check is not applicable"))
            return
        expected_frequency = _expected_frequency_spec(manifest, args)
        _check_row_frequency_metadata(checks, ok_rows, expected_frequency, args)
        missing_touchstone = []
        accessible_touchstones = []
        for index, row in ok_rows:
            path_text = row.get("touchstone_path") or ""
            if not path_text or path_text == "None":
                missing_touchstone.append((index, "empty"))
                continue
            path = Path(path_text)
            if not path.exists():
                missing_touchstone.append((index, path_text))
                continue
            accessible_touchstones.append((index, path))
            if len(missing_touchstone) >= 5:
                break
        if missing_touchstone:
            checks.append(Check("WARN", "touchstone files", f"Missing or inaccessible examples: {missing_touchstone}"))
        else:
            checks.append(Check("PASS", "touchstone files", "All ok rows point to accessible Touchstone files"))
        _check_touchstone_frequency_grid(checks, accessible_touchstones, expected_frequency, args)


def _write_outputs(
    dataset_dir: Path,
    checks: list[Check],
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    args: argparse.Namespace,
) -> None:
    report_path = Path(args.report).expanduser().resolve() if args.report else dataset_dir / "validation_report.md"
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else dataset_dir / "validation_summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    overall = _overall_status(checks)
    summary = {
        "overall_status": overall,
        "dataset_dir": str(dataset_dir),
        "checks": [check.__dict__ for check in checks],
        "counts": {
            "requested_count": manifest.get("requested_count"),
            "ok_count": manifest.get("ok_count"),
            "fail_count": manifest.get("fail_count"),
            "csv_rows": len(rows),
        },
        "port_mode": manifest.get("port_mode"),
        "cadence_pin_purpose": manifest.get("cadence_pin_purpose"),
        "zin_coverage": manifest.get("zin_coverage"),
        "sparameter_quality": manifest.get("sparameter_quality"),
        "geometry_quality": manifest.get("geometry_quality"),
        "uniformity_space_filling": _as_dict(manifest.get("uniformity")).get("space_filling") if manifest else None,
        "target_frequency": manifest.get("target_frequency"),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={overall}")
    print(f"report={report_path}")
    print(f"summary={summary_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")


def _render_report(summary: dict[str, Any]) -> str:
    checks = [Check(**item) for item in summary["checks"]]
    lines = [
        "# RFIC Transformer Dataset Validation Report",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Counts: `{summary['counts']}`",
        f"- Port mode: `{summary.get('port_mode')}`",
        f"- Cadence pin purpose: `{summary.get('cadence_pin_purpose')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        detail = check.detail.replace("|", "\\|")
        lines.append(f"| {check.status} | {check.name} | {detail} |")
    lines.extend(
        [
            "",
            "## Key Manifest Sections",
            "",
            "### Zin Coverage",
            "```json",
            json.dumps(summary.get("zin_coverage"), indent=2),
            "```",
            "",
            "### S-Parameter Quality",
            "```json",
            json.dumps(summary.get("sparameter_quality"), indent=2),
            "```",
            "",
            "### Geometry Quality",
            "```json",
            json.dumps(summary.get("geometry_quality"), indent=2),
            "```",
            "",
            "### Space Filling",
            "```json",
            json.dumps(summary.get("uniformity_space_filling"), indent=2),
            "```",
            "",
            "### Target Frequency",
            "```json",
            json.dumps(summary.get("target_frequency"), indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _expected_frequency_spec(manifest: dict[str, Any], args: argparse.Namespace) -> FrequencySpec:
    target = _as_dict(manifest.get("target_frequency"))
    return FrequencySpec(
        start_hz=_ghz_to_hz(args.expected_frequency_start_ghz)
        if args.expected_frequency_start_ghz is not None
        else _as_float(target.get("start_hz")),
        stop_hz=_ghz_to_hz(args.expected_frequency_stop_ghz)
        if args.expected_frequency_stop_ghz is not None
        else _as_float(target.get("stop_hz")),
        step_hz=_ghz_to_hz(args.expected_frequency_step_ghz)
        if args.expected_frequency_step_ghz is not None
        else _as_float(target.get("step_hz")),
        points=args.expected_frequency_points
        if args.expected_frequency_points is not None
        else _as_int(target.get("points")),
    )


def _check_row_frequency_metadata(
    checks: list[Check],
    ok_rows: list[tuple[int, dict[str, str]]],
    expected: FrequencySpec,
    args: argparse.Namespace,
) -> None:
    if not expected.available():
        checks.append(Check("WARN", "CSV frequency metadata", "No expected frequency grid in args or manifest target_frequency"))
        return
    checked = 0
    bad: list[tuple[int, str]] = []
    for index, row in ok_rows:
        row_spec = FrequencySpec(
            start_hz=_as_float(row.get("sparam_freq_start_hz")),
            stop_hz=_as_float(row.get("sparam_freq_stop_hz")),
            step_hz=_as_float(row.get("sparam_freq_step_hz")),
            points=_as_int(row.get("sparam_freq_points")),
        )
        if not row_spec.available():
            bad.append((index, "missing sparam_freq_* columns"))
            if len(bad) >= 5:
                break
            continue
        checked += 1
        mismatch = _frequency_mismatch_detail(row_spec, expected, args.frequency_tolerance_hz)
        if mismatch:
            bad.append((index, mismatch))
            if len(bad) >= 5:
                break
    if bad:
        checks.append(Check("FAIL", "CSV frequency metadata", f"Bad examples: {bad}"))
    else:
        checks.append(Check("PASS", "CSV frequency metadata", f"{checked} ok rows match expected frequency grid"))


def _check_touchstone_frequency_grid(
    checks: list[Check],
    paths: list[tuple[int, Path]],
    expected: FrequencySpec,
    args: argparse.Namespace,
) -> None:
    if not expected.available():
        checks.append(Check("WARN", "Touchstone frequency coverage", "No expected frequency grid in args or manifest target_frequency"))
        return
    if not paths:
        checks.append(Check("WARN", "Touchstone frequency coverage", "No accessible Touchstone files to inspect"))
        return
    limit = int(args.max_touchstone_frequency_checks)
    selected = paths if limit == 0 else paths[: max(0, limit)]
    bad: list[tuple[int, str]] = []
    for index, path in selected:
        try:
            freqs_hz = _load_touchstone_frequencies_hz(path)
        except Exception as exc:
            bad.append((index, f"{path}: {exc}"))
            if len(bad) >= 5:
                break
            continue
        actual = _frequency_spec_from_array(freqs_hz)
        mismatch = _frequency_mismatch_detail(actual, expected, args.frequency_tolerance_hz)
        if mismatch:
            bad.append((index, f"{path}: {mismatch}"))
            if len(bad) >= 5:
                break
    if bad:
        checks.append(Check("FAIL", "Touchstone frequency coverage", f"Bad examples: {bad}"))
    else:
        scope = "all" if limit == 0 or len(selected) == len(paths) else f"{len(selected)} of {len(paths)}"
        checks.append(Check("PASS", "Touchstone frequency coverage", f"Checked {scope} accessible Touchstone files"))


def _frequency_spec_from_array(freqs_hz: list[float]) -> FrequencySpec:
    if not freqs_hz:
        return FrequencySpec()
    if len(freqs_hz) < 2:
        return FrequencySpec(start_hz=freqs_hz[0], stop_hz=freqs_hz[-1], points=len(freqs_hz))
    diffs = [freqs_hz[i + 1] - freqs_hz[i] for i in range(len(freqs_hz) - 1)]
    return FrequencySpec(start_hz=freqs_hz[0], stop_hz=freqs_hz[-1], step_hz=diffs[0], points=len(freqs_hz))


def _frequency_mismatch_detail(actual: FrequencySpec, expected: FrequencySpec, tolerance_hz: float) -> str | None:
    mismatches = []
    tol = max(float(tolerance_hz), 0.0)
    for name in ("start_hz", "stop_hz", "step_hz"):
        want = getattr(expected, name)
        got = getattr(actual, name)
        if want is not None:
            if got is None or abs(float(got) - float(want)) > tol:
                mismatches.append(f"{name}: got={got}, expected={want}")
    if expected.points is not None and actual.points != expected.points:
        mismatches.append(f"points: got={actual.points}, expected={expected.points}")
    return "; ".join(mismatches) if mismatches else None


def _load_touchstone_frequencies_hz(path: Path) -> list[float]:
    match = re.search(r"\.s(\d+)p$", path.name, re.IGNORECASE)
    n_ports = int(match.group(1)) if match else 2
    expected_values_per_block = 1 + 2 * n_ports * n_ports
    freq_unit = 1.0
    rows: list[list[float]] = []
    current_row: list[float] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("!")[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line[1:].lower().split()
            for part in parts:
                if part in ("hz", "khz", "mhz", "ghz"):
                    freq_unit = {"hz": 1.0, "khz": 1.0e3, "mhz": 1.0e6, "ghz": 1.0e9}[part]
            continue
        current_row.extend(float(item) for item in line.split())
        while len(current_row) >= expected_values_per_block:
            rows.append(current_row[:expected_values_per_block])
            current_row = current_row[expected_values_per_block:]
    if not rows:
        raise ValueError("no numeric Touchstone data")
    return [float(row[0]) * freq_unit for row in rows]


def _overall_status(checks: list[Check]) -> str:
    if any(check.status == "FAIL" for check in checks):
        return "FAIL"
    if any(check.status == "WARN" for check in checks):
        return "WARN"
    return "PASS"


def _limit_check(checks: list[Check], name: str, value: float | None, limit: float, *, fail: bool) -> None:
    if value is None:
        checks.append(Check("WARN", name, "Not available"))
    elif value <= limit:
        checks.append(Check("PASS", name, f"max={value:.6g}, limit={limit:.6g}"))
    else:
        checks.append(Check("FAIL" if fail else "WARN", name, f"max={value:.6g}, limit={limit:.6g}"))


def _span_check(checks: list[Check], name: str, container: dict[str, Any], key: str, minimum: float | None) -> None:
    summary = _as_dict(container.get(key))
    lo = _as_float(summary.get("min"))
    hi = _as_float(summary.get("max"))
    if lo is None or hi is None:
        checks.append(Check("WARN", name, f"{key} not available"))
        return
    span = hi - lo
    if minimum is None or span >= minimum:
        checks.append(Check("PASS", name, f"{lo:.6g}..{hi:.6g}, span={span:.6g}"))
    else:
        checks.append(Check("WARN", name, f"{lo:.6g}..{hi:.6g}, span={span:.6g}, target>={minimum:.6g}"))


def _summary_value(container: dict[str, Any], key: str, metric: str) -> float | None:
    return _as_float(_as_dict(container.get(key)).get(metric))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        item = float(value)
    except (TypeError, ValueError):
        return None
    return item if math.isfinite(item) else None


def _ghz_to_hz(value: float) -> float:
    return float(value) * 1.0e9


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _is_allowed_non_numeric_validation_value(key: str, value: str) -> bool:
    text = value.strip().lower()
    if text in {"true", "false", "none", "nan"}:
        return True
    if key in {"geometry_check__topology_mode", "geometry_check__backend"}:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
