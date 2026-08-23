#!/usr/bin/env python3
"""Audit an EMX-vs-HFSS target-marker comparison table.

This script is intentionally small: it does not re-extract S parameters. It
only reads the marker CSV produced by compare_emx_hfss_ads.py and decides
whether the sample is allowed to pass the project validation gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_REQUIRED_METRICS = ("lp_nh", "ls_nh", "q", "kw")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    marker_csv = Path(args.marker_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    required_metrics = tuple(_normalize_metric(m) for m in args.required_metric)
    result = audit_marker_gate(
        marker_csv,
        max_percent_error=args.max_percent_error,
        required_metrics=required_metrics,
        emx_touchstone=Path(args.emx_touchstone).expanduser().resolve() if args.emx_touchstone else None,
        hfss_touchstone=Path(args.hfss_touchstone).expanduser().resolve() if args.hfss_touchstone else None,
        require_s8p_touchstones=args.require_s8p_touchstones,
    )

    summary_path = out_dir / "emx_hfss_marker_gate_summary.json"
    report_path = out_dir / "emx_hfss_marker_gate_report.md"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")

    print(f"overall_status={result['overall_status']}")
    print(f"block_large_scale_generation={result['block_large_scale_generation']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for item in result["metrics"]:
        print(
            f"{item['status']:4s} {item['metric']}: "
            f"percent_error={item.get('percent_error')}%, "
            f"threshold={result['max_percent_error']}%"
        )

    if result["overall_status"] == "FAIL" and not args.no_fail_exit:
        return 2
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker-csv", required=True, help="emx_hfss_ads_target_marker_metrics.csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument(
        "--required-metric",
        action="append",
        default=list(DEFAULT_REQUIRED_METRICS),
        help="Metric that must pass. Defaults: lp_nh, ls_nh, q, kw.",
    )
    parser.add_argument("--emx-touchstone", help="Optional EMX Touchstone used to produce the marker CSV")
    parser.add_argument("--hfss-touchstone", help="Optional HFSS Touchstone used to produce the marker CSV")
    parser.add_argument(
        "--require-s8p-touchstones",
        action="store_true",
        help="Require both provided Touchstone files to exist and have .s8p suffix before allowing final PASS.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def audit_marker_gate(
    marker_csv: Path,
    *,
    max_percent_error: float,
    required_metrics: tuple[str, ...] = DEFAULT_REQUIRED_METRICS,
    emx_touchstone: Path | None = None,
    hfss_touchstone: Path | None = None,
    require_s8p_touchstones: bool = False,
) -> dict[str, Any]:
    rows = _read_rows(marker_csv)
    by_metric = {_normalize_metric(row.get("metric", "")): row for row in rows if row.get("metric")}

    metric_results: list[dict[str, Any]] = []
    for metric in required_metrics:
        row = by_metric.get(metric)
        if row is None:
            metric_results.append(
                {
                    "metric": metric,
                    "status": "FAIL",
                    "reason": "missing_required_metric",
                    "percent_error": None,
                }
            )
            continue
        percent_error = _float_or_none(row.get("percent_error"))
        if percent_error is None:
            status = "FAIL"
            reason = "missing_percent_error"
        elif percent_error <= max_percent_error:
            status = "PASS"
            reason = "within_gate"
        else:
            status = "FAIL"
            reason = "exceeds_gate"
        metric_results.append(
            {
                "metric": metric,
                "status": status,
                "reason": reason,
                "percent_error": percent_error,
                "emx": _float_or_none(row.get("emx")),
                "hfss": _float_or_none(row.get("hfss_ads") or row.get("hfss")),
                "abs_error": _float_or_none(row.get("abs_error")),
                "nearest_frequency_ghz": _float_or_none(row.get("nearest_frequency_ghz")),
            }
        )

    touchstone_results = _touchstone_contract_checks(
        emx_touchstone=emx_touchstone,
        hfss_touchstone=hfss_touchstone,
        require_s8p_touchstones=require_s8p_touchstones,
    )
    metrics_pass = all(item["status"] == "PASS" for item in metric_results)
    touchstones_pass = all(item["status"] == "PASS" for item in touchstone_results)
    overall_status = "PASS" if metrics_pass and touchstones_pass else "FAIL"
    return {
        "schema": "rfic_transformer_marker_gate_audit.v1",
        "marker_csv": str(marker_csv),
        "max_percent_error": max_percent_error,
        "required_metrics": list(required_metrics),
        "require_s8p_touchstones": require_s8p_touchstones,
        "overall_status": overall_status,
        "block_large_scale_generation": overall_status != "PASS",
        "metrics": metric_results,
        "touchstone_contract": touchstone_results,
        "final_evidence_verified": metrics_pass and touchstones_pass and require_s8p_touchstones,
        "decision": (
            "Validation gate passed; this sample can support the next-stage run."
            if overall_status == "PASS"
            else "Validation gate failed; do not launch large-scale EMX training data generation from this state."
        ),
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# EMX/HFSS Marker Gate Audit",
        "",
        f"- Marker CSV: `{result['marker_csv']}`",
        f"- Gate: percent error <= {result['max_percent_error']}%",
        f"- Require S8P Touchstones: **{result['require_s8p_touchstones']}**",
        f"- Overall status: **{result['overall_status']}**",
        f"- Block large-scale generation: **{result['block_large_scale_generation']}**",
        f"- Final evidence verified: **{result['final_evidence_verified']}**",
        "",
        "| Metric | Percent error | Status | Reason |",
        "|---|---:|---|---|",
    ]
    for item in result["metrics"]:
        pct = "missing" if item.get("percent_error") is None else f"{item['percent_error']:.3f}%"
        lines.append(f"| `{item['metric']}` | {pct} | {item['status']} | {item['reason']} |")
    if result.get("touchstone_contract"):
        lines.extend(["", "| Touchstone check | Path | Status | Reason |", "|---|---|---|---|"])
        for item in result["touchstone_contract"]:
            lines.append(
                f"| `{item['name']}` | `{item.get('path', '')}` | {item['status']} | {item['reason']} |"
            )
    elif not result["require_s8p_touchstones"]:
        lines.extend(
            [
                "",
                "Note: this is a CSV-only diagnostic gate. It is not final evidence unless rerun with `--require-s8p-touchstones` and both EMX/HFSS `.s8p` paths.",
            ]
        )
    lines.extend(["", result["decision"], ""])
    return "\n".join(lines)


def _touchstone_contract_checks(
    *,
    emx_touchstone: Path | None,
    hfss_touchstone: Path | None,
    require_s8p_touchstones: bool,
) -> list[dict[str, Any]]:
    if not require_s8p_touchstones and emx_touchstone is None and hfss_touchstone is None:
        return []
    checks = [
        _touchstone_check("emx_s8p", emx_touchstone, require_s8p_touchstones),
        _touchstone_check("hfss_s8p", hfss_touchstone, require_s8p_touchstones),
    ]
    if all(item["status"] == "PASS" for item in checks):
        checks.append(_touchstone_pair_check(checks[0], checks[1]))
    return checks


def _touchstone_check(name: str, path: Path | None, required: bool) -> dict[str, Any]:
    if path is None:
        return {
            "name": name,
            "path": "",
            "status": "FAIL" if required else "PASS",
            "reason": "missing_required_path" if required else "not_required",
        }
    exists = path.is_file()
    suffix_ok = path.suffix.lower() == ".s8p"
    if not exists:
        status = "FAIL"
        reason = "file_not_found"
    elif not suffix_ok:
        status = "FAIL"
        reason = "touchstone_suffix_is_not_s8p"
    else:
        inspection = _inspect_touchstone(path, port_count=8)
        status = inspection.pop("status")
        reason = inspection.pop("reason")
        return {
            "name": name,
            "path": str(path),
            "status": status,
            "reason": reason,
            **inspection,
        }
    return {
        "name": name,
        "path": str(path),
        "status": status,
        "reason": reason,
    }


def _inspect_touchstone(path: Path, *, port_count: int) -> dict[str, Any]:
    option = _read_touchstone_option(path)
    if option["parameter_kind"] != "S":
        return {
            "status": "FAIL",
            "reason": "touchstone_parameter_is_not_s",
            **option,
        }
    freqs = _read_touchstone_frequencies_hz(path, port_count=port_count, unit=option["frequency_unit"])
    if not freqs:
        return {
            "status": "FAIL",
            "reason": "no_complete_touchstone_frequency_blocks",
            **option,
            "port_count": port_count,
            "frequency_point_count": 0,
        }
    return {
        "status": "PASS",
        "reason": "exists_with_valid_s8p_content",
        **option,
        "port_count": port_count,
        "frequency_point_count": len(freqs),
        "first_frequency_hz": freqs[0],
        "last_frequency_hz": freqs[-1],
        "_frequencies_hz": freqs,
    }


def _touchstone_pair_check(emx: dict[str, Any], hfss: dict[str, Any]) -> dict[str, Any]:
    emx_freqs = emx.get("_frequencies_hz", [])
    hfss_freqs = hfss.get("_frequencies_hz", [])
    ref_match = _nearly_equal(emx.get("reference_ohm"), hfss.get("reference_ohm"), rel_tol=1e-9)
    grid_match = len(emx_freqs) == len(hfss_freqs) and all(
        _nearly_equal(a, b, rel_tol=1e-9, abs_tol=1.0) for a, b in zip(emx_freqs, hfss_freqs)
    )
    port_match = emx.get("port_count") == hfss.get("port_count") == 8
    status = "PASS" if ref_match and grid_match and port_match else "FAIL"
    if not port_match:
        reason = "port_count_mismatch"
    elif not ref_match:
        reason = "reference_impedance_mismatch"
    elif not grid_match:
        reason = "frequency_grid_mismatch"
    else:
        reason = "emx_hfss_s8p_specs_match"
    return {
        "name": "emx_hfss_s8p_pair",
        "path": f"{emx.get('path', '')} :: {hfss.get('path', '')}",
        "status": status,
        "reason": reason,
        "emx_frequency_point_count": emx.get("frequency_point_count"),
        "hfss_frequency_point_count": hfss.get("frequency_point_count"),
        "emx_first_frequency_hz": emx.get("first_frequency_hz"),
        "hfss_first_frequency_hz": hfss.get("first_frequency_hz"),
        "emx_last_frequency_hz": emx.get("last_frequency_hz"),
        "hfss_last_frequency_hz": hfss.get("last_frequency_hz"),
        "emx_reference_ohm": emx.get("reference_ohm"),
        "hfss_reference_ohm": hfss.get("reference_ohm"),
    }


def _read_touchstone_option(path: Path) -> dict[str, Any]:
    option = {
        "frequency_unit": "GHZ",
        "parameter_kind": "S",
        "data_format": "MA",
        "reference_ohm": 50.0,
    }
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line.startswith("#"):
                continue
            tokens = line[1:].strip().upper().split()
            if tokens:
                option["frequency_unit"] = tokens[0]
            if len(tokens) > 1:
                option["parameter_kind"] = tokens[1]
            if len(tokens) > 2:
                option["data_format"] = tokens[2]
            if "R" in tokens:
                idx = tokens.index("R")
                if idx + 1 < len(tokens):
                    option["reference_ohm"] = _float_or_none(tokens[idx + 1])
            break
    return option


def _read_touchstone_frequencies_hz(path: Path, *, port_count: int, unit: str) -> list[float]:
    scale = {
        "HZ": 1.0,
        "KHZ": 1e3,
        "MHZ": 1e6,
        "GHZ": 1e9,
    }.get(unit.upper(), 1.0)
    numbers_per_frequency = port_count * port_count * 2
    numeric_tokens: list[float] = []
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.split("!", 1)[0].strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            for token in line.split():
                value = _float_or_none(token)
                if value is not None:
                    numeric_tokens.append(value)

    freqs: list[float] = []
    index = 0
    while index < len(numeric_tokens):
        freqs.append(numeric_tokens[index] * scale)
        index += 1 + numbers_per_frequency
    if index != len(numeric_tokens):
        return []
    return freqs


def _nearly_equal(left: object, right: object, *, rel_tol: float, abs_tol: float = 0.0) -> bool:
    left_float = _float_or_none(left)
    right_float = _float_or_none(right)
    if left_float is None or right_float is None:
        return False
    return abs(left_float - right_float) <= max(abs_tol, rel_tol * max(abs(left_float), abs(right_float), 1.0))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _normalize_metric(metric: str) -> str:
    value = metric.strip().lower()
    aliases = {
        "lp": "lp_nh",
        "ls": "ls_nh",
        "k": "kw",
        "k_signed": "kw",
        "kw_abs": "kw",
        "q_min": "q",
    }
    return aliases.get(value, value)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
