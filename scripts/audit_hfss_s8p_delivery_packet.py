#!/usr/bin/env python3
"""Audit a completed HFSS S8P delivery packet against the EMX contract.

This is a delivery-level evidence gate. It does not run HFSS and it does not
declare EMX/HFSS agreement by itself. It ties together the independent evidence
that should exist after the regular workflow:

1. HFSS build payload generated from the EMX handoff.
2. HFSS build-time port manifest with explicit terminal references.
3. HFSS export manifest plus the selected `.s8p`.
4. EMX reference `.s8p`.
5. ADS-equivalent EMX/HFSS metric comparison at the target frequency.
6. Report-facing model and curve images.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_PORTS = [f"P{idx:03d}" for idx in range(1, 9)]
CORE_METRICS = ("lp_nh", "ls_nh", "q", "kw")


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "payload": _path(args.payload),
        "port_manifest": _path(args.port_manifest),
        "export_manifest": _path(args.export_manifest),
        "emx_s8p": _path(args.emx_s8p),
        "hfss_s8p": _path(args.hfss_s8p),
        "comparison_marker_csv": _path(args.comparison_marker_csv),
        "comparison_summary_json": _path(args.comparison_summary_json) if args.comparison_summary_json else None,
        "plots_dir": _path(args.plots_dir) if args.plots_dir else None,
        "model_views_dir": _path(args.model_views_dir) if args.model_views_dir else None,
    }

    payload = _read_json(paths["payload"])
    port_manifest = _read_json(paths["port_manifest"])
    export_manifest = _read_json(paths["export_manifest"])
    comparison_rows = _read_csv(paths["comparison_marker_csv"])
    comparison_summary = _read_json(paths["comparison_summary_json"]) if paths["comparison_summary_json"] else {}

    emx_touchstone = _inspect_touchstone(paths["emx_s8p"], expected_points=args.expected_points)
    hfss_touchstone = _inspect_touchstone(paths["hfss_s8p"], expected_points=args.expected_points)
    marker_metrics = _marker_metrics(comparison_rows)
    plot_assets = _plot_assets(paths["plots_dir"]) if paths["plots_dir"] else {}
    model_assets = _model_assets(paths["model_views_dir"]) if paths["model_views_dir"] else {}

    checks: list[Check] = []
    checks.extend(_payload_checks(payload, paths["payload"], args))
    checks.extend(_port_manifest_checks(port_manifest, paths["port_manifest"]))
    checks.extend(_export_manifest_checks(export_manifest, paths["export_manifest"], paths["hfss_s8p"]))
    checks.extend(_touchstone_checks("EMX", emx_touchstone, args))
    checks.extend(_touchstone_checks("HFSS", hfss_touchstone, args))
    checks.extend(_comparison_checks(marker_metrics, comparison_summary, args))
    checks.extend(_asset_checks(plot_assets, model_assets, args))

    core_errors = {
        metric: marker_metrics.get(metric, {}).get("percent_error")
        for metric in CORE_METRICS
        if metric in marker_metrics
    }
    max_core_error = max((float(v) for v in core_errors.values() if v is not None and math.isfinite(float(v))), default=None)
    core_gate = bool(core_errors) and all(
        value is not None and math.isfinite(float(value)) and float(value) <= float(args.max_error_pct)
        for value in core_errors.values()
    )

    summary = {
        "schema": "rfic_transformer_hfss_s8p_delivery_audit.v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_id": payload.get("sample_id") or _sample_from_path(paths["hfss_s8p"]),
        "decision": "READY_FOR_REPORTING" if _all_pass(checks) and core_gate else "NEEDS_REPAIR",
        "note": (
            "READY_FOR_REPORTING requires workflow evidence plus <= threshold core metric error. "
            "A valid HFSS .s8p alone is not enough."
        ),
        "threshold_percent": float(args.max_error_pct),
        "core_metrics": marker_metrics,
        "core_error_percent": core_errors,
        "max_core_error_percent": max_core_error,
        "core_metric_gate_pass": core_gate,
        "paths": {key: (str(value) if value else "") for key, value in paths.items()},
        "file_sha256": {
            key: _sha256(value)
            for key, value in paths.items()
            if value and value.is_file() and key not in {"plots_dir", "model_views_dir"}
        },
        "emx_touchstone": emx_touchstone,
        "hfss_touchstone": hfss_touchstone,
        "payload_summary": _payload_summary(payload),
        "port_manifest_summary": _port_manifest_summary(port_manifest),
        "export_manifest_summary": _export_manifest_summary(export_manifest),
        "plot_assets": plot_assets,
        "model_assets": model_assets,
        "checks": [check.__dict__ for check in checks],
    }

    summary_path = out_dir / "hfss_s8p_delivery_audit_summary.json"
    checks_path = out_dir / "hfss_s8p_delivery_audit_checks.csv"
    report_path = out_dir / "HFSS_S8P_DELIVERY_AUDIT_REPORT.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_checks(checks_path, checks)
    report_path.write_text(_render_report(summary, checks), encoding="utf-8")
    print(json.dumps({"summary": str(summary_path), "report": str(report_path), "decision": summary["decision"]}, indent=2))
    return 0 if summary["decision"] == "READY_FOR_REPORTING" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--port-manifest", required=True)
    parser.add_argument("--export-manifest", required=True)
    parser.add_argument("--emx-s8p", required=True)
    parser.add_argument("--hfss-s8p", required=True)
    parser.add_argument("--comparison-marker-csv", required=True)
    parser.add_argument("--comparison-summary-json")
    parser.add_argument("--plots-dir")
    parser.add_argument("--model-views-dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-points", type=int, default=56)
    parser.add_argument("--expected-reference-ohm", type=float, default=50.0)
    parser.add_argument("--max-error-pct", type=float, default=10.0)
    parser.add_argument("--allow-missing-assets", action="store_true")
    return parser.parse_args(argv)


def _path(value: str | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser().resolve()


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_checks(path: Path, checks: list[Check]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "name", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.__dict__)


def _check(name: str, passed: bool, detail: Any) -> Check:
    return Check("PASS" if passed else "FAIL", name, _jsonish(detail))


def _all_pass(checks: list[Check]) -> bool:
    return bool(checks) and all(check.status == "PASS" for check in checks)


def _jsonish(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_checks(payload: dict[str, Any], payload_path: Path, args: argparse.Namespace) -> list[Check]:
    hfss = payload.get("hfss") if isinstance(payload.get("hfss"), dict) else {}
    grid = payload.get("frequency_grid") if isinstance(payload.get("frequency_grid"), dict) else {}
    ports = payload.get("ports") if isinstance(payload.get("ports"), list) else []
    stack = payload.get("stack") if isinstance(payload.get("stack"), dict) else {}
    source_files = payload.get("source_files") if isinstance(payload.get("source_files"), dict) else {}
    return [
        _check("payload file exists", payload_path.is_file(), str(payload_path)),
        _check("payload sample id exists", bool(payload.get("sample_id")), payload.get("sample_id")),
        _check("payload expects .s8p export", hfss.get("expected_touchstone_suffix") == ".s8p", hfss.get("expected_touchstone_suffix")),
        _check("payload solution is Terminal", "terminal" in str(hfss.get("solution_type", "")).lower(), hfss.get("solution_type")),
        _check("payload has eight ports", [p.get("port_name") for p in ports] == EXPECTED_PORTS, [p.get("port_name") for p in ports]),
        _check("payload frequency grid is 5-60 GHz 1.0 GHz", _grid_matches(grid, args), grid),
        _check("payload carries calibration profile", bool((hfss.get("calibration_profile") or {}).get("name")), hfss.get("calibration_profile")),
        _check("payload carries conductor stack", bool((stack.get("conductors") or {}).get("metal9")) and bool((stack.get("conductors") or {}).get("metal10")), stack.get("conductors")),
        _check("payload source EMX s8p exists", _path_exists(source_files.get("emx_s8p")), source_files.get("emx_s8p")),
    ]


def _port_manifest_checks(port_manifest: dict[str, Any], path: Path) -> list[Check]:
    ports = port_manifest.get("ports") if isinstance(port_manifest.get("ports"), list) else []
    port_order = [port.get("port_name") for port in ports if isinstance(port, dict)]
    references = {
        port.get("port_name"): port.get("reference_conductors")
        for port in ports
        if isinstance(port, dict)
    }
    modes = {
        port.get("port_name"): port.get("assignment_mode")
        for port in ports
        if isinstance(port, dict)
    }
    integration_lines = {
        port.get("port_name"): port.get("integration_line")
        for port in ports
        if isinstance(port, dict)
    }
    return [
        _check("port manifest exists", path.is_file(), str(path)),
        _check("port manifest has eight ports", port_order == EXPECTED_PORTS, port_order),
        _check("each port has explicit reference conductor(s)", all(bool(ref) for ref in references.values()) and len(references) == 8, references),
        _check("each port has an integration line", all(bool(line) for line in integration_lines.values()) and len(integration_lines) == 8, integration_lines),
        _check("ports are assigned as terminal/reference ports", all("terminal" in str(mode).lower() for mode in modes.values()) and len(modes) == 8, modes),
    ]


def _export_manifest_checks(export_manifest: dict[str, Any], path: Path, hfss_s8p: Path) -> list[Check]:
    selected = Path(str(export_manifest.get("selected_s8p") or "")).expanduser()
    selected_resolved = selected.resolve() if str(selected) else selected
    return [
        _check("export manifest exists", path.is_file(), str(path)),
        _check("export manifest status PASS", export_manifest.get("status") == "PASS", export_manifest.get("status")),
        _check("export manifest expects 8 ports", int(export_manifest.get("expected_port_count") or 0) == 8, export_manifest.get("expected_port_count")),
        _check("export manifest selected s8p matches input", selected_resolved == hfss_s8p.resolve(), {"selected": str(selected_resolved), "input": str(hfss_s8p.resolve())}),
    ]


def _touchstone_checks(label: str, item: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    option = item.get("option") if isinstance(item.get("option"), dict) else {}
    return [
        _check(f"{label} touchstone exists", bool(item.get("exists")), item.get("path")),
        _check(f"{label} touchstone suffix is .s8p", item.get("suffix") == ".s8p", item.get("suffix")),
        _check(f"{label} touchstone has 8 ports", item.get("port_count") == 8, item.get("port_count")),
        _check(f"{label} touchstone is S-parameter data", str(option.get("parameter_kind", "")).lower() == "s", option),
        _check(f"{label} touchstone reference is 50 ohm", _float_close(option.get("reference_ohm"), args.expected_reference_ohm, 1e-9), option),
        _check(f"{label} touchstone frequency grid is complete", _inspected_grid_matches(item, args), item),
        _check(f"{label} touchstone numeric blocks are complete", int(item.get("trailing_numeric_token_count") or 0) == 0, item.get("trailing_numeric_token_count")),
    ]


def _comparison_checks(metrics: dict[str, dict[str, Any]], summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks = [
        _check("comparison marker table contains core metrics", all(metric in metrics for metric in CORE_METRICS), sorted(metrics)),
    ]
    for metric in CORE_METRICS:
        row = metrics.get(metric, {})
        err = row.get("percent_error")
        checks.append(
            _check(
                f"{metric} error is within threshold",
                err is not None and math.isfinite(float(err)) and float(err) <= float(args.max_error_pct),
                row,
            )
        )
    if summary:
        checks.append(_check("comparison summary status is present", bool(summary.get("status") or summary.get("decision")), summary.get("status") or summary.get("decision")))
    return checks


def _asset_checks(plot_assets: dict[str, Any], model_assets: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    if args.allow_missing_assets:
        return []
    required_plots = ("emx_curves", "hfss_curves", "overlay_curves")
    required_models = ("hfss_top_annotated",)
    return [
        _check("report plot assets exist", all(plot_assets.get(name, {}).get("exists") for name in required_plots), plot_assets),
        _check("HFSS model-view asset exists", all(model_assets.get(name, {}).get("exists") for name in required_models), model_assets),
    ]


def _grid_matches(grid: dict[str, Any], args: argparse.Namespace) -> bool:
    return (
        _float_close(grid.get("start_ghz"), args.expected_start_ghz, 1e-9)
        and _float_close(grid.get("stop_ghz"), args.expected_stop_ghz, 1e-9)
        and _float_close(grid.get("step_ghz"), args.expected_step_ghz, 1e-9)
        and int(grid.get("points") or 0) == int(args.expected_points)
    )


def _inspected_grid_matches(item: dict[str, Any], args: argparse.Namespace) -> bool:
    return (
        int(item.get("frequency_point_count") or 0) == int(args.expected_points)
        and _float_close(item.get("start_ghz"), args.expected_start_ghz, 1e-6)
        and _float_close(item.get("stop_ghz"), args.expected_stop_ghz, 1e-6)
        and _float_close(item.get("step_ghz"), args.expected_step_ghz, 1e-6)
    )


def _float_close(actual: Any, expected: float, tol: float) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= tol
    except (TypeError, ValueError):
        return False


def _path_exists(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and Path(text).expanduser().is_file()


def _inspect_touchstone(path: Path, *, expected_points: int) -> dict[str, Any]:
    result = {
        "path": str(path),
        "exists": path.is_file(),
        "suffix": path.suffix.lower(),
        "port_count": None,
        "option": {},
        "frequency_point_count": 0,
        "start_ghz": None,
        "stop_ghz": None,
        "step_ghz": None,
        "trailing_numeric_token_count": None,
    }
    match = re.search(r"\.s(\d+)p$", path.name.lower())
    result["port_count"] = int(match.group(1)) if match else None
    if not path.is_file():
        return result
    option = _touchstone_option(path)
    values: list[float] = []
    for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
        line = _strip_comment(raw)
        if not line or line.startswith("#") or line.startswith("["):
            continue
        for token in line.replace("D", "E").replace("d", "e").split():
            try:
                values.append(float(token))
            except ValueError:
                pass
    port_count = int(result["port_count"] or 8)
    block_len = 1 + 2 * port_count * port_count
    scale = _frequency_scale(option.get("frequency_unit", "ghz"))
    freqs = []
    idx = 0
    while idx + block_len <= len(values):
        freqs.append(values[idx] * scale)
        idx += block_len
    result["option"] = option
    result["frequency_point_count"] = len(freqs)
    result["trailing_numeric_token_count"] = len(values) - idx
    if freqs:
        result["start_ghz"] = freqs[0] / 1.0e9
        result["stop_ghz"] = freqs[-1] / 1.0e9
        result["step_ghz"] = None if len(freqs) < 2 else (freqs[1] - freqs[0]) / 1.0e9
    return result


def _touchstone_option(path: Path) -> dict[str, Any]:
    option = {"frequency_unit": "ghz", "parameter_kind": "s", "format": "ma", "reference_ohm": 50.0}
    for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
        line = _strip_comment(raw)
        if not line.startswith("#"):
            continue
        tokens = line[1:].strip().lower().split()
        if tokens:
            option["frequency_unit"] = tokens[0]
        if len(tokens) >= 2:
            option["parameter_kind"] = tokens[1]
        if len(tokens) >= 3:
            option["format"] = tokens[2]
        if "r" in tokens:
            idx = tokens.index("r")
            if idx + 1 < len(tokens):
                try:
                    option["reference_ohm"] = float(tokens[idx + 1])
                except ValueError:
                    option["reference_ohm"] = None
        return option
    return option


def _strip_comment(line: str) -> str:
    return line.split("!", 1)[0].strip()


def _frequency_scale(unit: Any) -> float:
    return {"hz": 1.0, "khz": 1.0e3, "mhz": 1.0e6, "ghz": 1.0e9}.get(str(unit).lower(), 1.0e9)


def _marker_metrics(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        metric = str(row.get("metric") or "").strip().lower()
        if not metric:
            continue
        result[metric] = {
            "emx": _float_or_none(row.get("emx")),
            "hfss": _float_or_none(row.get("hfss_ads") or row.get("hfss")),
            "abs_error": _float_or_none(row.get("abs_error")),
            "percent_error": _float_or_none(row.get("percent_error")),
            "metric_status": row.get("metric_status") or row.get("status") or "",
        }
    return result


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plot_assets(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    candidates = {
        "emx_curves": ("emx_ads_style_metrics_common_5_60GHz.png", "emx_ads_style_metrics_5_60GHz.png"),
        "hfss_curves": ("hfss_ads_style_metrics_common_5_60GHz.png", "hfss_ads_style_metrics_5_60GHz.png"),
        "overlay_curves": ("emx_vs_hfss_ads_style_overlay_common_5_60GHz.png", "emx_vs_hfss_ads_style_overlay_5_60GHz.png"),
    }
    return {name: _first_asset(path, names) for name, names in candidates.items()}


def _model_assets(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return {
        "hfss_top_annotated": _first_asset(path, ("hfss_payload_geometry_top_annotated.png",)),
        "hfss_isometric": _first_asset(path, ("hfss_payload_geometry_isometric.png",)),
    }


def _first_asset(root: Path, names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return {"path": str(candidate), "exists": True, "bytes": candidate.stat().st_size}
    return {"path": str(root / names[0]), "exists": False, "bytes": 0}


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    hfss = payload.get("hfss") if isinstance(payload.get("hfss"), dict) else {}
    profile = hfss.get("calibration_profile") if isinstance(hfss.get("calibration_profile"), dict) else {}
    return {
        "sample_id": payload.get("sample_id"),
        "solution_type": hfss.get("solution_type"),
        "setup_name": hfss.get("setup_name"),
        "sweep_name": hfss.get("sweep_name"),
        "calibration_profile": profile.get("name"),
        "frequency_grid": payload.get("frequency_grid"),
    }


def _port_manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    ports = manifest.get("ports") if isinstance(manifest.get("ports"), list) else []
    return {
        "port_count": manifest.get("port_count"),
        "actual_port_order": manifest.get("actual_port_order"),
        "port_reference_mode": manifest.get("port_reference_mode"),
        "power_line_port_reference_mode": manifest.get("power_line_port_reference_mode"),
        "m5_shield_boundary": manifest.get("m5_shield_boundary"),
        "assignment_modes": {port.get("port_name"): port.get("assignment_mode") for port in ports if isinstance(port, dict)},
        "reference_counts": {port.get("port_name"): len(port.get("reference_conductors") or []) for port in ports if isinstance(port, dict)},
    }


def _export_manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": manifest.get("status"),
        "selected_s8p": manifest.get("selected_s8p"),
        "candidate_count": manifest.get("candidate_count"),
        "expected_frequency_grid": manifest.get("expected_frequency_grid"),
    }


def _sample_from_path(path: Path) -> str:
    return path.stem.split("_")[0]


def _render_report(summary: dict[str, Any], checks: list[Check]) -> str:
    metrics = summary["core_metrics"]
    lines = [
        "# HFSS S8P Delivery Audit",
        "",
        f"- Generated UTC: `{summary['generated_utc']}`",
        f"- Sample: `{summary.get('sample_id', '')}`",
        f"- Decision: `{summary['decision']}`",
        f"- Threshold: `{summary['threshold_percent']:.3g}%`",
        f"- Max core error: `{summary.get('max_core_error_percent')}`",
        "",
        "## Core 15 GHz Metrics",
        "",
        "| Metric | EMX | HFSS | Abs. error | Error % |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric in CORE_METRICS:
        row = metrics.get(metric, {})
        lines.append(
            "| {metric} | {emx} | {hfss} | {abs_error} | {percent_error} |".format(
                metric=metric,
                emx=_fmt(row.get("emx")),
                hfss=_fmt(row.get("hfss")),
                abs_error=_fmt(row.get("abs_error")),
                percent_error=_fmt(row.get("percent_error")),
            )
        )
    lines.extend(
        [
            "",
            "## Workflow Evidence",
            "",
            f"- Payload: `{summary['paths'].get('payload', '')}`",
            f"- Port manifest: `{summary['paths'].get('port_manifest', '')}`",
            f"- Export manifest: `{summary['paths'].get('export_manifest', '')}`",
            f"- EMX S8P: `{summary['paths'].get('emx_s8p', '')}`",
            f"- HFSS S8P: `{summary['paths'].get('hfss_s8p', '')}`",
            "",
            "## Checks",
            "",
            "| Status | Check | Detail |",
            "|---|---|---|",
        ]
    )
    for check in checks:
        detail = check.detail.replace("|", "\\|")
        if len(detail) > 280:
            detail = detail[:277] + "..."
        lines.append(f"| {check.status} | {check.name} | {detail} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This audit proves traceability and file-format correctness only when the workflow checks pass. "
            "It proves physical validation only when the core metric errors are also within the configured threshold.",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return str(number)
    return f"{number:.6g}"


if __name__ == "__main__":
    raise SystemExit(main())
