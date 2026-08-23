#!/usr/bin/env python3
"""Audit selected validation samples for the 8-port power-line layout contract.

This script connects the random physical-feature validation sample list back to
raw layout evidence. It does not run EMX, HFSS, ADS, or infer missing geometry.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    samples_csv = Path(args.samples_csv).expanduser().resolve()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve() if args.dataset_dir else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(samples_csv)
    if args.max_samples is not None:
        rows = rows[: max(0, int(args.max_samples))]

    sample_results = [_audit_row(row, index, dataset_dir, args) for index, row in enumerate(rows, start=1)]
    checks = [
        _check("samples_csv_exists", samples_csv.is_file(), str(samples_csv)),
        _check("selected_rows_present", bool(rows), f"rows={len(rows)}"),
    ]
    for result in sample_results:
        checks.extend(result["checks"])

    fail_count = sum(1 for result in sample_results if result["overall_status"] == "FAIL")
    overall_status = "FAIL" if any(item["status"] == "FAIL" for item in checks) or fail_count else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "SELECTED_SAMPLE_LAYOUTS_READY_FOR_HFSS_ADS_VALIDATION"
        if overall_status == "PASS"
        else "DO_NOT_USE_SELECTED_SAMPLE_UNTIL_LAYOUT_AUDIT_PASSES",
        "samples_csv": str(samples_csv),
        "dataset_dir": None if dataset_dir is None else str(dataset_dir),
        "out_dir": str(out_dir),
        "selected_count": len(rows),
        "pass_count": sum(1 for result in sample_results if result["overall_status"] == "PASS"),
        "fail_count": fail_count,
        "expected_port_names": _expected_port_names(args),
        "expected_power_line_bridge_width_um": (
            None if args.expected_power_line_bridge_width_um is None else float(args.expected_power_line_bridge_width_um)
        ),
        "expected_power_line_vertical_length_diameter_ratio": float(
            args.expected_power_line_vertical_length_diameter_ratio
        ),
        "sample_results": sample_results,
        "checks": checks,
        "limitations": [
            "This audit proves selected layout metadata obeys the 8-port power-line contract.",
            "It does not prove EMX/HFSS S-parameter agreement or ADS physical-feature curve correctness.",
        ],
    }

    summary_path = out_dir / "selected_power_line_8port_layout_audit_summary.json"
    report_path = out_dir / "selected_power_line_8port_layout_audit_report.md"
    checks_path = out_dir / "selected_power_line_8port_layout_audit_checks.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(checks_path, checks)

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"checks_csv={checks_path}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-csv", required=True, help="physical_feature_validation_samples.csv")
    parser.add_argument("--dataset-dir", help="Dataset run directory used to resolve relative sample paths")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-port-names", default="P001,P002,P003,P004,P005,P006,P007,P008")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument(
        "--expected-power-line-bridge-width-um",
        type=float,
        default=None,
        help="Optional legacy fixed bridge width check. Omit for shared variable line_width_um runs.",
    )
    parser.add_argument("--expected-primary-power-line-layer", type=int, default=74)
    parser.add_argument("--expected-primary-power-line-datatype", type=int, default=0)
    parser.add_argument("--expected-secondary-power-line-layer", type=int, default=39)
    parser.add_argument("--expected-secondary-power-line-datatype", type=int, default=60)
    parser.add_argument("--expected-power-line-vertical-length-diameter-ratio", type=float, default=1.5)
    parser.add_argument("--min-power-line-other-coil-clearance-um", type=float, default=1.0e-6)
    parser.add_argument(
        "--expected-power-line-center-tap-topology",
        default="primary_right_secondary_left",
        choices=("primary_right_secondary_left", "primary_left_secondary_right", "any"),
    )
    parser.add_argument("--power-line-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument("--internal-angle-deg", type=float, default=135.0)
    parser.add_argument("--terminal-angle-deg", type=float, default=90.0)
    parser.add_argument("--angle-tolerance-deg", type=float, default=1.0e-3)
    parser.add_argument(
        "--require-target-summary-geometry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require each selected sample summary.json to contain passing winding angle evidence.",
    )
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _audit_row(row: dict[str, str], index: int, dataset_dir: Path | None, args: argparse.Namespace) -> dict[str, Any]:
    audit = _load_geometry_audit_module()
    sample_id = row.get("selection_rank") or str(index)
    evaluation = row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{index}"
    source = _resolve_sample_source(row, dataset_dir)
    layout_path = None if source is None else _find_layout_json(source)
    power_line_path = None if source is None else _find_power_line_geometry_json(source)
    summary_path = None if source is None else _find_summary_json(source)
    checks = [
        _sample_check(sample_id, evaluation, "sample source resolved", source is not None, "" if source is None else str(source)),
        _sample_check(
            sample_id,
            evaluation,
            "sample source directory exists",
            source is not None and source.is_dir(),
            "" if source is None else str(source),
        ),
        _sample_check(
            sample_id,
            evaluation,
            "layout json exists",
            layout_path is not None and layout_path.is_file(),
            "" if layout_path is None else str(layout_path),
        ),
        _sample_check(
            sample_id,
            evaluation,
            "power_line_8port geometry json exists",
            power_line_path is not None and power_line_path.is_file(),
            "" if power_line_path is None else str(power_line_path),
        ),
    ]
    if bool(args.require_target_summary_geometry):
        checks.append(
            _sample_check(
                sample_id,
                evaluation,
                "target summary geometry json exists",
                summary_path is not None and summary_path.is_file(),
                "" if summary_path is None else str(summary_path),
            )
        )

    audit_args = argparse.Namespace(
        expected_pin_purpose=int(args.expected_pin_purpose),
        expected_port_names=str(args.expected_port_names),
        expected_power_line_bridge_width_um=(
            None if args.expected_power_line_bridge_width_um is None else float(args.expected_power_line_bridge_width_um)
        ),
        expected_primary_power_line_layer=int(args.expected_primary_power_line_layer),
        expected_primary_power_line_datatype=int(args.expected_primary_power_line_datatype),
        expected_secondary_power_line_layer=int(args.expected_secondary_power_line_layer),
        expected_secondary_power_line_datatype=int(args.expected_secondary_power_line_datatype),
        expected_power_line_vertical_length_diameter_ratio=float(
            args.expected_power_line_vertical_length_diameter_ratio
        ),
        min_power_line_other_coil_clearance_um=float(args.min_power_line_other_coil_clearance_um),
        expected_power_line_center_tap_topology=str(args.expected_power_line_center_tap_topology),
        power_line_tolerance_um=float(args.power_line_tolerance_um),
        internal_angle_deg=float(args.internal_angle_deg),
        terminal_angle_deg=float(args.terminal_angle_deg),
        angle_tolerance_deg=float(args.angle_tolerance_deg),
    )

    if summary_path is not None and summary_path.is_file():
        try:
            summary_json = _read_json(summary_path)
            checks.extend(_with_sample(sample_id, evaluation, audit._audit_target_summary_geometry(summary_json, audit_args)))
        except Exception as exc:  # noqa: BLE001
            checks.append(_sample_check(sample_id, evaluation, "target summary geometry parse/audit", False, f"{type(exc).__name__}: {exc}"))
    if layout_path is not None and layout_path.is_file():
        try:
            layout = _read_json(layout_path)
            checks.extend(_with_sample(sample_id, evaluation, audit._audit_layout(layout, audit_args)))
        except Exception as exc:  # noqa: BLE001
            checks.append(_sample_check(sample_id, evaluation, "layout json parse/audit", False, f"{type(exc).__name__}: {exc}"))
    if power_line_path is not None and power_line_path.is_file():
        try:
            power_line = _read_json(power_line_path)
            checks.extend(_with_sample(sample_id, evaluation, audit._audit_power_line_8port_geometry(power_line, audit_args)))
        except Exception as exc:  # noqa: BLE001
            checks.append(_sample_check(sample_id, evaluation, "power_line_8port geometry parse/audit", False, f"{type(exc).__name__}: {exc}"))
    if layout_path is not None and layout_path.is_file() and power_line_path is not None and power_line_path.is_file():
        try:
            layout = _read_json(layout_path)
            power_line = _read_json(power_line_path)
            checks.extend(_audit_power_line_port_footprints(sample_id, evaluation, layout, power_line, audit_args))
        except Exception as exc:  # noqa: BLE001
            checks.append(_sample_check(sample_id, evaluation, "power_line_8port port footprint sync parse/audit", False, f"{type(exc).__name__}: {exc}"))

    status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    return {
        "selection_rank": row.get("selection_rank", str(index)),
        "row_index": row.get("row_index", ""),
        "evaluation": evaluation,
        "overall_status": status,
        "source": "" if source is None else str(source),
        "summary_json_path": "" if summary_path is None else str(summary_path),
        "layout_json_path": "" if layout_path is None else str(layout_path),
        "power_line_8port_geometry_json_path": "" if power_line_path is None else str(power_line_path),
        "work_dir": row.get("work_dir", ""),
        "touchstone_path": row.get("touchstone_path", ""),
        "checks": checks,
    }


def _load_geometry_audit_module() -> Any:
    script_path = Path(__file__).resolve().with_name("audit_geometry_quality.py")
    spec = importlib.util.spec_from_file_location("audit_geometry_quality_script_for_selected_samples", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load geometry audit module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _audit_power_line_port_footprints(
    sample_id: str,
    evaluation: str,
    layout: dict[str, Any],
    power_line: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    line_width = _to_float(power_line.get("line_width_um"))
    if line_width is None:
        line_width = _to_float(power_line.get("bridge_width_um"))
    tol = float(args.power_line_tolerance_um)
    expected_side = [0.5, line_width] if line_width is not None else None
    expected_vertical = [line_width, 0.5] if line_width is not None else None
    ports = {
        str(port.get("name")): port
        for port in (layout.get("ports") or [])
        if isinstance(port, dict) and port.get("name")
    }
    checks = [
        _sample_check(
            sample_id,
            evaluation,
            "power_line_8port port footprint line width available",
            line_width is not None and line_width > 0.0,
            f"line_width_um={line_width}",
        )
    ]
    if line_width is None or line_width <= 0.0:
        return checks
    side_ports = ("P001", "P004", "P005", "P006")
    vertical_ports = ("P002", "P003", "P007", "P008")
    for port_name in side_ports:
        checks.append(
            _sample_check(
                sample_id,
                evaluation,
                f"power_line_8port {port_name} side-port footprint sync",
                _port_sizes_match(ports.get(port_name), expected_side, tol),
                _port_size_detail(ports.get(port_name), expected_side),
            )
        )
    for port_name in vertical_ports:
        checks.append(
            _sample_check(
                sample_id,
                evaluation,
                f"power_line_8port {port_name} vertical-port footprint sync",
                _port_sizes_match(ports.get(port_name), expected_vertical, tol),
                _port_size_detail(ports.get(port_name), expected_vertical),
            )
        )
    return checks


def _port_sizes_match(port: dict[str, Any] | None, expected: list[float] | None, tol: float) -> bool:
    if port is None or expected is None:
        return False
    for key in ("signal_internal_size_um", "ground_internal_size_um", "internal_size_um"):
        value = port.get(key)
        if not _size_matches(value, expected, tol):
            return False
    return True


def _size_matches(value: Any, expected: list[float], tol: float) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    left = _to_float(value[0])
    right = _to_float(value[1])
    return (
        left is not None
        and right is not None
        and abs(left - float(expected[0])) <= tol
        and abs(right - float(expected[1])) <= tol
    )


def _port_size_detail(port: dict[str, Any] | None, expected: list[float] | None) -> str:
    if port is None:
        return "port missing"
    return (
        f"expected={expected}, signal={port.get('signal_internal_size_um')}, "
        f"ground={port.get('ground_internal_size_um')}, internal={port.get('internal_size_um')}"
    )


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_sample_source(row: dict[str, str], dataset_dir: Path | None) -> Path | None:
    work_dir = _resolve_optional_path(row.get("work_dir"), dataset_dir)
    if work_dir is not None:
        if (work_dir / "layout").is_dir():
            return (work_dir / "layout").resolve()
        return work_dir.resolve()

    touchstone = _resolve_optional_path(row.get("touchstone_path"), dataset_dir)
    if touchstone is not None:
        eval_dir = _evaluation_dir_from_touchstone(touchstone)
        if eval_dir is not None:
            return (eval_dir / "layout").resolve() if (eval_dir / "layout").is_dir() else eval_dir.resolve()

    evaluation = (row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or "").strip()
    if evaluation and dataset_dir is not None:
        eval_dir = dataset_dir / "evaluations" / evaluation
        return (eval_dir / "layout").resolve() if (eval_dir / "layout").is_dir() else eval_dir.resolve()
    return None


def _resolve_optional_path(raw: str | None, dataset_dir: Path | None) -> Path | None:
    text = (raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path.resolve() if path.is_absolute() else None if dataset_dir is None else (dataset_dir / path).resolve()


def _evaluation_dir_from_touchstone(path: Path) -> Path | None:
    if path.parent.name == "emx":
        return path.parent.parent
    parts = path.parts
    if "evaluations" in parts:
        idx = parts.index("evaluations")
        if idx + 1 < len(parts):
            return Path(*parts[: idx + 2])
    return None


def _find_layout_json(source: Path) -> Path | None:
    candidates = [
        source / "transformer_layout.layout.json",
        source / "layout" / "transformer_layout.layout.json",
        source / "layout.json",
    ]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    matches = sorted(source.glob("layout/*.layout.json")) + sorted(source.glob("*.layout.json"))
    return matches[0].resolve() if matches else None


def _find_power_line_geometry_json(source: Path) -> Path | None:
    candidates = [
        source / "power_line_8port_geometry.json",
        source / "layout" / "power_line_8port_geometry.json",
    ]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def _find_summary_json(source: Path) -> Path | None:
    candidates = [
        source / "summary.json",
        source / "layout" / "summary.json",
    ]
    if source.name == "layout":
        candidates.append(source.parent / "summary.json")
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} top-level JSON is {type(data).__name__}")
    return data


def _to_float(value: Any) -> float | None:
    try:
        item = float(value)
    except (TypeError, ValueError):
        return None
    return item if item == item else None


def _with_sample(sample_id: str, evaluation: str, checks: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "sample": str(sample_id),
            "evaluation": str(evaluation),
            "status": str(check.status),
            "name": str(check.name),
            "detail": str(check.detail),
        }
        for check in checks
    ]


def _sample_check(sample_id: str, evaluation: str, name: str, passed: bool, detail: str) -> dict[str, str]:
    return {
        "sample": str(sample_id),
        "evaluation": str(evaluation),
        "status": "PASS" if passed else "FAIL",
        "name": name,
        "detail": detail,
    }


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"sample": "", "evaluation": "", "status": "PASS" if passed else "FAIL", "name": name, "detail": detail}


def _write_checks_csv(path: Path, checks: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "evaluation", "status", "name", "detail"])
        writer.writeheader()
        writer.writerows(checks)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Selected Power-Line 8-Port Layout Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Samples CSV: `{summary['samples_csv']}`",
        f"- Dataset dir: `{summary['dataset_dir']}`",
        f"- Selected/pass/fail: {summary['selected_count']} / {summary['pass_count']} / {summary['fail_count']}",
        "",
        "## Samples",
        "",
        "| Rank | Evaluation | Status | Summary JSON | Layout JSON | Power-line JSON |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in summary["sample_results"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(str(result["selection_rank"])),
                    _cell(str(result["evaluation"])),
                    _cell(str(result["overall_status"])),
                    f"`{_cell(str(result['summary_json_path']))}`",
                    f"`{_cell(str(result['layout_json_path']))}`",
                    f"`{_cell(str(result['power_line_8port_geometry_json_path']))}`",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Checks", "", "| Status | Sample | Evaluation | Check | Detail |", "| --- | --- | --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(
            f"| {_cell(check['status'])} | {_cell(str(check.get('sample', '')))} | {_cell(str(check.get('evaluation', '')))} | "
            f"{_cell(check['name'])} | {_cell(str(check['detail']))} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _expected_port_names(args: argparse.Namespace) -> list[str]:
    return [item.strip() for item in str(args.expected_port_names).split(",") if item.strip()]


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
