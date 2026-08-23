#!/usr/bin/env python3
"""Audit Zin label coverage for RFIC transformer training datasets.

This gate is label-driven. Geometry-only datasets intentionally return
NOT_READY because they cannot prove Zin coverage for inverse training.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_target_envelope_config(args)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "zin_coverage_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = _read_json(dataset_dir / "dataset_manifest.json")
    rows = _read_rows(dataset_dir / "dataset_rows.csv")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    labels = _collect_zin_labels(ok_rows)
    target_envelope = _target_envelope_summary(labels, args)
    checks = _build_checks(manifest, rows, ok_rows, labels, target_envelope, args)
    overall_status = _overall_status(checks, labels)
    occupancy = _bin_occupancy(labels, args)
    target_occupancy = _target_envelope_occupancy(labels, target_envelope, args)
    plots = _write_plots(out_dir, labels, target_envelope, args) if labels["valid_count"] else []
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "rows": {"row_count": len(rows), "ok_count": len(ok_rows)},
        "manifest_zin_coverage": manifest.get("zin_coverage", {}),
        "target_envelope_config": getattr(args, "_target_envelope_config_summary", {"configured": False}),
        "label_summary": _label_summary(labels, args),
        "bin_occupancy": _bin_occupancy_summary(occupancy),
        "target_envelope_summary": target_envelope,
        "target_envelope_bin_occupancy": _bin_occupancy_summary(target_occupancy),
        "checks": checks,
        "plots": plots,
        "limitations": [
            "This audit checks existing Zin labels only; it does not run EMX, HFSS, or ADS.",
            "A NOT_READY result means Zin labels are absent and coverage must not be claimed.",
            "Coverage thresholds should be set from the professor/project target impedance envelope before final acceptance.",
        ],
    }
    summary_path = out_dir / "zin_coverage_audit_summary.json"
    report_path = out_dir / "zin_coverage_audit_report.md"
    rows_path = out_dir / "zin_coverage_points.csv"
    bins_path = out_dir / "zin_coverage_bins.csv"
    target_bins_path = out_dir / "zin_target_envelope_bins.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_points(rows_path, labels)
    _write_bins(bins_path, occupancy)
    _write_bins(target_bins_path, target_occupancy)

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"points_csv={rows_path}")
    print(f"bins_csv={bins_path}")
    print(f"target_envelope_bins_csv={target_bins_path}")
    for check in checks:
        print(f"{check['status']:9s} {check['name']}: {check['detail']}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--require-all-ok", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-valid-count", type=int)
    parser.add_argument("--min-real-span-ohm", type=float)
    parser.add_argument("--min-imag-span-ohm", type=float)
    parser.add_argument("--min-abs-span-ohm", type=float)
    parser.add_argument("--min-real-bins", type=int)
    parser.add_argument("--min-imag-bins", type=int)
    parser.add_argument("--min-occupied-2d-bins", type=int)
    parser.add_argument("--target-count-per-bin", type=int, default=1)
    parser.add_argument("--target-envelope-config", help="JSON file containing reusable Zin target-envelope bounds and thresholds")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--target-real-min-ohm", type=float)
    parser.add_argument("--target-real-max-ohm", type=float)
    parser.add_argument("--target-imag-min-ohm", type=float)
    parser.add_argument("--target-imag-max-ohm", type=float)
    parser.add_argument("--min-target-envelope-area-frac", type=float)
    parser.add_argument("--min-target-envelope-occupied-2d-bins", type=int)
    parser.add_argument("--max-target-envelope-outside-frac", type=float)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _apply_target_envelope_config(args: argparse.Namespace) -> None:
    config_path_raw = getattr(args, "target_envelope_config", None)
    if not config_path_raw:
        args._target_envelope_config_summary = {"configured": False, "status": "NOT_CONFIGURED"}
        return
    path = Path(config_path_raw).expanduser().resolve()
    summary: dict[str, Any] = {"configured": True, "path": str(path)}
    if not path.is_file():
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"missing config file: {path}"}
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report exact config parser issue.
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        return
    if not isinstance(data, dict):
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"top-level JSON is {type(data).__name__}"}
        return
    if "TEMPLATE_ONLY" in str(data.get("status", "")).upper():
        args._target_envelope_config_summary = {
            **summary,
            "status": "FAIL",
            "error": "target envelope config is marked TEMPLATE_ONLY; fill and save a project-specific copy before using it",
        }
        return
    envelope = data.get("zin_target_envelope", data)
    if not isinstance(envelope, dict):
        args._target_envelope_config_summary = {
            **summary,
            "status": "FAIL",
            "error": f"zin_target_envelope is {type(envelope).__name__}",
        }
        return
    field_map = {
        "real_min_ohm": "target_real_min_ohm",
        "real_max_ohm": "target_real_max_ohm",
        "imag_min_ohm": "target_imag_min_ohm",
        "imag_max_ohm": "target_imag_max_ohm",
        "min_area_fraction": "min_target_envelope_area_frac",
        "min_occupied_2d_bins": "min_target_envelope_occupied_2d_bins",
        "max_outside_fraction": "max_target_envelope_outside_frac",
        "target_count_per_bin": "target_count_per_bin",
    }
    applied: dict[str, Any] = {}
    invalid: list[str] = []
    for source_key, arg_name in field_map.items():
        if source_key not in envelope or envelope[source_key] is None:
            continue
        value = envelope[source_key]
        try:
            if arg_name in {"min_target_envelope_occupied_2d_bins", "target_count_per_bin"}:
                coerced: Any = int(value)
            else:
                coerced = float(value)
        except (TypeError, ValueError):
            invalid.append(f"{source_key}={value!r}")
            continue
        if arg_name == "target_count_per_bin":
            if getattr(args, arg_name) == 1:
                setattr(args, arg_name, coerced)
                applied[arg_name] = coerced
            continue
        if getattr(args, arg_name) is None:
            setattr(args, arg_name, coerced)
            applied[arg_name] = coerced
    if invalid:
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"invalid numeric fields: {invalid}"}
        return
    args._target_envelope_config_summary = {
        **summary,
        "status": "PASS",
        "schema": data.get("schema", "direct_or_zin_target_envelope"),
        "name": data.get("name") or envelope.get("name"),
        "applied_fields": applied,
        "notes": data.get("notes", []),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - preserve exact evidence parser issue.
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _collect_zin_labels(rows: list[dict[str, str]]) -> dict[str, Any]:
    points = []
    for index, row in enumerate(rows):
        real = _as_float(_first_value(row, ("zin_center_real_ohm", "zin_real_ohm", "center_zin_real_ohm")))
        imag = _as_float(_first_value(row, ("zin_center_imag_ohm", "zin_imag_ohm", "center_zin_imag_ohm")))
        mag = _as_float(_first_value(row, ("zin_center_abs_ohm", "zin_abs_ohm", "center_zin_abs_ohm")))
        if real is None or imag is None:
            continue
        if mag is None:
            mag = float(math.hypot(real, imag))
        points.append(
            {
                "row_index": index,
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or "",
                "real_ohm": real,
                "imag_ohm": imag,
                "abs_ohm": mag,
                "freq_hz": _as_float(row.get("zin_center_freq_hz")),
            }
        )
    real_arr = np.asarray([point["real_ohm"] for point in points], dtype=float)
    imag_arr = np.asarray([point["imag_ohm"] for point in points], dtype=float)
    abs_arr = np.asarray([point["abs_ohm"] for point in points], dtype=float)
    return {
        "points": points,
        "valid_count": len(points),
        "real": real_arr,
        "imag": imag_arr,
        "abs": abs_arr,
    }


def _first_value(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
    return None


def _build_checks(
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    labels: dict[str, Any],
    target_envelope: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks = []
    config_summary = getattr(args, "_target_envelope_config_summary", {"configured": False, "status": "NOT_CONFIGURED"})
    if config_summary.get("configured"):
        checks.append(
            _check(
                config_summary.get("status") == "PASS",
                "Zin target envelope config",
                config_summary.get("error", f"path={config_summary.get('path')}; applied={config_summary.get('applied_fields', {})}"),
            )
        )
    checks.append(_check(bool(rows), "dataset rows", f"rows={len(rows)}, ok_rows={len(ok_rows)}"))
    checks.append(_check("_parse_error" not in manifest, "manifest parses", manifest.get("_parse_error", "ok") if manifest else "missing"))
    valid = int(labels["valid_count"])
    if valid == 0:
        checks.append({"status": "NOT_READY", "name": "Zin labels", "detail": "No center-frequency Zin labels found in ok rows"})
        return checks
    checks.append(_check(True, "Zin labels", f"valid={valid}"))
    if args.require_all_ok:
        checks.append(_check(valid == len(ok_rows), "Zin label count", f"valid={valid}, ok_rows={len(ok_rows)}"))
    if args.min_valid_count is not None:
        checks.append(_check(valid >= int(args.min_valid_count), "minimum valid Zin count", f"valid={valid}, required={args.min_valid_count}"))

    real_span = _span(labels["real"])
    imag_span = _span(labels["imag"])
    abs_span = _span(labels["abs"])
    _append_span_check(checks, "Zin real span", real_span, args.min_real_span_ohm)
    _append_span_check(checks, "Zin imag span", imag_span, args.min_imag_span_ohm)
    _append_span_check(checks, "Zin abs span", abs_span, args.min_abs_span_ohm)

    real_bins = _occupied_bins(labels["real"], int(args.bins))
    imag_bins = _occupied_bins(labels["imag"], int(args.bins))
    occupied_2d_bins = _occupied_2d_bins(labels["real"], labels["imag"], int(args.bins))
    if args.min_real_bins is not None:
        checks.append(_check(real_bins >= int(args.min_real_bins), "Zin real occupied bins", f"occupied={real_bins}, required={args.min_real_bins}"))
    if args.min_imag_bins is not None:
        checks.append(_check(imag_bins >= int(args.min_imag_bins), "Zin imag occupied bins", f"occupied={imag_bins}, required={args.min_imag_bins}"))
    if args.min_occupied_2d_bins is not None:
        checks.append(
            _check(
                occupied_2d_bins >= int(args.min_occupied_2d_bins),
                "Zin 2D occupied bins",
                f"occupied={occupied_2d_bins}, required={args.min_occupied_2d_bins}",
            )
        )
    checks.append(_check(True, "Zin occupied bins", f"real={real_bins}/{args.bins}, imag={imag_bins}/{args.bins}, 2d={occupied_2d_bins}/{int(args.bins) ** 2}"))
    _append_target_envelope_checks(checks, target_envelope, args)
    return checks


def _target_envelope_config(args: argparse.Namespace) -> dict[str, Any] | None:
    raw = {
        "real_min_ohm": args.target_real_min_ohm,
        "real_max_ohm": args.target_real_max_ohm,
        "imag_min_ohm": args.target_imag_min_ohm,
        "imag_max_ohm": args.target_imag_max_ohm,
    }
    threshold_requested = (
        args.min_target_envelope_area_frac is not None
        or args.min_target_envelope_occupied_2d_bins is not None
        or args.max_target_envelope_outside_frac is not None
    )
    if all(value is None for value in raw.values()):
        if threshold_requested:
            return {"configured": False, "status": "FAIL", "error": "target envelope thresholds require all target Re/Im bounds"}
        return None
    missing = [key for key, value in raw.items() if value is None]
    if missing:
        return {"configured": False, "status": "FAIL", "error": f"missing target envelope bounds: {missing}"}
    real_min = float(raw["real_min_ohm"])
    real_max = float(raw["real_max_ohm"])
    imag_min = float(raw["imag_min_ohm"])
    imag_max = float(raw["imag_max_ohm"])
    if real_max <= real_min or imag_max <= imag_min:
        return {"configured": False, "status": "FAIL", "error": "target envelope max bounds must be greater than min bounds"}
    return {
        "configured": True,
        "status": "PASS",
        "real_min_ohm": real_min,
        "real_max_ohm": real_max,
        "imag_min_ohm": imag_min,
        "imag_max_ohm": imag_max,
        "area_ohm2": (real_max - real_min) * (imag_max - imag_min),
    }


def _target_envelope_summary(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    config = _target_envelope_config(args)
    if config is None:
        return {"configured": False, "status": "NOT_CONFIGURED"}
    if not config.get("configured"):
        return config
    valid = int(labels["valid_count"])
    if valid == 0:
        return {**config, "inside_count": 0, "outside_count": 0, "inside_fraction": None, "outside_fraction": None}
    real = labels["real"]
    imag = labels["imag"]
    mask = (
        (real >= float(config["real_min_ohm"]))
        & (real <= float(config["real_max_ohm"]))
        & (imag >= float(config["imag_min_ohm"]))
        & (imag <= float(config["imag_max_ohm"]))
    )
    inside_count = int(np.count_nonzero(mask))
    outside_count = valid - inside_count
    hull_area = _convex_hull_area(real[mask], imag[mask]) if inside_count else 0.0
    area = float(config["area_ohm2"])
    target_counts, _real_edges, _imag_edges = np.histogram2d(
        real,
        imag,
        bins=int(args.bins),
        range=[
            (float(config["real_min_ohm"]), float(config["real_max_ohm"])),
            (float(config["imag_min_ohm"]), float(config["imag_max_ohm"])),
        ],
    )
    return {
        **config,
        "valid_count": valid,
        "inside_count": inside_count,
        "outside_count": outside_count,
        "inside_fraction": inside_count / valid,
        "outside_fraction": outside_count / valid,
        "inside_convex_hull_area_ohm2": hull_area,
        "inside_convex_hull_area_fraction": hull_area / area if area > 0.0 else None,
        "occupied_2d_bins": int(np.count_nonzero(target_counts)),
        "total_2d_bins": int(args.bins) ** 2,
        "covered_2d_bins": int(np.count_nonzero(target_counts >= max(int(args.target_count_per_bin), 1))),
    }


def _append_target_envelope_checks(
    checks: list[dict[str, str]],
    target_envelope: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    if target_envelope.get("status") == "NOT_CONFIGURED":
        if (
            args.min_target_envelope_area_frac is not None
            or args.min_target_envelope_occupied_2d_bins is not None
            or args.max_target_envelope_outside_frac is not None
        ):
            checks.append(_check(False, "Zin target envelope", "target envelope thresholds were configured without target Re/Im bounds"))
        return
    checks.append(
        _check(
            target_envelope.get("configured") is True,
            "Zin target envelope configured",
            target_envelope.get("error", "target Re/Im bounds are valid"),
        )
    )
    if target_envelope.get("configured") is not True:
        return
    if args.max_target_envelope_outside_frac is not None:
        outside = target_envelope.get("outside_fraction")
        checks.append(
            _check(
                outside is not None and float(outside) <= float(args.max_target_envelope_outside_frac),
                "Zin target envelope outside fraction",
                f"outside_fraction={outside}, limit={args.max_target_envelope_outside_frac}",
            )
        )
    if args.min_target_envelope_area_frac is not None:
        area_frac = target_envelope.get("inside_convex_hull_area_fraction")
        checks.append(
            _check(
                area_frac is not None and float(area_frac) >= float(args.min_target_envelope_area_frac),
                "Zin target envelope hull area",
                f"area_fraction={area_frac}, required={args.min_target_envelope_area_frac}",
            )
        )
    if args.min_target_envelope_occupied_2d_bins is not None:
        occupied = int(target_envelope.get("occupied_2d_bins") or 0)
        checks.append(
            _check(
                occupied >= int(args.min_target_envelope_occupied_2d_bins),
                "Zin target envelope occupied 2D bins",
                f"occupied={occupied}, required={args.min_target_envelope_occupied_2d_bins}",
            )
        )


def _append_span_check(checks: list[dict[str, str]], name: str, span: float, required: float | None) -> None:
    if required is None:
        checks.append(_check(True, name, f"span={span:.6g} ohm; no minimum configured"))
    else:
        checks.append(_check(span >= float(required), name, f"span={span:.6g} ohm, required={float(required):.6g} ohm"))


def _overall_status(checks: list[dict[str, str]], labels: dict[str, Any]) -> str:
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if int(labels["valid_count"]) == 0:
        return "NOT_READY"
    return "PASS"


def _label_summary(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    valid = int(labels["valid_count"])
    if valid == 0:
        return {
            "valid_count": 0,
            "real_ohm": _empty_summary(),
            "imag_ohm": _empty_summary(),
            "abs_ohm": _empty_summary(),
            "occupied_bins": {"real": 0, "imag": 0, "bins": int(args.bins)},
            "convex_hull_area_ohm2": 0.0,
        }
    return {
        "valid_count": valid,
        "real_ohm": _array_summary(labels["real"]),
        "imag_ohm": _array_summary(labels["imag"]),
        "abs_ohm": _array_summary(labels["abs"]),
        "occupied_bins": {
            "real": _occupied_bins(labels["real"], int(args.bins)),
            "imag": _occupied_bins(labels["imag"], int(args.bins)),
            "bins": int(args.bins),
        },
        "convex_hull_area_ohm2": _convex_hull_area(labels["real"], labels["imag"]),
    }


def _empty_summary() -> dict[str, float | None]:
    return {"min": None, "max": None, "mean": None, "std": None, "span": None}


def _array_summary(values: np.ndarray) -> dict[str, float]:
    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "span": _span(values),
    }


def _span(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.max(values) - np.min(values))


def _occupied_bins(values: np.ndarray, bins: int) -> int:
    if values.size == 0:
        return 0
    if bins <= 0:
        return 0
    lo = float(np.min(values))
    hi = float(np.max(values))
    if math.isclose(lo, hi):
        return 1
    counts, _edges = np.histogram(values, bins=bins, range=(lo, hi))
    return int(np.count_nonzero(counts))


def _occupied_2d_bins(real: np.ndarray, imag: np.ndarray, bins: int) -> int:
    if real.size == 0 or imag.size == 0 or bins <= 0:
        return 0
    counts, _real_edges, _imag_edges = np.histogram2d(real, imag, bins=bins, range=[_hist_range(real), _hist_range(imag)])
    return int(np.count_nonzero(counts))


def _hist_range(values: np.ndarray) -> tuple[float, float]:
    lo = float(np.min(values))
    hi = float(np.max(values))
    if math.isclose(lo, hi):
        pad = max(abs(lo) * 0.05, 0.5)
        return lo - pad, hi + pad
    return lo, hi


def _bin_occupancy(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    bins = max(int(args.bins), 0)
    target = max(int(args.target_count_per_bin), 1)
    if int(labels["valid_count"]) == 0 or bins == 0:
        return {"bins": bins, "target_count_per_bin": target, "cells": []}
    counts, real_edges, imag_edges = np.histogram2d(
        labels["real"],
        labels["imag"],
        bins=bins,
        range=[_hist_range(labels["real"]), _hist_range(labels["imag"])],
    )
    cells = []
    for real_idx in range(bins):
        for imag_idx in range(bins):
            count = int(counts[real_idx, imag_idx])
            if count == 0:
                status = "empty"
            elif count < target:
                status = "sparse"
            else:
                status = "covered"
            cells.append(
                {
                    "real_bin": real_idx,
                    "imag_bin": imag_idx,
                    "real_min_ohm": float(real_edges[real_idx]),
                    "real_max_ohm": float(real_edges[real_idx + 1]),
                    "imag_min_ohm": float(imag_edges[imag_idx]),
                    "imag_max_ohm": float(imag_edges[imag_idx + 1]),
                    "count": count,
                    "status": status,
                }
            )
    return {"bins": bins, "target_count_per_bin": target, "cells": cells}


def _target_envelope_occupancy(
    labels: dict[str, Any],
    target_envelope: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    bins = max(int(args.bins), 0)
    target = max(int(args.target_count_per_bin), 1)
    if int(labels["valid_count"]) == 0 or bins == 0 or target_envelope.get("configured") is not True:
        return {"bins": bins, "target_count_per_bin": target, "cells": []}
    real_range = (float(target_envelope["real_min_ohm"]), float(target_envelope["real_max_ohm"]))
    imag_range = (float(target_envelope["imag_min_ohm"]), float(target_envelope["imag_max_ohm"]))
    counts, real_edges, imag_edges = np.histogram2d(
        labels["real"],
        labels["imag"],
        bins=bins,
        range=[real_range, imag_range],
    )
    cells = []
    for real_idx in range(bins):
        for imag_idx in range(bins):
            count = int(counts[real_idx, imag_idx])
            if count == 0:
                status = "empty"
            elif count < target:
                status = "sparse"
            else:
                status = "covered"
            cells.append(
                {
                    "real_bin": real_idx,
                    "imag_bin": imag_idx,
                    "real_min_ohm": float(real_edges[real_idx]),
                    "real_max_ohm": float(real_edges[real_idx + 1]),
                    "imag_min_ohm": float(imag_edges[imag_idx]),
                    "imag_max_ohm": float(imag_edges[imag_idx + 1]),
                    "count": count,
                    "status": status,
                }
            )
    return {"bins": bins, "target_count_per_bin": target, "cells": cells}


def _bin_occupancy_summary(occupancy: dict[str, Any]) -> dict[str, Any]:
    cells = occupancy.get("cells", [])
    return {
        "bins": int(occupancy.get("bins", 0)),
        "target_count_per_bin": int(occupancy.get("target_count_per_bin", 1)),
        "total_2d_bins": len(cells),
        "occupied_2d_bins": sum(1 for cell in cells if int(cell["count"]) > 0),
        "empty_2d_bins": sum(1 for cell in cells if cell["status"] == "empty"),
        "sparse_2d_bins": sum(1 for cell in cells if cell["status"] == "sparse"),
        "covered_2d_bins": sum(1 for cell in cells if cell["status"] == "covered"),
    }


def _convex_hull_area(real: np.ndarray, imag: np.ndarray) -> float:
    points = sorted(set(zip(real.tolist(), imag.tolist())))
    if len(points) < 3:
        return 0.0

    def cross(origin: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    area = 0.0
    for idx, point in enumerate(hull):
        nxt = hull[(idx + 1) % len(hull)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return float(abs(area) / 2.0)


def _write_plots(
    out_dir: Path,
    labels: dict[str, Any],
    target_envelope: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    if args.no_plots:
        return []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001 - plot generation is secondary evidence.
        return [{"status": "SKIPPED", "path": "", "title": "Zin plots", "detail": f"matplotlib unavailable: {exc}"}]

    plots = []
    real = labels["real"]
    imag = labels["imag"]
    mag = labels["abs"]

    scatter_path = out_dir / "zin_center_scatter.png"
    fig, ax = plt.subplots(figsize=(7, 6))
    image = ax.scatter(real, imag, c=mag, cmap="viridis", s=28, alpha=0.85, edgecolors="none")
    if target_envelope.get("configured") is True:
        rect = plt.Rectangle(
            (float(target_envelope["real_min_ohm"]), float(target_envelope["imag_min_ohm"])),
            float(target_envelope["real_max_ohm"]) - float(target_envelope["real_min_ohm"]),
            float(target_envelope["imag_max_ohm"]) - float(target_envelope["imag_min_ohm"]),
            fill=False,
            color="#ef4444",
            linewidth=1.4,
            linestyle="--",
            label="Target envelope",
        )
        ax.add_patch(rect)
        ax.legend(loc="best", frameon=False)
    ax.axhline(0.0, color="#6b7280", linewidth=0.8)
    ax.axvline(0.0, color="#6b7280", linewidth=0.8)
    ax.set_xlabel("Re{Zin} (ohm)")
    ax.set_ylabel("Im{Zin} (ohm)")
    ax.set_title("Center-Frequency Zin Coverage")
    fig.colorbar(image, ax=ax, label="|Zin| (ohm)")
    fig.tight_layout()
    fig.savefig(scatter_path, dpi=180)
    plt.close(fig)
    plots.append({"status": "OK", "path": str(scatter_path), "title": "Center Zin scatter"})

    hist_path = out_dir / "zin_center_histograms.png"
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    for ax, arr, title in zip(axes, (real, imag, mag), ("Re{Zin}", "Im{Zin}", "|Zin|")):
        ax.hist(arr, bins=int(args.bins), color="#2563eb", alpha=0.78, edgecolor="white")
        ax.set_title(title)
        ax.set_xlabel("ohm")
        ax.set_ylabel("count")
    fig.suptitle("Center-Frequency Zin Marginals")
    fig.tight_layout()
    fig.savefig(hist_path, dpi=180)
    plt.close(fig)
    plots.append({"status": "OK", "path": str(hist_path), "title": "Center Zin histograms"})
    if target_envelope.get("configured") is True:
        heatmap_path = out_dir / "zin_target_envelope_heatmap.png"
        counts, real_edges, imag_edges = np.histogram2d(
            real,
            imag,
            bins=int(args.bins),
            range=[
                (float(target_envelope["real_min_ohm"]), float(target_envelope["real_max_ohm"])),
                (float(target_envelope["imag_min_ohm"]), float(target_envelope["imag_max_ohm"])),
            ],
        )
        fig, ax = plt.subplots(figsize=(7, 6))
        mesh = ax.imshow(
            counts.T,
            origin="lower",
            aspect="auto",
            extent=[real_edges[0], real_edges[-1], imag_edges[0], imag_edges[-1]],
            cmap="magma",
        )
        ax.set_xlabel("Re{Zin} (ohm)")
        ax.set_ylabel("Im{Zin} (ohm)")
        ax.set_title("Target-Envelope Zin Bin Occupancy")
        fig.colorbar(mesh, ax=ax, label="count")
        fig.tight_layout()
        fig.savefig(heatmap_path, dpi=180)
        plt.close(fig)
        plots.append({"status": "OK", "path": str(heatmap_path), "title": "Target-envelope Zin heatmap"})
    return plots


def _write_points(path: Path, labels: dict[str, Any]) -> None:
    fields = ["row_index", "evaluation", "freq_hz", "real_ohm", "imag_ohm", "abs_ohm"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for point in labels["points"]:
            writer.writerow(point)


def _write_bins(path: Path, occupancy: dict[str, Any]) -> None:
    fields = ["real_bin", "imag_bin", "real_min_ohm", "real_max_ohm", "imag_min_ohm", "imag_max_ohm", "count", "status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in occupancy.get("cells", []):
            writer.writerow(cell)


def _render_report(summary: dict[str, Any]) -> str:
    label = summary["label_summary"]
    lines = [
        "# Zin Coverage Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Valid Zin count: {label['valid_count']}",
        "",
        "## Coverage Summary",
        "",
        "```json",
        json.dumps(label, indent=2),
        "```",
        "",
        "## Bin Occupancy Summary",
        "",
        "```json",
        json.dumps(summary["bin_occupancy"], indent=2),
        "```",
        "",
        "## Target Envelope Config",
        "",
        "```json",
        json.dumps(summary["target_envelope_config"], indent=2),
        "```",
        "",
        "## Target Envelope Summary",
        "",
        "```json",
        json.dumps(summary["target_envelope_summary"], indent=2),
        "```",
        "",
        "## Target Envelope Bin Occupancy",
        "",
        "```json",
        json.dumps(summary["target_envelope_bin_occupancy"], indent=2),
        "```",
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
            "## Interpretation",
            "",
            "PASS means existing Zin labels satisfy the configured count/span/bin gates. NOT_READY means no real Zin labels were found and Zin coverage must not be claimed.",
        ]
    )
    return "\n".join(lines) + "\n"


def _check(condition: bool, name: str, detail: str) -> dict[str, str]:
    return {"status": "PASS" if condition else "FAIL", "name": name, "detail": detail}


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "fail", "failed"}


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


if __name__ == "__main__":
    raise SystemExit(main())
