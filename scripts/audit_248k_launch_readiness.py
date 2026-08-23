#!/usr/bin/env python3
"""Audit whether the 248k production dataset is allowed to launch.

This is a conservative pre-launch gate. It does not run EMX/Cadence/HFSS/ADS
and it does not generate any dataset. It prevents starting the expensive 248k
production run until the wideband 500 pilot and sampled HFSS/EMX validation
evidence are present and passing.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.core import load_run_config  # noqa: E402


REQUIRED_QUALITY_STEPS = (
    "dataset validation",
    "dataset visualization",
    "sampling distribution audit",
    "geometry quality audit",
    "dataset Touchstone preflight",
    "response feature extraction",
    "response feature coverage audit",
    "Zin coverage audit",
    "HFSS validation sample selection",
)


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
    production_config = Path(args.production_config).expanduser().resolve()
    checks.extend(_production_config_checks(production_config, args))
    checks.append(
        _zin_target_envelope_config_check(
            Path(args.zin_target_envelope_config).expanduser().resolve()
        )
    )
    checks.append(
        _response_target_envelope_config_check(
            Path(args.response_target_envelope_config).expanduser().resolve()
        )
    )
    checks.append(_summary_status_check(Path(args.production_preflight_summary).expanduser().resolve(), "248k strict path preflight"))
    checks.append(_wideband_quality_summary_check(Path(args.wideband_quality_summary).expanduser().resolve()))
    checks.append(_hfss_batch_summary_check(Path(args.hfss_batch_summary).expanduser().resolve(), args))

    overall_status = "PASS" if checks and all(check.status == "PASS" for check in checks) else "NOT_READY"
    launch_commands = out_dir / "248k_launch_commands.sh"
    launch_commands.write_text(_render_launch_commands(production_config, args), encoding="utf-8")
    launch_commands.chmod(0o755)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "production_config": str(production_config),
        "out_dir": str(out_dir),
        "launch_commands": str(launch_commands),
        "checks": [check.as_dict() for check in checks],
        "arguments": {
            "production_count": int(args.production_count),
            "production_batch_size": int(args.production_batch_size),
            "production_run_dir": args.production_run_dir,
            "sampler": args.sampler,
            "seed": int(args.seed),
            "expected_wideband_count": int(args.expected_wideband_count),
            "min_hfss_samples": int(args.min_hfss_samples),
            "expected_frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "expected_frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "zin_target_envelope_config": str(Path(args.zin_target_envelope_config).expanduser().resolve()),
            "response_target_envelope_config": str(Path(args.response_target_envelope_config).expanduser().resolve()),
        },
        "limitations": [
            "This gate checks prerequisite evidence only; it does not run the 248k job.",
            "A PASS means it is reasonable to launch 248k with the generated command, not that 248k data exists.",
            "Zin and response target-envelope configs must be project-filled, non-template files before launch.",
            "A NOT_READY result must not be reported as completed production generation.",
        ],
    }
    summary_path = out_dir / "248k_launch_readiness_summary.json"
    report_path = out_dir / "248k_launch_readiness_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"launch_commands={launch_commands}")
    for check in checks:
        print(f"{check.status:9s} {check.name}: {check.detail}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_project = Path(__file__).resolve().parents[1]
    default_wideband_run = Path("runs/dataset500_wideband_grounded_20260613")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production-config",
        default=str(default_project / "configs" / "mars_dataset_248k_template.yaml"),
    )
    parser.add_argument("--production-preflight-summary", required=True)
    parser.add_argument(
        "--wideband-quality-summary",
        default=str(default_wideband_run / "dataset_quality_gates_20260613" / "dataset_quality_gates_summary.json"),
    )
    parser.add_argument(
        "--hfss-batch-summary",
        default=str(default_wideband_run / "hfss_emx_validation_batch_20260613" / "hfss_emx_validation_batch_summary.json"),
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--production-run-dir", default="runs/dataset248k_wideband_grounded_20260613")
    parser.add_argument("--production-count", type=int, default=248000)
    parser.add_argument("--production-batch-size", type=int, default=100)
    parser.add_argument("--sampler", default="lhs_optimized")
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--z-load-ohm", type=float, default=50.0)
    parser.add_argument("--expected-wideband-count", type=int, default=500)
    parser.add_argument("--min-hfss-samples", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--expected-frequency-points", type=int, default=451)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument(
        "--zin-target-envelope-config",
        default=str(default_project / "configs" / "zin_target_envelope_template_20260614.json"),
        help="Project-filled, non-template Zin Re/Im target-envelope JSON required before 248k launch.",
    )
    parser.add_argument(
        "--response-target-envelope-config",
        default=str(default_project / "configs" / "response_target_envelopes_template_20260614.json"),
        help="Project-filled, non-template K/Qp and Lp/Ls response-envelope JSON required before 248k launch.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _production_config_checks(config_path: Path, args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    try:
        config = load_run_config(config_path)
    except Exception as exc:  # noqa: BLE001 - exact loader error is useful evidence.
        return [Check("FAIL", "248k config loads", f"{type(exc).__name__}: {exc}")]

    checks.append(Check("PASS", "248k config loads", str(config_path)))
    points = config.target.frequency_points_hz()
    grid_mismatch = _frequency_mismatch(
        points,
        start_hz=float(args.expected_frequency_start_ghz) * 1.0e9,
        stop_hz=float(args.expected_frequency_stop_ghz) * 1.0e9,
        step_hz=float(args.expected_frequency_step_ghz) * 1.0e9,
        points=int(args.expected_frequency_points),
        tolerance_hz=float(args.frequency_tolerance_hz),
    )
    checks.append(
        Check(
            "FAIL" if grid_mismatch else "PASS",
            "248k frequency grid",
            grid_mismatch
            or (
                f"{points[0] / 1.0e9:g}-{points[-1] / 1.0e9:g} GHz, "
                f"step={(points[1] - points[0]) / 1.0e9:g} GHz, points={len(points)}"
            ),
        )
    )
    checks.append(
        Check(
            "PASS" if str(config.emx.port_mode) == "single_ended_shield_grounded" else "FAIL",
            "248k port mode",
            str(config.emx.port_mode),
        )
    )
    checks.append(
        Check(
            "PASS" if int(config.emx.cadence_pin_purpose) == 51 else "FAIL",
            "248k cadence pin purpose",
            str(config.emx.cadence_pin_purpose),
        )
    )
    shield = config.bounds.shield
    shield_ok = bool(shield.enabled and shield.kind == "ring" and shield.margin_um is not None and shield.width_um is not None)
    checks.append(
        Check(
            "PASS" if shield_ok else "FAIL",
            "248k shield",
            f"enabled={shield.enabled}, kind={shield.kind}, margin_um={shield.margin_um}, width_um={shield.width_um}",
        )
    )
    checks.append(_production_path_check(config))
    return checks


def _frequency_mismatch(
    actual_points: list[float],
    *,
    start_hz: float,
    stop_hz: float,
    step_hz: float,
    points: int,
    tolerance_hz: float,
) -> str:
    if len(actual_points) < 2:
        return "frequency grid has fewer than 2 points"
    comparisons = (
        ("start_hz", float(actual_points[0]), start_hz),
        ("stop_hz", float(actual_points[-1]), stop_hz),
        ("step_hz", float(actual_points[1] - actual_points[0]), step_hz),
    )
    for name, actual, expected in comparisons:
        if abs(actual - expected) > tolerance_hz:
            return f"{name} mismatch: actual={actual}, expected={expected}"
    if len(actual_points) != points:
        return f"points mismatch: actual={len(actual_points)}, expected={points}"
    return ""


def _production_path_check(config: Any) -> Check:
    fields = {
        "emx_binary": config.emx.emx_binary,
        "emx_process_file": config.emx.emx_process_file,
        "cadence_install_root": config.emx.cadence_install_root,
        "cadence_pdk_cds_lib": config.emx.cadence_pdk_cds_lib,
        "cadence_layer_map": config.emx.cadence_layer_map,
    }
    bad: list[tuple[str, str]] = []
    for name, raw_value in fields.items():
        value = "" if raw_value is None else str(raw_value).strip()
        if _looks_like_placeholder(value):
            bad.append((name, "placeholder"))
        elif not Path(value).expanduser().exists():
            bad.append((name, "missing"))
    if bad:
        return Check("NOT_READY", "248k EMX/Cadence paths", f"bad_entries={bad}")
    return Check("PASS", "248k EMX/Cadence paths", "required paths exist and are not placeholders")


def _looks_like_placeholder(value: str) -> bool:
    if not value:
        return True
    upper = value.upper()
    return "REPLACE" in upper or value.startswith("/REPLACE/")


def _zin_target_envelope_config_check(path: Path) -> Check:
    data = _read_json(path)
    if data is None:
        return Check("NOT_READY", "Zin target-envelope config", f"missing or invalid JSON: {path}")
    failures = _template_config_failures(data, path)
    envelope = data.get("zin_target_envelope")
    if not isinstance(envelope, dict):
        failures.append("zin_target_envelope missing")
    else:
        failures.extend(
            _bounds_failures(
                envelope,
                (
                    ("real_min_ohm", "real_max_ohm"),
                    ("imag_min_ohm", "imag_max_ohm"),
                ),
            )
        )
        failures.extend(
            _threshold_failures(
                envelope,
                area_key="min_area_fraction",
                bins_key="min_occupied_2d_bins",
                outside_key="max_outside_fraction",
            )
        )
        failures.extend(_target_count_failures(envelope))
    if failures:
        return Check("NOT_READY", "Zin target-envelope config", f"{failures[:8]}; path={path}")
    return Check("PASS", "Zin target-envelope config", f"filled non-template Re/Im Zin envelope: {path}")


def _response_target_envelope_config_check(path: Path) -> Check:
    data = _read_json(path)
    if data is None:
        return Check("NOT_READY", "response target-envelope config", f"missing or invalid JSON: {path}")
    failures = _template_config_failures(data, path)
    root = data.get("response_target_envelopes")
    if not isinstance(root, dict):
        failures.append("response_target_envelopes missing")
    else:
        failures.extend(_target_count_failures(root))
        failures.extend(
            _response_envelope_failures(
                root.get("k_qp"),
                section="k_qp",
                bound_pairs=(("k_min", "k_max"), ("qp_min", "qp_max")),
            )
        )
        failures.extend(
            _response_envelope_failures(
                root.get("lp_ls"),
                section="lp_ls",
                bound_pairs=(("lp_min_nh", "lp_max_nh"), ("ls_min_nh", "ls_max_nh")),
            )
        )
    if failures:
        return Check("NOT_READY", "response target-envelope config", f"{failures[:8]}; path={path}")
    return Check("PASS", "response target-envelope config", f"filled non-template K/Qp and Lp/Ls envelopes: {path}")


def _template_config_failures(data: dict[str, Any], path: Path) -> list[str]:
    status = str(data.get("status", ""))
    if "TEMPLATE_ONLY" in status.upper():
        return [f"template-only config status={status}"]
    if "template" in path.name.lower() and "filled" not in path.name.lower():
        return [f"template-looking filename={path.name}"]
    return []


def _response_envelope_failures(
    section_data: Any,
    *,
    section: str,
    bound_pairs: tuple[tuple[str, str], ...],
) -> list[str]:
    if not isinstance(section_data, dict):
        return [f"{section} missing"]
    failures = [f"{section}.{item}" for item in _bounds_failures(section_data, bound_pairs)]
    failures.extend(
        f"{section}.{item}"
        for item in _threshold_failures(
            section_data,
            area_key="min_area_fraction",
            bins_key="min_occupied_2d_bins",
            outside_key="max_outside_fraction",
        )
    )
    return failures


def _bounds_failures(data: dict[str, Any], pairs: tuple[tuple[str, str], ...]) -> list[str]:
    failures: list[str] = []
    for lower_key, upper_key in pairs:
        lower = data.get(lower_key)
        upper = data.get(upper_key)
        if not _finite_number(lower):
            failures.append(f"{lower_key} not finite")
            continue
        if not _finite_number(upper):
            failures.append(f"{upper_key} not finite")
            continue
        if float(upper) <= float(lower):
            failures.append(f"{upper_key} <= {lower_key}")
    return failures


def _threshold_failures(
    data: dict[str, Any],
    *,
    area_key: str,
    bins_key: str,
    outside_key: str,
) -> list[str]:
    failures: list[str] = []
    area = data.get(area_key)
    bins = data.get(bins_key)
    outside = data.get(outside_key)
    if not _fraction_number(area) or float(area) <= 0.0:
        failures.append(f"{area_key} must be in (0, 1]")
    if not _finite_number(bins) or int(bins) < 1 or not math.isclose(float(bins), float(int(bins))):
        failures.append(f"{bins_key} must be integer >= 1")
    if not _fraction_number(outside):
        failures.append(f"{outside_key} must be in [0, 1]")
    return failures


def _target_count_failures(data: dict[str, Any]) -> list[str]:
    value = data.get("target_count_per_bin")
    if value is None:
        return []
    if not _finite_number(value) or int(value) < 1 or not math.isclose(float(value), float(int(value))):
        return ["target_count_per_bin must be integer >= 1"]
    return []


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _fraction_number(value: Any) -> bool:
    return _finite_number(value) and 0.0 <= float(value) <= 1.0


def _summary_status_check(path: Path, name: str) -> Check:
    data = _read_json(path)
    if data is None:
        return Check("NOT_READY", name, f"missing or invalid JSON: {path}")
    status = str(data.get("overall_status", ""))
    return Check("PASS" if status == "PASS" else "NOT_READY", name, f"overall_status={status or 'missing'}; path={path}")


def _wideband_quality_summary_check(path: Path) -> Check:
    data = _read_json(path)
    if data is None:
        return Check("NOT_READY", "wideband 500 quality gates", f"missing or invalid JSON: {path}")
    status = data.get("overall_status")
    steps = {str(item.get("name")): str(item.get("status")) for item in data.get("steps", []) if isinstance(item, dict)}
    missing = [name for name in REQUIRED_QUALITY_STEPS if name not in steps]
    failed = [name for name in REQUIRED_QUALITY_STEPS if steps.get(name) != "PASS"]
    if status != "PASS":
        return Check("NOT_READY", "wideband 500 quality gates", f"overall_status={status}; path={path}")
    if missing:
        return Check("NOT_READY", "wideband 500 quality gates", f"missing_steps={missing[:8]}; path={path}")
    if failed:
        return Check("NOT_READY", "wideband 500 quality gates", f"non_pass_steps={failed[:8]}; path={path}")
    return Check("PASS", "wideband 500 quality gates", f"{len(REQUIRED_QUALITY_STEPS)} required steps PASS; path={path}")


def _hfss_batch_summary_check(path: Path, args: argparse.Namespace) -> Check:
    data = _read_json(path)
    if data is None:
        return Check("NOT_READY", "sampled HFSS/EMX batch gate", f"missing or invalid JSON: {path}")
    status = data.get("overall_status")
    sample_count = int(data.get("sample_count") or 0)
    counts = data.get("status_counts", {}) if isinstance(data.get("status_counts"), dict) else {}
    pass_count = int(counts.get("PASS") or 0)
    arguments = data.get("arguments", {}) if isinstance(data.get("arguments"), dict) else {}
    strict_mismatch = _batch_grid_mismatches(arguments, args)
    no_extrapolation_failures = _hfss_batch_no_extrapolation_failures(data, path, args)
    if status != "PASS":
        return Check("NOT_READY", "sampled HFSS/EMX batch gate", f"overall_status={status}; path={path}")
    if sample_count < int(args.min_hfss_samples) or pass_count < int(args.min_hfss_samples):
        return Check("NOT_READY", "sampled HFSS/EMX batch gate", f"sample_count={sample_count}, pass_count={pass_count}, required={args.min_hfss_samples}")
    if strict_mismatch:
        return Check("NOT_READY", "sampled HFSS/EMX batch gate", f"strict_arg_mismatch={strict_mismatch}")
    if not bool(arguments.get("require_all_present")) or not bool(arguments.get("require_all_pass")):
        return Check("NOT_READY", "sampled HFSS/EMX batch gate", "require_all_present/require_all_pass were not true")
    if no_extrapolation_failures:
        return Check("NOT_READY", "sampled HFSS/EMX batch gate", f"no_extrapolation_failures={no_extrapolation_failures[:8]}; path={path}")
    return Check("PASS", "sampled HFSS/EMX batch gate", f"samples={sample_count}, pass_count={pass_count}, strict grid/no-extrapolation PASS; path={path}")


def _hfss_batch_no_extrapolation_failures(data: dict[str, Any], batch_summary_path: Path, args: argparse.Namespace) -> list[str]:
    records = data.get("records")
    if not isinstance(records, list) or not records:
        return ["records_missing"]
    pass_count = int((data.get("status_counts") or {}).get("PASS") or 0)
    pass_records = [record for record in records if isinstance(record, dict) and record.get("status") == "PASS"]
    if len(pass_records) < pass_count:
        return [f"pass_records={len(pass_records)}, status_count_PASS={pass_count}"]
    failures: list[str] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            failures.append(f"record_{index}=invalid")
            continue
        if record.get("status") != "PASS":
            failures.append(f"{record.get('evaluation', index)}: status={record.get('status')}")
            continue
        if record.get("no_extrapolation_status") != "PASS":
            failures.append(f"{record.get('evaluation', index)}: no_extrapolation_status={record.get('no_extrapolation_status')}")
            continue
        summary_path = _resolve_batch_record_path(record.get("summary_path"), batch_summary_path.parent)
        if summary_path is None:
            failures.append(f"{record.get('evaluation', index)}: summary_path_missing")
            continue
        summary = _read_json(summary_path)
        if summary is None:
            failures.append(f"{record.get('evaluation', index)}: unreadable_summary={summary_path}")
            continue
        failures.extend(_compare_summary_failures(record, summary, args))
    return failures


def _resolve_batch_record_path(raw_path: Any, base_dir: Path) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path)).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def _compare_summary_failures(record: dict[str, Any], summary: dict[str, Any], args: argparse.Namespace) -> list[str]:
    prefix = str(record.get("evaluation", "sample"))
    failures: list[str] = []
    if summary.get("overall_status") != "PASS":
        failures.append(f"{prefix}: compare_overall_status={summary.get('overall_status')}")
    source_failures = _compare_source_failures(prefix, record, summary)
    failures.extend(source_failures)
    criterion = summary.get("criterion", {}) if isinstance(summary.get("criterion"), dict) else {}
    criterion_max = criterion.get("max_percent_error")
    if not isinstance(criterion_max, (int, float)) or float(criterion_max) > 5.0:
        failures.append(f"{prefix}: criterion_max_percent_error={criterion_max}")
    freq = summary.get("frequency_window_hz", {}) if isinstance(summary.get("frequency_window_hz"), dict) else {}
    expected_start = float(args.expected_frequency_start_ghz) * 1.0e9
    expected_stop = float(args.expected_frequency_stop_ghz) * 1.0e9
    tolerance_hz = float(args.frequency_tolerance_hz)
    if abs(float(freq.get("min", 0.0)) - expected_start) > tolerance_hz:
        failures.append(f"{prefix}: compare_window_start={freq.get('min')}")
    if abs(float(freq.get("max", 0.0)) - expected_stop) > tolerance_hz:
        failures.append(f"{prefix}: compare_window_stop={freq.get('max')}")
    if int(freq.get("count", -1)) != int(args.expected_frequency_points):
        failures.append(f"{prefix}: compare_window_count={freq.get('count')}")
    grid_checks = summary.get("frequency_grid_checks", {}) if isinstance(summary.get("frequency_grid_checks"), dict) else {}
    required_grid_checks = (
        "ADS no-extrapolation coverage",
        "expected frequency points",
        "expected frequency step",
        "matching HFSS/ADS frequency grid",
    )
    for name in required_grid_checks:
        status = (grid_checks.get(name) or {}).get("status") if isinstance(grid_checks.get(name), dict) else None
        if status != "PASS":
            failures.append(f"{prefix}: {name}={status}")
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    for name in ("k", "qp", "qs", "lp_nh", "ls_nh"):
        item = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
        status = item.get("status")
        if status != "PASS":
            failures.append(f"{prefix}: metric_{name}={status}")
        max_percent_error = item.get("max_percent_error")
        if not isinstance(max_percent_error, (int, float)) or float(max_percent_error) > 5.0:
            failures.append(f"{prefix}: metric_{name}_max_percent_error={max_percent_error}")
    return failures


def _compare_source_failures(prefix: str, record: dict[str, Any], summary: dict[str, Any]) -> list[str]:
    checks = (
        ("emx", record.get("emx_path"), summary.get("emx_source")),
        ("hfss", record.get("hfss_path"), summary.get("hfss_ads_source")),
    )
    failures: list[str] = []
    for label, record_path, summary_path in checks:
        if not record_path:
            failures.append(f"{prefix}: record_{label}_path_missing")
            continue
        if not summary_path:
            failures.append(f"{prefix}: summary_{label}_source_missing")
            continue
        if _normalized_path_text(record_path) != _normalized_path_text(summary_path):
            failures.append(
                f"{prefix}: {label}_source_mismatch="
                f"record:{_normalized_path_text(record_path)} summary:{_normalized_path_text(summary_path)}"
            )
    return failures


def _normalized_path_text(raw_path: Any) -> str:
    return str(Path(str(raw_path)).expanduser().resolve())


def _batch_grid_mismatches(arguments: dict[str, Any], args: argparse.Namespace) -> list[str]:
    checks = (
        ("compare_start_ghz", float(args.expected_frequency_start_ghz)),
        ("compare_stop_ghz", float(args.expected_frequency_stop_ghz)),
        ("expected_frequency_step_ghz", float(args.expected_frequency_step_ghz)),
        ("expected_frequency_points", int(args.expected_frequency_points)),
        ("min_frequency_points", int(args.expected_frequency_points)),
        ("max_percent_error", 5.0),
    )
    mismatches: list[str] = []
    for key, expected in checks:
        actual = arguments.get(key)
        if isinstance(expected, int):
            if int(actual or 0) != expected:
                mismatches.append(f"{key}={actual}")
        elif not math.isclose(float(actual or 0.0), float(expected), rel_tol=0.0, abs_tol=1.0e-9):
            mismatches.append(f"{key}={actual}")
    return mismatches


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _render_launch_commands(config_path: Path, args: argparse.Namespace) -> str:
    run_dir = args.production_run_dir
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Run only after audit_248k_launch_readiness.py reports PASS.",
            ".venv/bin/python scripts/preflight_dataset_config.py \\",
            f"  {config_path} \\",
            "  --check-emx-paths \\",
            "  --report runs/dataset248k_wideband_grounded_20260613_config_preflight.md \\",
            "  --summary runs/dataset248k_wideband_grounded_20260613_config_preflight.json",
            "",
            "MPLCONFIGDIR=$PWD/.mplconfig \\",
            ".venv/bin/python -m rfic_transformer_inverse_design.interfaces.cli sample-dataset \\",
            f"  --config {config_path} \\",
            f"  --count {int(args.production_count)} \\",
            f"  --batch-size {int(args.production_batch_size)} \\",
            f"  --sampler {args.sampler} \\",
            f"  --seed {int(args.seed)} \\",
            f"  --z-load-ohm {float(args.z_load_ohm):g} \\",
            f"  --out-dir {run_dir} \\",
            "  --fail-on-error",
            "",
            ".venv/bin/python scripts/audit_mars_run_progress.py \\",
            f"  {run_dir} \\",
            f"  --out-dir {run_dir}/mars_run_progress_audit_20260613 \\",
            f"  --expected-count {int(args.production_count)} \\",
            f"  --expected-frequency-start-ghz {float(args.expected_frequency_start_ghz):g} \\",
            f"  --expected-frequency-stop-ghz {float(args.expected_frequency_stop_ghz):g} \\",
            f"  --expected-frequency-step-ghz {float(args.expected_frequency_step_ghz):g} \\",
            f"  --expected-frequency-points {int(args.expected_frequency_points)} \\",
            "  --max-touchstone-frequency-checks 1000 \\",
            "  --require-clearance-audit \\",
            "  --require-geometry-quality \\",
            "  --internal-angle-deg 135 \\",
            "  --terminal-angle-deg 90 \\",
            "  --require-emx-command \\",
            "  --expected-port-mode single_ended_shield_grounded \\",
            "  --expected-pin-purpose 51",
            "",
            ".venv/bin/python scripts/run_dataset_quality_gates.py \\",
            f"  {run_dir} \\",
            f"  --out-dir {run_dir}/dataset_quality_gates_20260613 \\",
            "  --require-emx \\",
            "  --require-clearance-audit \\",
            "  --audit-sampling-distribution \\",
            "  --extract-response-features \\",
            "  --audit-response-feature-coverage \\",
            "  --audit-zin-coverage \\",
            "  --select-hfss-samples \\",
            f"  --hfss-sample-count {int(args.min_hfss_samples)} \\",
            f"  --response-target-envelope-config {Path(args.response_target_envelope_config).expanduser().resolve()} \\",
            f"  --zin-target-envelope-config {Path(args.zin_target_envelope_config).expanduser().resolve()} \\",
            f"  --expected-frequency-start-ghz {float(args.expected_frequency_start_ghz):g} \\",
            f"  --expected-frequency-stop-ghz {float(args.expected_frequency_stop_ghz):g} \\",
            f"  --expected-frequency-step-ghz {float(args.expected_frequency_step_ghz):g} \\",
            f"  --expected-frequency-points {int(args.expected_frequency_points)} \\",
            "  --max-touchstone-frequency-checks 1000",
            "",
        ]
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# 248k Launch Readiness Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Production config: `{summary['production_config']}`",
        f"- Launch commands: `{summary['launch_commands']}`",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
