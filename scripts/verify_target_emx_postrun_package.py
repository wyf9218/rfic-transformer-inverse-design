#!/usr/bin/env python3
"""Verify a pulled target-EMX post-run validation package.

This is the local counterpart to target_emx_wideband_postrun_validation.commands.sh.
It verifies the validation tarball produced on MARS, checks the two physics
gates, and optionally proves that a downloaded EMX .s4p matches the SHA recorded
on MARS. It does not generate or alter simulator data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUT_DIR = Path(
    "/home/researcher/Documents/模拟变压器AI反向建模/hfss_validation/final500_ec6698dfc575950b/target_emx_postrun_import_20260613"
)
MIN_VALIDATION_PNG_WIDTH = 640
MIN_VALIDATION_PNG_HEIGHT = 360
MIN_VALIDATION_PNG_BYTES = 2048
MIN_VALIDATION_PNG_COLOR_DELTA = 2


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tarball", required=True, help="MARS validation tarball, e.g. validation_20260613_transfer.tar.gz")
    parser.add_argument("--sha-record", help="Optional tarball .sha256 file; defaults to <tarball>.sha256")
    parser.add_argument("--emx-s4p", help="Optional downloaded EMX .s4p to compare with the MARS-recorded SHA")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--expected-frequency-points", type=int, default=451)
    parser.add_argument("--expected-min-target-abs-k", type=float, default=0.05)
    parser.add_argument("--expected-min-window-abs-k", type=float, default=0.05)
    parser.add_argument("--expected-physical-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-physical-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--expected-shape-window-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-shape-window-stop-ghz", type=float, default=30.0)
    parser.add_argument("--expected-max-shape-spike-ratio", type=float, default=4.0)
    parser.add_argument("--expected-max-shape-relative-step", type=float, default=0.25)
    parser.add_argument("--expected-approved-port-pairs", default="1,2:3,4")
    parser.add_argument("--expected-port-pair-count", type=int, default=24)
    parser.add_argument("--expected-port-pair-metric-count", type=int, default=6)
    parser.add_argument("--expected-photo-max-percent-error", type=float, default=5.0)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--require-emx-s4p", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tarball = Path(args.tarball).expanduser().resolve()
    sha_record = Path(args.sha_record).expanduser().resolve() if args.sha_record else Path(str(tarball) + ".sha256")
    emx_s4p = Path(args.emx_s4p).expanduser().resolve() if args.emx_s4p else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    errors: list[str] = []
    validation_root: Path | None = None
    touchstone_summary: dict[str, Any] = {}
    emx_first_summary: dict[str, Any] = {}
    mars_emx_sha: str | None = None

    try:
        checks.extend(_tarball_checks(tarball, sha_record))
        validation_root = _extract_validation_tarball(tarball, out_dir)
        checks.append(Check("PASS", "validation tar extract", str(validation_root)))
        checks.extend(_required_artifact_checks(validation_root, args))
        touchstone_summary = _read_json(validation_root / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json")
        emx_first_summary = _read_json(validation_root / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json")
        checks.extend(_touchstone_gate_checks(touchstone_summary, args))
        checks.extend(_emx_first_gate_checks(emx_first_summary, args, validation_root))
        mars_emx_sha = _read_first_sha(validation_root / "emx_wideband.s4p.sha256")
        checks.extend(_local_emx_sha_checks(emx_s4p, mars_emx_sha, require_emx_s4p=bool(args.require_emx_s4p)))
    except Exception as exc:  # noqa: BLE001 - persist exact audit failure.
        errors.append(f"{type(exc).__name__}: {exc}")
        checks.append(Check("FAIL", "post-run package verification execution", errors[-1]))

    failed = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    overall_status = "FAIL" if failed else "PASS"
    decision = _decision(overall_status, warnings, emx_s4p, mars_emx_sha)
    accepted_bundle = _accepted_emx_reference_bundle(validation_root, emx_s4p, mars_emx_sha, decision)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "tarball": _file_record(tarball),
        "sha_record": _file_record(sha_record),
        "emx_s4p": _file_record(emx_s4p) if emx_s4p else None,
        "validation_root": str(validation_root) if validation_root else None,
        "mars_emx_sha256": mars_emx_sha,
        "touchstone_summary": str(validation_root / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json") if validation_root else None,
        "emx_first_summary": str(validation_root / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json") if validation_root else None,
        "accepted_emx_reference_bundle": accepted_bundle,
        "checks": [check.__dict__ for check in checks],
        "status_counts": _status_counts(checks),
        "method_notes": [
            "This verifier proves transfer/package integrity and gate summaries only; it does not rerun EMX, HFSS, or ADS.",
            "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS requires the MARS post-run Touchstone gate, the EMX-first gate, and a local EMX .s4p whose SHA256 matches the MARS-recorded SHA.",
            "If the local EMX .s4p is missing, the validation evidence may be intact but the reference cannot yet be imported into the desktop ADS/HFSS workflow.",
            "When accepted, accepted_emx_reference_bundle is the only structured EMX artifact bundle that downstream HFSS/ADS validation runners should consume.",
        ],
        "errors": errors,
    }
    summary_path = out_dir / "target_emx_postrun_import_summary.json"
    report_path = out_dir / "target_emx_postrun_import_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _tarball_checks(tarball: Path, sha_record: Path) -> list[Check]:
    checks: list[Check] = []
    if tarball.is_file() and tarball.stat().st_size > 0:
        checks.append(Check("PASS", "validation tarball exists", f"{tarball} ({tarball.stat().st_size} bytes)"))
    else:
        checks.append(Check("FAIL", "validation tarball exists", f"missing or empty: {tarball}"))
        return checks
    if not sha_record.is_file():
        checks.append(Check("FAIL", "validation tarball SHA record", f"missing: {sha_record}"))
        return checks
    expected = sha_record.read_text(encoding="utf-8").split()[0]
    actual = _sha256(tarball)
    status = "PASS" if expected == actual else "FAIL"
    checks.append(Check(status, "validation tarball SHA", f"expected={expected}, actual={actual}"))
    return checks


def _extract_validation_tarball(tarball: Path, out_dir: Path) -> Path:
    extract_dir = out_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as archive:
        safe, reason = _safe_extractall(archive, extract_dir)
    if not safe:
        raise ValueError(reason)
    dirs = [path for path in extract_dir.iterdir() if path.is_dir()]
    if len(dirs) == 1:
        return dirs[0]
    if (extract_dir / "touchstone_physical_gate").is_dir() and (extract_dir / "emx_first_validation_gate_20260613").is_dir():
        return extract_dir
    raise ValueError(f"could not locate validation root after extracting {tarball}")


def _safe_extractall(archive: tarfile.TarFile, destination: Path) -> tuple[bool, str]:
    for member in archive.getmembers():
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            return False, f"unsafe tar member path: {member.name}"
        if member.issym() or member.islnk():
            return False, f"tar member link is not allowed: {member.name}"
    _safe_extractall_compat(archive, destination)
    return True, "extracted"


def _safe_extractall_compat(archive: tarfile.TarFile, destination: Path) -> None:
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        # Python < 3.12 has no extraction filter argument. Path/link safety is
        # already enforced by _safe_extractall before this fallback is reached.
        archive.extractall(destination)


def _required_artifact_checks(root: Path, args: argparse.Namespace) -> list[Check]:
    required = (
        "emx_wideband.s4p.sha256",
        "touchstone_physical_gate/touchstone_transformer_audit_summary.json",
        "touchstone_physical_gate/touchstone_transformer_audit_report.md",
        "touchstone_physical_gate/touchstone_transformer_metrics.csv",
        "touchstone_physical_gate/touchstone_ads_equivalent_metrics.png",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_summary.json",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_report.md",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_metrics.csv",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_ads_style_metrics.png",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_core_metrics.png",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_port_pair_sensitivity.csv",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_port_pair_sensitivity.png",
    )
    missing = [item for item in required if not (root / item).is_file()]
    if missing:
        return [Check("FAIL", "post-run validation artifacts", f"missing={missing}")]
    empty = [item for item in required if (root / item).stat().st_size <= 0]
    if empty:
        return [Check("FAIL", "post-run validation artifacts", f"empty={empty}")]
    checks = [Check("PASS", "post-run validation artifacts", f"{len(required)} required non-empty files present")]
    checks.extend(_artifact_content_checks(root, args))
    return checks


def _artifact_content_checks(root: Path, args: argparse.Namespace) -> list[Check]:
    csv_requirements = {
        "touchstone_physical_gate/touchstone_transformer_metrics.csv": (
            "freq_hz",
            "freq_ghz",
            "lp_nh",
            "ls_nh",
            "m_nh",
            "k",
            "qp",
            "qs",
        ),
        "emx_first_validation_gate_20260613/emx_first_validation_gate_metrics.csv": (
            "freq_hz",
            "freq_ghz",
            "lp_nh",
            "ls_nh",
            "k",
            "qp",
            "qs",
            "cm_single_primary_ff",
        ),
        "emx_first_validation_gate_20260613/emx_first_validation_gate_port_pair_sensitivity.csv": (
            "port_pairs",
            "overall_status",
            "pass_count",
            "metric_count",
            "max_percent_error",
            "mean_percent_error",
        ),
    }
    csv_numeric_columns = {
        "touchstone_physical_gate/touchstone_transformer_metrics.csv": (
            "freq_hz",
            "freq_ghz",
            "lp_nh",
            "ls_nh",
            "m_nh",
            "k",
            "qp",
            "qs",
        ),
        "emx_first_validation_gate_20260613/emx_first_validation_gate_metrics.csv": (
            "freq_hz",
            "freq_ghz",
            "lp_nh",
            "ls_nh",
            "k",
            "qp",
            "qs",
            "cm_single_primary_ff",
        ),
        "emx_first_validation_gate_20260613/emx_first_validation_gate_port_pair_sensitivity.csv": (
            "pass_count",
            "metric_count",
            "max_percent_error",
            "mean_percent_error",
        ),
    }
    png_requirements = (
        "touchstone_physical_gate/touchstone_ads_equivalent_metrics.png",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_ads_style_metrics.png",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_core_metrics.png",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_port_pair_sensitivity.png",
    )
    failures: list[str] = []
    for rel_path, required_columns in csv_requirements.items():
        failures.extend(_csv_artifact_failures(root / rel_path, rel_path, required_columns))
        failures.extend(_csv_numeric_column_failures(root / rel_path, rel_path, csv_numeric_columns[rel_path]))
    failures.extend(_metrics_csv_frequency_grid_failures(root, args))
    for rel_path in png_requirements:
        failures.extend(_png_artifact_failures(root / rel_path, rel_path))
    if failures:
        return [Check("FAIL", "post-run validation artifact content", "; ".join(failures[:8]))]
    return [
        Check(
            "PASS",
            "post-run validation artifact content",
            f"{len(csv_requirements)} CSV files have required columns/rows, finite numeric metric columns, and metrics CSVs have 5-50 GHz / 0.1 GHz / 451-point grids; {len(png_requirements)} PNG files have valid dimensions and are nontrivial plot images",
        )
    ]


def _csv_artifact_failures(path: Path, rel_path: str, required_columns: tuple[str, ...]) -> list[str]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            rows = list(reader)
    except Exception as exc:  # noqa: BLE001 - report exact file issue.
        return [f"{rel_path}: unreadable CSV ({type(exc).__name__}: {exc})"]
    missing = [column for column in required_columns if column not in fieldnames]
    failures: list[str] = []
    if missing:
        failures.append(f"{rel_path}: missing columns={missing}")
    if not rows:
        failures.append(f"{rel_path}: no data rows")
    return failures


def _csv_numeric_column_failures(path: Path, rel_path: str, numeric_columns: tuple[str, ...]) -> list[str]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            rows = list(reader)
    except Exception as exc:  # noqa: BLE001 - report exact file issue.
        return [f"{rel_path}: unreadable CSV numeric fields ({type(exc).__name__}: {exc})"]
    missing = [column for column in numeric_columns if column not in fieldnames]
    if missing or not rows:
        return []
    failures: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        for column in numeric_columns:
            raw = row.get(column)
            if raw in (None, ""):
                failures.append(f"{rel_path}: row {row_index} missing numeric value for {column}")
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                failures.append(f"{rel_path}: row {row_index} nonnumeric value for {column}: {raw!r}")
                continue
            if not math.isfinite(value):
                failures.append(f"{rel_path}: row {row_index} non-finite numeric value for {column}: {raw!r}")
        if len(failures) >= 8:
            break
    return failures


def _metrics_csv_frequency_grid_failures(root: Path, args: argparse.Namespace) -> list[str]:
    rel_paths = (
        "touchstone_physical_gate/touchstone_transformer_metrics.csv",
        "emx_first_validation_gate_20260613/emx_first_validation_gate_metrics.csv",
    )
    failures: list[str] = []
    for rel_path in rel_paths:
        failures.extend(
            _csv_frequency_grid_failures(
                root / rel_path,
                rel_path,
                expected_start_hz=float(args.expected_frequency_start_ghz) * 1.0e9,
                expected_stop_hz=float(args.expected_frequency_stop_ghz) * 1.0e9,
                expected_step_hz=float(args.expected_frequency_step_ghz) * 1.0e9,
                expected_points=int(args.expected_frequency_points),
                tolerance_hz=float(args.frequency_tolerance_hz),
            )
        )
    return failures


def _csv_frequency_grid_failures(
    path: Path,
    rel_path: str,
    *,
    expected_start_hz: float,
    expected_stop_hz: float,
    expected_step_hz: float,
    expected_points: int,
    tolerance_hz: float,
) -> list[str]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:  # noqa: BLE001 - report exact file issue.
        return [f"{rel_path}: unreadable CSV frequency grid ({type(exc).__name__}: {exc})"]
    if not rows:
        return [f"{rel_path}: no data rows for frequency grid"]
    freqs: list[float] = []
    for index, row in enumerate(rows):
        try:
            if row.get("freq_hz") not in (None, ""):
                freqs.append(float(row["freq_hz"]))
            elif row.get("freq_ghz") not in (None, ""):
                freqs.append(float(row["freq_ghz"]) * 1.0e9)
            else:
                return [f"{rel_path}: row {index + 1} missing freq_hz/freq_ghz"]
        except (TypeError, ValueError):
            return [f"{rel_path}: row {index + 1} has nonnumeric frequency"]
    failures: list[str] = []
    if len(freqs) != expected_points:
        failures.append(f"{rel_path}: frequency points expected {expected_points}, got {len(freqs)}")
    if abs(freqs[0] - expected_start_hz) > tolerance_hz:
        failures.append(f"{rel_path}: start_hz expected {expected_start_hz:g}, got {freqs[0]:g}")
    if abs(freqs[-1] - expected_stop_hz) > tolerance_hz:
        failures.append(f"{rel_path}: stop_hz expected {expected_stop_hz:g}, got {freqs[-1]:g}")
    steps = [b - a for a, b in zip(freqs, freqs[1:])]
    if any(step <= 0.0 for step in steps):
        failures.append(f"{rel_path}: frequency values are not strictly increasing")
    bad_steps = [step for step in steps if abs(step - expected_step_hz) > tolerance_hz]
    if bad_steps:
        failures.append(f"{rel_path}: frequency step expected {expected_step_hz:g}, bad_step_count={len(bad_steps)}")
    return failures


def _png_artifact_failures(path: Path, rel_path: str) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"{rel_path}: unreadable PNG ({type(exc).__name__}: {exc})"]
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return [f"{rel_path}: missing PNG signature"]
    if len(data) < 33:
        return [f"{rel_path}: missing PNG IHDR chunk"]
    ihdr_length = int.from_bytes(data[8:12], "big")
    ihdr_type = data[12:16]
    if ihdr_length != 13 or ihdr_type != b"IHDR":
        return [f"{rel_path}: invalid PNG IHDR chunk"]
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return [f"{rel_path}: invalid PNG dimensions {width}x{height}"]
    failures: list[str] = []
    if width < MIN_VALIDATION_PNG_WIDTH or height < MIN_VALIDATION_PNG_HEIGHT:
        failures.append(
            f"{rel_path}: PNG dimensions {width}x{height} below minimum {MIN_VALIDATION_PNG_WIDTH}x{MIN_VALIDATION_PNG_HEIGHT}"
        )
    if len(data) < MIN_VALIDATION_PNG_BYTES:
        failures.append(f"{rel_path}: PNG bytes {len(data)} below minimum {MIN_VALIDATION_PNG_BYTES}")
    if failures:
        return failures
    return _png_image_content_failures(path, rel_path)


def _png_image_content_failures(path: Path, rel_path: str) -> list[str]:
    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # noqa: BLE001
        return [f"{rel_path}: Pillow unavailable for nonblank PNG check ({type(exc).__name__}: {exc})"]
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            extrema = ImageStat.Stat(rgb).extrema
    except Exception as exc:  # noqa: BLE001
        return [f"{rel_path}: unreadable PNG image data ({type(exc).__name__}: {exc})"]
    max_delta = max((high - low) for low, high in extrema)
    if max_delta <= MIN_VALIDATION_PNG_COLOR_DELTA:
        return [f"{rel_path}: blank or nearly constant PNG (max_channel_delta={max_delta})"]
    return []


def _touchstone_gate_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    checks.append(_status_check("Touchstone physical gate status", summary.get("overall_status"), "PASS"))
    if summary.get("port_count") == 4:
        checks.append(Check("PASS", "Touchstone physical gate port count", "ports=4"))
    else:
        checks.append(Check("FAIL", "Touchstone physical gate port count", f"port_count={summary.get('port_count')!r}"))
    freq = summary.get("frequency", {})
    expected = {
        "start_hz": float(args.expected_frequency_start_ghz) * 1.0e9,
        "stop_hz": float(args.expected_frequency_stop_ghz) * 1.0e9,
        "step_hz": float(args.expected_frequency_step_ghz) * 1.0e9,
        "points": int(args.expected_frequency_points),
    }
    failures: list[str] = []
    for key, expected_value in expected.items():
        actual = freq.get(key)
        if key == "points":
            if int(actual or -1) != int(expected_value):
                failures.append(f"{key}: expected {int(expected_value)}, got {actual!r}")
        else:
            try:
                error = abs(float(actual) - float(expected_value))
            except (TypeError, ValueError):
                failures.append(f"{key}: expected {expected_value:g}, got {actual!r}")
                continue
            if error > float(args.frequency_tolerance_hz):
                failures.append(f"{key}: expected {expected_value:g}, got {float(actual):g}, error={error:g}")
    if failures:
        checks.append(Check("FAIL", "Touchstone physical gate frequency grid", "; ".join(failures)))
    else:
        checks.append(Check("PASS", "Touchstone physical gate frequency grid", "5-50 GHz, 0.1 GHz, 451 points"))
    checks.extend(_touchstone_argument_checks(summary, args))
    checks.extend(_required_touchstone_check_statuses(summary))
    failed_checks = [item for item in summary.get("checks", []) if item.get("status") == "FAIL"]
    if failed_checks:
        checks.append(Check("FAIL", "Touchstone physical gate internal checks", f"failed={len(failed_checks)}"))
    else:
        checks.append(Check("PASS", "Touchstone physical gate internal checks", f"{len(summary.get('checks', []))} checks without FAIL"))
    return checks


def _touchstone_argument_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    arguments = summary.get("arguments", {})
    required = {
        "min_target_abs_k": float(args.expected_min_target_abs_k),
        "min_window_abs_k": float(args.expected_min_window_abs_k),
    }
    failures: list[str] = []
    for key, expected in required.items():
        actual = arguments.get(key)
        try:
            actual_float = float(actual)
        except (TypeError, ValueError):
            failures.append(f"{key}: expected {expected:g}, got {actual!r}")
            continue
        if abs(actual_float - expected) > 1.0e-12:
            failures.append(f"{key}: expected {expected:g}, got {actual_float:g}")
    if failures:
        return [Check("FAIL", "Touchstone physical gate coupling arguments", "; ".join(failures))]
    return [
        Check(
            "PASS",
            "Touchstone physical gate coupling arguments",
            f"min_target_abs_k={required['min_target_abs_k']:g}, min_window_abs_k={required['min_window_abs_k']:g}",
        )
    ]


def _required_touchstone_check_statuses(summary: dict[str, Any]) -> list[Check]:
    by_name = {item.get("name"): item for item in summary.get("checks", [])}
    required_names = (
        "source identity",
        "differential Z finiteness",
        "differential Z reciprocity",
        "differential Z positive-realness",
        "ADS-equivalent metric finiteness",
        "target-frequency transformer metrics",
        "positive metric window",
        "smooth transformer metric window",
    )
    failures: list[str] = []
    for name in required_names:
        status = by_name.get(name, {}).get("status")
        if status != "PASS":
            failures.append(f"{name}={status!r}")
    if failures:
        return [Check("FAIL", "Touchstone physical gate required physics checks", "; ".join(failures))]
    return [Check("PASS", "Touchstone physical gate required physics checks", f"{len(required_names)} required physics checks PASS")]


def _emx_first_gate_checks(summary: dict[str, Any], args: argparse.Namespace, validation_root: Path) -> list[Check]:
    checks: list[Check] = []
    checks.append(_status_check("EMX-first gate status", summary.get("overall_status"), "PASS"))
    checks.append(_status_check("EMX-first gate decision", summary.get("decision"), "ACCEPT_AS_GOLDEN_EMX_REFERENCE"))
    freq = summary.get("frequency_ghz", {})
    expected = {
        "start": float(args.expected_frequency_start_ghz),
        "stop": float(args.expected_frequency_stop_ghz),
        "step": float(args.expected_frequency_step_ghz),
        "points": int(args.expected_frequency_points),
    }
    failures: list[str] = []
    for key, expected_value in expected.items():
        actual = freq.get(key)
        if key == "points":
            if int(actual or -1) != int(expected_value):
                failures.append(f"{key}: expected {int(expected_value)}, got {actual!r}")
        else:
            try:
                error_hz = abs(float(actual) - float(expected_value)) * 1.0e9
            except (TypeError, ValueError):
                failures.append(f"{key}: expected {expected_value:g}, got {actual!r}")
                continue
            if error_hz > float(args.frequency_tolerance_hz):
                failures.append(f"{key}: expected {expected_value:g}, got {float(actual):g}, error_hz={error_hz:g}")
    if failures:
        checks.append(Check("FAIL", "EMX-first gate frequency grid", "; ".join(failures)))
    else:
        checks.append(Check("PASS", "EMX-first gate frequency grid", "5-50 GHz, 0.1 GHz, 451 points"))
    checks.extend(_emx_first_curve_gate_argument_checks(summary, args))
    checks.extend(_required_emx_first_check_statuses(summary))
    checks.extend(_port_pair_sensitivity_csv_gate_checks(summary, args, validation_root))
    failed_checks = [item for item in summary.get("checks", []) if item.get("status") == "FAIL"]
    if failed_checks:
        checks.append(Check("FAIL", "EMX-first gate internal checks", f"failed={len(failed_checks)}"))
    else:
        checks.append(Check("PASS", "EMX-first gate internal checks", f"{len(summary.get('checks', []))} checks without FAIL"))
    return checks


def _emx_first_curve_gate_argument_checks(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    gate = summary.get("physical_curve_gate")
    if not isinstance(gate, dict):
        return [Check("FAIL", "EMX-first gate curve-window arguments", "missing physical_curve_gate")]
    expected = {
        "physical_window_start_ghz": float(args.expected_physical_window_start_ghz),
        "physical_window_stop_ghz": float(args.expected_physical_window_stop_ghz),
        "shape_window_start_ghz": float(args.expected_shape_window_start_ghz),
        "shape_window_stop_ghz": float(args.expected_shape_window_stop_ghz),
        "min_target_abs_k": float(args.expected_min_target_abs_k),
        "min_window_abs_k": float(args.expected_min_window_abs_k),
        "max_target_abs_k": 0.98,
        "max_shape_spike_ratio": float(args.expected_max_shape_spike_ratio),
        "max_shape_relative_step": float(args.expected_max_shape_relative_step),
    }
    failures: list[str] = []
    for key, expected_value in expected.items():
        actual = gate.get(key)
        try:
            actual_float = float(actual)
        except (TypeError, ValueError):
            failures.append(f"{key}: expected {expected_value:g}, got {actual!r}")
            continue
        if not math.isfinite(actual_float) or abs(actual_float - expected_value) > 1.0e-9:
            failures.append(f"{key}: expected {expected_value:g}, got {actual_float:g}")
    if failures:
        return [Check("FAIL", "EMX-first gate curve-window arguments", "; ".join(failures))]
    return [
        Check(
            "PASS",
            "EMX-first gate curve-window arguments",
            "physical/smooth windows and K/shape thresholds recorded as expected",
        )
    ]


def _required_emx_first_check_statuses(summary: dict[str, Any]) -> list[Check]:
    by_name = {item.get("name"): item for item in summary.get("checks", [])}
    required_names = (
        "source identity",
        "source provenance header",
        "S-matrix shape",
        "frequency row count",
        "finite numeric values",
        "frequency monotonicity",
        "reciprocity",
        "passivity",
        "differential Z finiteness",
        "differential Z reciprocity",
        "differential Z positive-realness",
        "final ADS sweep coverage",
        "ADS no-extrapolation plot grid",
        "target frequency availability",
        "ADS photo anchor",
        "basic numeric physics sanity",
        "physical metric window",
        "smooth transformer metric window",
        "approved port-pair photo alignment",
        "any port-pair photo alignment",
    )
    failures: list[str] = []
    for name in required_names:
        status = by_name.get(name, {}).get("status")
        if status != "PASS":
            failures.append(f"{name}={status!r}")
    if failures:
        return [Check("FAIL", "EMX-first gate required physics/photo/port checks", "; ".join(failures))]
    return [
        Check(
            "PASS",
            "EMX-first gate required physics/photo/port checks",
            f"{len(required_names)} required EMX-first checks PASS",
        )
    ]


def _port_pair_sensitivity_csv_gate_checks(summary: dict[str, Any], args: argparse.Namespace, validation_root: Path) -> list[Check]:
    pair_summary = summary.get("port_pair_sensitivity")
    if not isinstance(pair_summary, dict):
        return [Check("FAIL", "EMX-first port-pair sensitivity CSV gate", "missing port_pair_sensitivity summary")]
    csv_path = _resolve_port_pair_csv_path(summary, validation_root)
    if not csv_path.is_file():
        return [Check("FAIL", "EMX-first port-pair sensitivity CSV gate", f"missing port_pair_csv file: {csv_path}")]
    failures = _port_pair_sensitivity_csv_failures(
        csv_path,
        approved_port_pairs=str(args.expected_approved_port_pairs),
        expected_pair_count=int(args.expected_port_pair_count),
        expected_metric_count=int(args.expected_port_pair_metric_count),
        max_percent_error=float(args.expected_photo_max_percent_error),
    )
    if failures:
        return [Check("FAIL", "EMX-first port-pair sensitivity CSV gate", "; ".join(failures[:8]))]
    return [
        Check(
            "PASS",
            "EMX-first port-pair sensitivity CSV gate",
            (
                f"{int(args.expected_port_pair_count)} ordered four-port pairings checked; "
                f"approved pair {args.expected_approved_port_pairs} PASS and <= {float(args.expected_photo_max_percent_error):g}%"
            ),
        )
    ]


def _resolve_port_pair_csv_path(summary: dict[str, Any], validation_root: Path) -> Path:
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    candidate_text = artifacts.get("port_pair_csv") or (summary.get("port_pair_sensitivity") or {}).get("port_pair_csv")
    if candidate_text:
        candidate = Path(str(candidate_text))
        if candidate.is_file():
            return candidate
        if not candidate.is_absolute():
            relative_candidate = validation_root / candidate
            if relative_candidate.is_file():
                return relative_candidate
    return validation_root / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_port_pair_sensitivity.csv"


def _port_pair_sensitivity_csv_failures(
    csv_path: Path,
    *,
    approved_port_pairs: str,
    expected_pair_count: int,
    expected_metric_count: int,
    max_percent_error: float,
) -> list[str]:
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:  # noqa: BLE001
        return [f"unreadable port-pair sensitivity CSV ({type(exc).__name__}: {exc})"]
    failures: list[str] = []
    if len(rows) != expected_pair_count:
        failures.append(f"port-pair row count expected {expected_pair_count}, got {len(rows)}")
    seen = [str(row.get("port_pairs", "")) for row in rows]
    if len(set(seen)) != len(seen):
        failures.append("duplicate port_pairs entries in sensitivity CSV")
    approved = next((row for row in rows if row.get("port_pairs") == approved_port_pairs), None)
    if approved is None:
        failures.append(f"approved port pair {approved_port_pairs!r} missing")
        return failures
    if str(approved.get("overall_status")) != "PASS":
        failures.append(f"approved port pair {approved_port_pairs} status={approved.get('overall_status')!r}")
    try:
        approved_max_error = float(approved.get("max_percent_error", "nan"))
    except (TypeError, ValueError):
        failures.append(f"approved port pair {approved_port_pairs} max_percent_error is nonnumeric")
    else:
        if not math.isfinite(approved_max_error):
            failures.append(f"approved port pair {approved_port_pairs} max_percent_error is non-finite")
        elif approved_max_error > max_percent_error:
            failures.append(
                f"approved port pair {approved_port_pairs} max_percent_error={approved_max_error:.6g}% > {max_percent_error:g}%"
            )
    try:
        pass_count = int(float(approved.get("pass_count", "nan")))
        metric_count = int(float(approved.get("metric_count", "nan")))
    except (TypeError, ValueError):
        failures.append(f"approved port pair {approved_port_pairs} pass_count/metric_count is nonnumeric")
    else:
        if metric_count != expected_metric_count:
            failures.append(f"approved port pair {approved_port_pairs} metric_count expected {expected_metric_count}, got {metric_count}")
        if pass_count != metric_count:
            failures.append(f"approved port pair {approved_port_pairs} pass_count={pass_count}, metric_count={metric_count}")
    any_pass = any(str(row.get("overall_status")) == "PASS" for row in rows)
    if not any_pass:
        failures.append("no port-pair row has overall_status=PASS")
    return failures


def _local_emx_sha_checks(emx_s4p: Path | None, mars_sha: str | None, *, require_emx_s4p: bool) -> list[Check]:
    if not mars_sha:
        return [Check("FAIL", "MARS-recorded EMX S4P SHA", "missing emx_wideband.s4p.sha256 content")]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", mars_sha):
        return [Check("FAIL", "MARS-recorded EMX S4P SHA", f"invalid SHA256 digest: {mars_sha!r}")]
    checks = [Check("PASS", "MARS-recorded EMX S4P SHA", mars_sha)]
    if emx_s4p is None:
        status = "FAIL" if require_emx_s4p else "WARN"
        checks.append(Check(status, "local EMX S4P SHA", "local --emx-s4p not supplied"))
        return checks
    if not emx_s4p.is_file():
        checks.append(Check("FAIL", "local EMX S4P SHA", f"missing: {emx_s4p}"))
        return checks
    actual = _sha256(emx_s4p)
    status = "PASS" if actual == mars_sha else "FAIL"
    checks.append(Check(status, "local EMX S4P SHA", f"expected={mars_sha}, actual={actual}"))
    return checks


def _status_check(name: str, actual: Any, expected: str) -> Check:
    return Check("PASS" if actual == expected else "FAIL", name, f"expected={expected}, actual={actual!r}")


def _decision(overall_status: str, warnings: list[Check], emx_s4p: Path | None, mars_sha: str | None) -> str:
    if overall_status != "PASS":
        return "DO_NOT_IMPORT_TARGET_EMX_REFERENCE"
    if warnings or emx_s4p is None or not mars_sha:
        return "VALIDATION_EVIDENCE_TRANSFERRED_NO_LOCAL_EMX"
    return "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_first_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").split()
    return text[0] if text else None


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _file_record(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "bytes": path.stat().st_size if exists else None,
        "sha256": _sha256(path) if exists and path.is_file() else None,
    }


def _artifact_record(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    exists = path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "bytes": path.stat().st_size if exists else None,
        "sha256": _sha256(path) if exists else None,
    }


def _accepted_emx_reference_bundle(
    validation_root: Path | None,
    emx_s4p: Path | None,
    mars_emx_sha: str | None,
    decision: str,
) -> dict[str, Any]:
    status = "READY_FOR_HFSS" if decision == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS" else "NOT_READY"
    bundle: dict[str, Any] = {
        "status": status,
        "decision_required": "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
        "downstream_required_decision": "ACCEPT_HFSS_VALIDATION_SAMPLE",
        "guardrail": "Do not use this EMX reference for HFSS comparison unless status is READY_FOR_HFSS.",
        "mars_emx_sha256": mars_emx_sha,
        "emx_s4p": _artifact_record(emx_s4p),
        "validation_root": str(validation_root) if validation_root else None,
        "artifacts": {},
    }
    if validation_root:
        artifacts = {
            "touchstone_summary": validation_root / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json",
            "touchstone_report": validation_root / "touchstone_physical_gate" / "touchstone_transformer_audit_report.md",
            "touchstone_metrics_csv": validation_root / "touchstone_physical_gate" / "touchstone_transformer_metrics.csv",
            "touchstone_ads_equivalent_plot": validation_root / "touchstone_physical_gate" / "touchstone_ads_equivalent_metrics.png",
            "emx_first_summary": validation_root / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json",
            "emx_first_report": validation_root / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_report.md",
            "emx_first_metrics_csv": validation_root / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_metrics.csv",
            "emx_first_ads_style_plot": validation_root
            / "emx_first_validation_gate_20260613"
            / "emx_first_validation_gate_ads_style_metrics.png",
            "emx_first_core_plot": validation_root
            / "emx_first_validation_gate_20260613"
            / "emx_first_validation_gate_core_metrics.png",
            "port_pair_sensitivity_csv": validation_root
            / "emx_first_validation_gate_20260613"
            / "emx_first_validation_gate_port_pair_sensitivity.csv",
            "port_pair_sensitivity_plot": validation_root
            / "emx_first_validation_gate_20260613"
            / "emx_first_validation_gate_port_pair_sensitivity.png",
        }
        bundle["artifacts"] = {name: _artifact_record(path) for name, path in artifacts.items()}
    return bundle


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Target EMX Post-Run Import Verification",
        "",
        f"- Overall status: **{summary.get('overall_status')}**",
        f"- Decision: `{summary.get('decision')}`",
        f"- Tarball: `{summary.get('tarball', {}).get('path')}`",
        f"- Local EMX S4P: `{(summary.get('emx_s4p') or {}).get('path')}`",
        f"- MARS EMX SHA256: `{summary.get('mars_emx_sha256')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary.get("checks", []):
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    bundle = summary.get("accepted_emx_reference_bundle") or {}
    lines.extend(["", "## Accepted EMX Reference Bundle", ""])
    lines.append(f"- Status: `{bundle.get('status')}`")
    lines.append(f"- EMX S4P: `{((bundle.get('emx_s4p') or {}).get('path'))}`")
    lines.append(f"- Validation root: `{bundle.get('validation_root')}`")
    for name, record in (bundle.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{record.get('path')}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This verifier does not run EMX, HFSS, or ADS.",
            "- Use `ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS` only when the validation package gates pass and the local `.s4p` SHA matches the MARS-recorded SHA.",
            "- If the decision is `VALIDATION_EVIDENCE_TRANSFERRED_NO_LOCAL_EMX`, download the EMX `.s4p` itself before ADS/HFSS work.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
