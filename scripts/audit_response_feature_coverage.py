#!/usr/bin/env python3
"""Audit response-label coverage beyond Zin for training readiness.

This gate reads rows produced by `extract_touchstone_response_features.py` and
checks that K/Q/L/Cm labels are present, finite, physically plausible, and wide
enough for configured response-space coverage claims.
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


CORE_METRICS = ("lp_nh_center", "ls_nh_center", "k_center", "qp_center", "qs_center")
CM_METRICS = (
    "cm_single_primary_y11_plus_y12_ff_center",
    "cm_single_primary_y22_plus_y21_ff_center",
    "cm_single_secondary_y33_plus_y34_ff_center",
    "cm_single_secondary_y44_plus_y43_ff_center",
    "cm_diff_primary_y11_plus_y12_ff_center",
    "cm_diff_secondary_y22_plus_y21_ff_center",
)
OPTIONAL_RESPONSE_METRICS = (
    "zin_center_real_ohm",
    "zin_center_imag_ohm",
    "zin_center_abs_ohm",
    *CM_METRICS,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_target_envelope_config(args)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "response_feature_coverage_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(dataset_dir / "dataset_rows.csv")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    labels = _collect_labels(ok_rows, require_cm=bool(args.require_cm))
    target_envelopes = _target_envelope_summaries(labels, args)
    checks = _build_checks(rows, ok_rows, labels, target_envelopes, args)
    overall_status = _overall_status(checks, labels)
    metric_summary = _metric_summary(labels)
    plots = _write_plots(out_dir, labels, target_envelopes, args) if labels["valid_count"] else []
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "rows": {"row_count": len(rows), "ok_count": len(ok_rows)},
        "label_summary": {
            "valid_count": int(labels["valid_count"]),
            "required_metrics": labels["required_metrics"],
            "optional_metrics_present": labels["optional_metrics_present"],
            "missing_required_counts": labels["missing_required_counts"],
            "metric_summary": metric_summary,
        },
        "coverage": _coverage_summary(labels, args),
        "target_envelope_config": getattr(args, "_target_envelope_config_summary", {"configured": False}),
        "target_envelopes": target_envelopes,
        "checks": checks,
        "plots": plots,
        "limitations": [
            "This audit checks labels extracted from existing Touchstone files; it does not run EMX, HFSS, or ADS.",
            "PASS only means the configured response-label gates pass for the available local rows.",
            "Use simulator correlation on sampled designs before accepting a dataset for production training.",
        ],
    }
    summary_path = out_dir / "response_feature_coverage_summary.json"
    report_path = out_dir / "response_feature_coverage_report.md"
    points_path = out_dir / "response_feature_points.csv"
    metrics_path = out_dir / "response_feature_metric_summary.csv"
    k_qp_bins_path = out_dir / "response_target_k_qp_bins.csv"
    lp_ls_bins_path = out_dir / "response_target_lp_ls_bins.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_points(points_path, labels)
    _write_metric_summary(metrics_path, metric_summary)
    _write_target_bins(k_qp_bins_path, target_envelopes.get("k_qp", {}))
    _write_target_bins(lp_ls_bins_path, target_envelopes.get("lp_ls", {}))

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"points_csv={points_path}")
    print(f"metric_summary_csv={metrics_path}")
    print(f"target_k_qp_bins_csv={k_qp_bins_path}")
    print(f"target_lp_ls_bins_csv={lp_ls_bins_path}")
    for check in checks:
        print(f"{check['status']:9s} {check['name']}: {check['detail']}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--require-all-ok", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-cm", action="store_true")
    parser.add_argument("--min-valid-count", type=int)
    parser.add_argument("--min-l-nh", type=float, default=0.0)
    parser.add_argument("--min-q", type=float, default=0.0)
    parser.add_argument("--max-abs-k", type=float, default=1.05)
    parser.add_argument("--min-lp-span-nh", type=float)
    parser.add_argument("--min-ls-span-nh", type=float)
    parser.add_argument("--min-k-span", type=float)
    parser.add_argument("--min-qp-span", type=float)
    parser.add_argument("--min-qs-span", type=float)
    parser.add_argument("--min-cm-single-primary-span-ff", type=float)
    parser.add_argument("--min-occupied-k-q-bins", type=int)
    parser.add_argument("--target-envelope-config", help="JSON file containing reusable response target-envelope bounds and thresholds")
    parser.add_argument("--target-k-min", type=float)
    parser.add_argument("--target-k-max", type=float)
    parser.add_argument("--target-qp-min", type=float)
    parser.add_argument("--target-qp-max", type=float)
    parser.add_argument("--min-target-k-qp-area-frac", type=float)
    parser.add_argument("--min-target-k-qp-occupied-2d-bins", type=int)
    parser.add_argument("--max-target-k-qp-outside-frac", type=float)
    parser.add_argument("--target-lp-min-nh", type=float)
    parser.add_argument("--target-lp-max-nh", type=float)
    parser.add_argument("--target-ls-min-nh", type=float)
    parser.add_argument("--target-ls-max-nh", type=float)
    parser.add_argument("--min-target-lp-ls-area-frac", type=float)
    parser.add_argument("--min-target-lp-ls-occupied-2d-bins", type=int)
    parser.add_argument("--max-target-lp-ls-outside-frac", type=float)
    parser.add_argument("--target-count-per-bin", type=int, default=1)
    parser.add_argument("--bins", type=int, default=10)
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
            "error": "response target-envelope config is marked TEMPLATE_ONLY; fill and save a project-specific copy before using it",
        }
        return
    envelopes = data.get("response_target_envelopes", data)
    if not isinstance(envelopes, dict):
        args._target_envelope_config_summary = {
            **summary,
            "status": "FAIL",
            "error": f"response_target_envelopes is {type(envelopes).__name__}",
        }
        return
    applied: dict[str, Any] = {}
    invalid: list[str] = []
    common_target = envelopes.get("target_count_per_bin", data.get("target_count_per_bin"))
    if common_target is not None:
        try:
            count = int(common_target)
        except (TypeError, ValueError):
            invalid.append(f"target_count_per_bin={common_target!r}")
        else:
            if getattr(args, "target_count_per_bin") == 1:
                args.target_count_per_bin = count
                applied["target_count_per_bin"] = count
    invalid.extend(
        _apply_envelope_section(
            args,
            envelopes.get("k_qp", {}),
            {
                "k_min": "target_k_min",
                "k_max": "target_k_max",
                "qp_min": "target_qp_min",
                "qp_max": "target_qp_max",
                "min_area_fraction": "min_target_k_qp_area_frac",
                "min_occupied_2d_bins": "min_target_k_qp_occupied_2d_bins",
                "max_outside_fraction": "max_target_k_qp_outside_frac",
            },
            applied,
            "k_qp",
        )
    )
    invalid.extend(
        _apply_envelope_section(
            args,
            envelopes.get("lp_ls", {}),
            {
                "lp_min_nh": "target_lp_min_nh",
                "lp_max_nh": "target_lp_max_nh",
                "ls_min_nh": "target_ls_min_nh",
                "ls_max_nh": "target_ls_max_nh",
                "min_area_fraction": "min_target_lp_ls_area_frac",
                "min_occupied_2d_bins": "min_target_lp_ls_occupied_2d_bins",
                "max_outside_fraction": "max_target_lp_ls_outside_frac",
            },
            applied,
            "lp_ls",
        )
    )
    if invalid:
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"invalid fields: {invalid}"}
        return
    args._target_envelope_config_summary = {
        **summary,
        "status": "PASS",
        "schema": data.get("schema", "direct_or_response_target_envelopes"),
        "name": data.get("name") or envelopes.get("name"),
        "applied_fields": applied,
        "notes": data.get("notes", []),
    }


def _apply_envelope_section(
    args: argparse.Namespace,
    section: Any,
    field_map: dict[str, str],
    applied: dict[str, Any],
    prefix: str,
) -> list[str]:
    if section in (None, {}):
        return []
    if not isinstance(section, dict):
        return [f"{prefix}={type(section).__name__}"]
    invalid: list[str] = []
    for source_key, arg_name in field_map.items():
        if source_key not in section or section[source_key] is None:
            continue
        value = section[source_key]
        try:
            coerced: Any = int(value) if arg_name.endswith("occupied_2d_bins") else float(value)
        except (TypeError, ValueError):
            invalid.append(f"{prefix}.{source_key}={value!r}")
            continue
        if getattr(args, arg_name) is None:
            setattr(args, arg_name, coerced)
            applied[arg_name] = coerced
    return invalid


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _collect_labels(rows: list[dict[str, str]], *, require_cm: bool) -> dict[str, Any]:
    required_metrics = list(CORE_METRICS)
    if require_cm:
        required_metrics.extend(CM_METRICS)
    points = []
    missing_counts = {name: 0 for name in required_metrics}
    optional_present = sorted(
        name for name in OPTIONAL_RESPONSE_METRICS if any(_as_float(row.get(name)) is not None for row in rows)
    )
    for index, row in enumerate(rows):
        values: dict[str, float] = {}
        complete = True
        for metric in required_metrics:
            value = _as_float(row.get(metric))
            if value is None:
                missing_counts[metric] += 1
                complete = False
            else:
                values[metric] = value
        if not complete:
            continue
        for metric in OPTIONAL_RESPONSE_METRICS:
            if metric in values:
                continue
            value = _as_float(row.get(metric))
            if value is not None:
                values[metric] = value
        values.update(
            {
                "row_index": float(index),
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or "",
            }
        )
        points.append(values)
    return {
        "points": points,
        "valid_count": len(points),
        "required_metrics": required_metrics,
        "optional_metrics_present": optional_present,
        "missing_required_counts": missing_counts,
    }


def _build_checks(
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    labels: dict[str, Any],
    target_envelopes: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks = []
    config_summary = getattr(args, "_target_envelope_config_summary", {"configured": False, "status": "NOT_CONFIGURED"})
    if config_summary.get("configured"):
        checks.append(
            _check(
                config_summary.get("status") == "PASS",
                "response target envelope config",
                config_summary.get("error", f"path={config_summary.get('path')}; applied={config_summary.get('applied_fields', {})}"),
            )
        )
    checks.append(_check(bool(rows), "dataset rows", f"rows={len(rows)}, ok_rows={len(ok_rows)}"))
    valid = int(labels["valid_count"])
    if valid == 0:
        missing = {name: count for name, count in labels["missing_required_counts"].items() if count}
        checks.append({"status": "NOT_READY", "name": "response labels", "detail": f"No rows have all required response labels; missing={missing}"})
        return checks
    checks.append(_check(True, "response labels", f"valid={valid}, required_metrics={labels['required_metrics']}"))
    if args.require_all_ok:
        checks.append(_check(valid == len(ok_rows), "response label count", f"valid={valid}, ok_rows={len(ok_rows)}"))
    if args.min_valid_count is not None:
        checks.append(_check(valid >= int(args.min_valid_count), "minimum response label count", f"valid={valid}, required={args.min_valid_count}"))

    arrays = _metric_arrays(labels)
    checks.append(_check(float(np.nanmin(arrays["lp_nh_center"])) > float(args.min_l_nh), "positive Lp", f"min={float(np.nanmin(arrays['lp_nh_center'])):.6g} nH"))
    checks.append(_check(float(np.nanmin(arrays["ls_nh_center"])) > float(args.min_l_nh), "positive Ls", f"min={float(np.nanmin(arrays['ls_nh_center'])):.6g} nH"))
    checks.append(_check(float(np.nanmin(arrays["qp_center"])) > float(args.min_q), "positive Qp", f"min={float(np.nanmin(arrays['qp_center'])):.6g}"))
    checks.append(_check(float(np.nanmin(arrays["qs_center"])) > float(args.min_q), "positive Qs", f"min={float(np.nanmin(arrays['qs_center'])):.6g}"))
    max_abs_k = float(np.nanmax(np.abs(arrays["k_center"])))
    checks.append(_check(max_abs_k <= float(args.max_abs_k), "K magnitude", f"max_abs={max_abs_k:.6g}, limit={float(args.max_abs_k):.6g}"))

    _append_span_check(checks, "Lp span", _span(arrays["lp_nh_center"]), args.min_lp_span_nh, "nH")
    _append_span_check(checks, "Ls span", _span(arrays["ls_nh_center"]), args.min_ls_span_nh, "nH")
    _append_span_check(checks, "K span", _span(arrays["k_center"]), args.min_k_span, "")
    _append_span_check(checks, "Qp span", _span(arrays["qp_center"]), args.min_qp_span, "")
    _append_span_check(checks, "Qs span", _span(arrays["qs_center"]), args.min_qs_span, "")
    cm = arrays.get("cm_single_primary_y11_plus_y12_ff_center")
    if cm is not None:
        _append_span_check(checks, "single-ended primary Cm span", _span(cm), args.min_cm_single_primary_span_ff, "fF")
    elif args.min_cm_single_primary_span_ff is not None:
        checks.append(_check(False, "single-ended primary Cm span", "required Cm column is missing"))

    occupied_k_q = _occupied_2d_bins(arrays["k_center"], arrays["qp_center"], int(args.bins))
    if args.min_occupied_k_q_bins is not None:
        checks.append(
            _check(
                occupied_k_q >= int(args.min_occupied_k_q_bins),
                "K/Qp occupied bins",
                f"occupied={occupied_k_q}, required={args.min_occupied_k_q_bins}",
            )
        )
    checks.append(_check(True, "response occupied bins", f"K/Qp={occupied_k_q}/{int(args.bins) ** 2}"))
    _append_target_envelope_checks(checks, target_envelopes)
    return checks


def _target_envelope_summaries(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    arrays = _metric_arrays(labels)
    return {
        "k_qp": _target_pair_summary(
            arrays,
            x_metric="k_center",
            y_metric="qp_center",
            x_label="K",
            y_label="Qp",
            x_min=args.target_k_min,
            x_max=args.target_k_max,
            y_min=args.target_qp_min,
            y_max=args.target_qp_max,
            min_area_frac=args.min_target_k_qp_area_frac,
            min_occupied_bins=args.min_target_k_qp_occupied_2d_bins,
            max_outside_frac=args.max_target_k_qp_outside_frac,
            bins=int(args.bins),
            target_count_per_bin=int(args.target_count_per_bin),
        ),
        "lp_ls": _target_pair_summary(
            arrays,
            x_metric="lp_nh_center",
            y_metric="ls_nh_center",
            x_label="Lp (nH)",
            y_label="Ls (nH)",
            x_min=args.target_lp_min_nh,
            x_max=args.target_lp_max_nh,
            y_min=args.target_ls_min_nh,
            y_max=args.target_ls_max_nh,
            min_area_frac=args.min_target_lp_ls_area_frac,
            min_occupied_bins=args.min_target_lp_ls_occupied_2d_bins,
            max_outside_frac=args.max_target_lp_ls_outside_frac,
            bins=int(args.bins),
            target_count_per_bin=int(args.target_count_per_bin),
        ),
    }


def _target_pair_summary(
    arrays: dict[str, np.ndarray],
    *,
    x_metric: str,
    y_metric: str,
    x_label: str,
    y_label: str,
    x_min: float | None,
    x_max: float | None,
    y_min: float | None,
    y_max: float | None,
    min_area_frac: float | None,
    min_occupied_bins: int | None,
    max_outside_frac: float | None,
    bins: int,
    target_count_per_bin: int,
) -> dict[str, Any]:
    threshold_requested = min_area_frac is not None or min_occupied_bins is not None or max_outside_frac is not None
    raw_bounds = {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max}
    if all(value is None for value in raw_bounds.values()):
        if threshold_requested:
            return {
                "configured": False,
                "status": "FAIL",
                "x_metric": x_metric,
                "y_metric": y_metric,
                "error": "target-envelope thresholds require all target bounds",
            }
        return {"configured": False, "status": "NOT_CONFIGURED", "x_metric": x_metric, "y_metric": y_metric}
    missing = [key for key, value in raw_bounds.items() if value is None]
    if missing:
        return {
            "configured": False,
            "status": "FAIL",
            "x_metric": x_metric,
            "y_metric": y_metric,
            "error": f"missing target-envelope bounds: {missing}",
        }
    x_lo = float(x_min)
    x_hi = float(x_max)
    y_lo = float(y_min)
    y_hi = float(y_max)
    if x_hi <= x_lo or y_hi <= y_lo:
        return {
            "configured": False,
            "status": "FAIL",
            "x_metric": x_metric,
            "y_metric": y_metric,
            "error": "target-envelope max bounds must be greater than min bounds",
        }
    x = arrays.get(x_metric)
    y = arrays.get(y_metric)
    if x is None or y is None or x.size == 0 or y.size == 0:
        return {
            "configured": True,
            "status": "NOT_READY",
            "x_metric": x_metric,
            "y_metric": y_metric,
            "x_label": x_label,
            "y_label": y_label,
            "x_min": x_lo,
            "x_max": x_hi,
            "y_min": y_lo,
            "y_max": y_hi,
            "error": "required response arrays are missing",
        }
    mask = (x >= x_lo) & (x <= x_hi) & (y >= y_lo) & (y <= y_hi)
    valid_count = int(min(x.size, y.size))
    inside_count = int(np.count_nonzero(mask))
    outside_count = valid_count - inside_count
    envelope_area = (x_hi - x_lo) * (y_hi - y_lo)
    inside_area = _convex_hull_area(x[mask], y[mask]) if inside_count else 0.0
    occupancy = _target_pair_occupancy(x, y, x_lo, x_hi, y_lo, y_hi, bins, target_count_per_bin)
    return {
        "configured": True,
        "status": "PASS",
        "x_metric": x_metric,
        "y_metric": y_metric,
        "x_label": x_label,
        "y_label": y_label,
        "x_min": x_lo,
        "x_max": x_hi,
        "y_min": y_lo,
        "y_max": y_hi,
        "area": envelope_area,
        "valid_count": valid_count,
        "inside_count": inside_count,
        "outside_count": outside_count,
        "inside_fraction": inside_count / valid_count if valid_count else None,
        "outside_fraction": outside_count / valid_count if valid_count else None,
        "inside_convex_hull_area": inside_area,
        "inside_convex_hull_area_fraction": inside_area / envelope_area if envelope_area > 0.0 else None,
        "occupied_2d_bins": occupancy["occupied_2d_bins"],
        "covered_2d_bins": occupancy["covered_2d_bins"],
        "total_2d_bins": occupancy["total_2d_bins"],
        "cells": occupancy["cells"],
        "requirements": {
            "min_area_frac": min_area_frac,
            "min_occupied_bins": min_occupied_bins,
            "max_outside_frac": max_outside_frac,
        },
    }


def _target_pair_occupancy(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    bins: int,
    target_count_per_bin: int,
) -> dict[str, Any]:
    bins = max(int(bins), 0)
    target = max(int(target_count_per_bin), 1)
    if bins == 0:
        return {"cells": [], "occupied_2d_bins": 0, "covered_2d_bins": 0, "total_2d_bins": 0}
    counts, x_edges, y_edges = np.histogram2d(x, y, bins=bins, range=[(x_min, x_max), (y_min, y_max)])
    cells = []
    for x_idx in range(bins):
        for y_idx in range(bins):
            count = int(counts[x_idx, y_idx])
            status = "empty" if count == 0 else "sparse" if count < target else "covered"
            cells.append(
                {
                    "x_bin": x_idx,
                    "y_bin": y_idx,
                    "x_min": float(x_edges[x_idx]),
                    "x_max": float(x_edges[x_idx + 1]),
                    "y_min": float(y_edges[y_idx]),
                    "y_max": float(y_edges[y_idx + 1]),
                    "count": count,
                    "status": status,
                }
            )
    return {
        "cells": cells,
        "occupied_2d_bins": int(np.count_nonzero(counts)),
        "covered_2d_bins": int(np.count_nonzero(counts >= target)),
        "total_2d_bins": bins**2,
    }


def _append_target_envelope_checks(checks: list[dict[str, str]], target_envelopes: dict[str, Any]) -> None:
    for name, envelope in target_envelopes.items():
        if envelope.get("status") == "NOT_CONFIGURED":
            continue
        check_prefix = name.replace("_", "/")
        checks.append(
            _check(
                envelope.get("configured") is True,
                f"{check_prefix} target envelope configured",
                envelope.get("error", "target bounds are valid"),
            )
        )
        if envelope.get("configured") is not True:
            continue
        requirements = envelope.get("requirements", {})
        max_outside = requirements.get("max_outside_frac")
        if max_outside is not None:
            outside = envelope.get("outside_fraction")
            checks.append(
                _check(
                    outside is not None and float(outside) <= float(max_outside),
                    f"{check_prefix} target envelope outside fraction",
                    f"outside_fraction={outside}, limit={max_outside}",
                )
            )
        min_area = requirements.get("min_area_frac")
        if min_area is not None:
            area_frac = envelope.get("inside_convex_hull_area_fraction")
            checks.append(
                _check(
                    area_frac is not None and float(area_frac) >= float(min_area),
                    f"{check_prefix} target envelope hull area",
                    f"area_fraction={area_frac}, required={min_area}",
                )
            )
        min_bins = requirements.get("min_occupied_bins")
        if min_bins is not None:
            occupied = int(envelope.get("occupied_2d_bins") or 0)
            checks.append(
                _check(
                    occupied >= int(min_bins),
                    f"{check_prefix} target envelope occupied bins",
                    f"occupied={occupied}, required={min_bins}",
                )
            )


def _append_span_check(checks: list[dict[str, str]], name: str, span: float, required: float | None, unit: str) -> None:
    suffix = f" {unit}" if unit else ""
    if required is None:
        checks.append(_check(True, name, f"span={span:.6g}{suffix}; no minimum configured"))
    else:
        checks.append(_check(span >= float(required), name, f"span={span:.6g}{suffix}, required={float(required):.6g}{suffix}"))


def _overall_status(checks: list[dict[str, str]], labels: dict[str, Any]) -> str:
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if int(labels["valid_count"]) == 0:
        return "NOT_READY"
    return "PASS"


def _metric_arrays(labels: dict[str, Any]) -> dict[str, np.ndarray]:
    fields = set(CORE_METRICS) | set(OPTIONAL_RESPONSE_METRICS)
    arrays = {}
    for field in fields:
        vals = [point[field] for point in labels["points"] if field in point and isinstance(point[field], (int, float))]
        if vals:
            arrays[field] = np.asarray(vals, dtype=float)
    return arrays


def _metric_summary(labels: dict[str, Any]) -> dict[str, Any]:
    arrays = _metric_arrays(labels)
    return {name: _array_summary(values) for name, values in sorted(arrays.items())}


def _coverage_summary(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if int(labels["valid_count"]) == 0:
        return {"bins": int(args.bins), "k_qp_occupied_bins": 0}
    arrays = _metric_arrays(labels)
    return {
        "bins": int(args.bins),
        "target_count_per_bin": int(args.target_count_per_bin),
        "k_qp_occupied_bins": _occupied_2d_bins(arrays["k_center"], arrays["qp_center"], int(args.bins)),
        "k_qp_total_bins": int(args.bins) ** 2,
    }


def _array_summary(values: np.ndarray) -> dict[str, float]:
    return {
        "min": float(np.nanmin(values)),
        "max": float(np.nanmax(values)),
        "mean": float(np.nanmean(values)),
        "std": float(np.nanstd(values)),
        "span": _span(values),
    }


def _span(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.nanmax(values) - np.nanmin(values))


def _occupied_2d_bins(x: np.ndarray, y: np.ndarray, bins: int) -> int:
    if x.size == 0 or y.size == 0 or bins <= 0:
        return 0
    counts, _x_edges, _y_edges = np.histogram2d(x, y, bins=bins, range=[_hist_range(x), _hist_range(y)])
    return int(np.count_nonzero(counts))


def _hist_range(values: np.ndarray) -> tuple[float, float]:
    lo = float(np.nanmin(values))
    hi = float(np.nanmax(values))
    if math.isclose(lo, hi):
        pad = max(abs(lo) * 0.05, 0.5)
        return lo - pad, hi + pad
    return lo, hi


def _convex_hull_area(x: np.ndarray, y: np.ndarray) -> float:
    points = sorted(set(zip(x.tolist(), y.tolist())))
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
    target_envelopes: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    if args.no_plots:
        return []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001 - plot generation is secondary evidence.
        return [{"status": "SKIPPED", "path": "", "title": "Response plots", "detail": f"matplotlib unavailable: {exc}"}]
    arrays = _metric_arrays(labels)
    plots = []
    hist_path = out_dir / "response_feature_histograms.png"
    metrics = ["lp_nh_center", "ls_nh_center", "k_center", "qp_center", "qs_center", "cm_single_primary_y11_plus_y12_ff_center"]
    fig, axes = plt.subplots(2, 3, figsize=(12, 7))
    for ax, metric in zip(axes.ravel(), metrics):
        values = arrays.get(metric)
        if values is None:
            ax.text(0.5, 0.5, "missing", ha="center", va="center")
        else:
            ax.hist(values, bins=int(args.bins), color="#2563eb", alpha=0.78, edgecolor="white")
        ax.set_title(metric.replace("_center", ""))
        ax.set_ylabel("count")
    fig.tight_layout()
    fig.savefig(hist_path, dpi=180)
    plt.close(fig)
    plots.append({"status": "OK", "path": str(hist_path), "title": "Response feature histograms"})

    scatter_path = out_dir / "response_k_q_l_scatter.png"
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    axes[0].scatter(arrays["k_center"], arrays["qp_center"], c=arrays["lp_nh_center"], cmap="viridis", s=28)
    _add_target_rect(axes[0], target_envelopes.get("k_qp", {}), plt)
    axes[0].set_xlabel("K")
    axes[0].set_ylabel("Qp")
    axes[0].set_title("K vs Qp colored by Lp")
    axes[1].scatter(arrays["lp_nh_center"], arrays["ls_nh_center"], c=arrays["k_center"], cmap="coolwarm", s=28)
    _add_target_rect(axes[1], target_envelopes.get("lp_ls", {}), plt)
    axes[1].set_xlabel("Lp (nH)")
    axes[1].set_ylabel("Ls (nH)")
    axes[1].set_title("Lp vs Ls colored by K")
    for ax in axes:
        ax.grid(color="#E5E7EB")
    fig.tight_layout()
    fig.savefig(scatter_path, dpi=180)
    plt.close(fig)
    plots.append({"status": "OK", "path": str(scatter_path), "title": "Response feature scatter"})
    return plots


def _add_target_rect(ax: Any, envelope: dict[str, Any], plt: Any) -> None:
    if envelope.get("configured") is not True:
        return
    rect = plt.Rectangle(
        (float(envelope["x_min"]), float(envelope["y_min"])),
        float(envelope["x_max"]) - float(envelope["x_min"]),
        float(envelope["y_max"]) - float(envelope["y_min"]),
        fill=False,
        color="#ef4444",
        linewidth=1.3,
        linestyle="--",
        label="Target envelope",
    )
    ax.add_patch(rect)
    ax.legend(loc="best", frameon=False)


def _write_points(path: Path, labels: dict[str, Any]) -> None:
    fields = ["row_index", "evaluation", *CORE_METRICS, *OPTIONAL_RESPONSE_METRICS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for point in labels["points"]:
            writer.writerow({field: point.get(field, "") for field in fields})


def _write_metric_summary(path: Path, metric_summary: dict[str, Any]) -> None:
    fields = ["metric", "min", "max", "mean", "std", "span"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for metric, summary in metric_summary.items():
            writer.writerow({"metric": metric, **summary})


def _write_target_bins(path: Path, envelope: dict[str, Any]) -> None:
    fields = ["x_bin", "y_bin", "x_min", "x_max", "y_min", "y_max", "count", "status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in envelope.get("cells", []):
            writer.writerow(cell)


def _render_report(summary: dict[str, Any]) -> str:
    label = summary["label_summary"]
    lines = [
        "# Response Feature Coverage Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Valid response-label count: {label['valid_count']}",
        "",
        "## Metric Summary",
        "",
        "```json",
        json.dumps(label["metric_summary"], indent=2),
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
        json.dumps(summary["target_envelopes"], indent=2),
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
            "PASS means existing K/Q/L/Cm labels satisfy the configured count, physical-sanity, and coverage gates. NOT_READY means response labels are absent and response coverage must not be claimed.",
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
