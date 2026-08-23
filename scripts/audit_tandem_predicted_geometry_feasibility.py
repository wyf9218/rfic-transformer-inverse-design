#!/usr/bin/env python3
"""Audit whether tandem-predicted geometries satisfy the real project contract.

The tandem network constrains each output dimension independently. That does
not prove that the combined vector is a buildable transformer. This audit
rebuilds every saved prediction with the production run config and applies:

1. configured search-space bounds;
2. coupled ``TransformerSpec.validate()`` topology constraints; and
3. the project TSMC65 synchronized top-metal rule gate.

This is an analytical pre-layout gate. It does not replace GDS construction,
Cadence/EMX, foundry sign-off DRC, HFSS, or measurement.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.api import (  # noqa: E402
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.layout.drc_rules import (  # noqa: E402
    audit_tsmc65_top_metal_geometry,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    predictions_csv = Path(args.predictions_csv).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(predictions_csv)
    checks: dict[str, bool] = {
        "predictions_csv_exists": predictions_csv.is_file(),
        "config_exists": config_path.is_file(),
        "prediction_rows_meet_minimum": len(rows) >= int(args.min_rows),
    }
    config_error = ""
    run_config = None
    adapter = None
    if config_path.is_file():
        try:
            run_config = load_run_config(config_path)
            adapter = TransformerOptimizationAdapter(run_config.bounds)
        except Exception as exc:  # noqa: BLE001 - exact config failure is evidence.
            config_error = f"{type(exc).__name__}: {exc}"
    checks["config_loads"] = run_config is not None and adapter is not None
    checks["power_line_8port_enabled"] = bool(
        run_config is not None and run_config.emx.power_line_8port.enabled
    )

    records: list[dict[str, Any]] = []
    if adapter is not None and run_config is not None:
        records = _audit_rows(rows, adapter, run_config, str(args.predicted_prefix))

    missing_count = sum(bool(item["missing_fields"]) for item in records)
    bounds_failure_count = sum(int(item["bounds_error_count"] > 0) for item in records)
    topology_failure_count = sum(int(item["topology_error_count"] > 0) for item in records)
    drc_failure_count = sum(int(item["drc_error_count"] > 0) for item in records)
    valid_count = sum(item["status"] == "PASS" for item in records)
    valid_fraction = valid_count / len(records) if records else 0.0
    checks.update(
        {
            "all_rows_rebuilt": bool(records) and not missing_count and len(records) == len(rows),
            "all_predictions_inside_configured_bounds": bool(records) and bounds_failure_count == 0,
            "all_predictions_satisfy_coupled_topology": bool(records) and topology_failure_count == 0,
            "all_predictions_satisfy_tsmc65_top_metal_gate": bool(records) and drc_failure_count == 0,
            "all_predictions_analytically_feasible": bool(records) and valid_count == len(records),
        }
    )
    status = "PASS" if all(checks.values()) else "FAIL"

    audit_csv = out_dir / "tandem_predicted_geometry_feasibility_rows.csv"
    summary_path = out_dir / "tandem_predicted_geometry_feasibility_summary.json"
    report_path = out_dir / "tandem_predicted_geometry_feasibility_report.md"
    _write_csv(audit_csv, records)

    failure_reasons = Counter()
    for record in records:
        for category in str(record.get("failure_categories") or "").split(";"):
            if category:
                failure_reasons[category] += 1
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "ALLOW_PREDICTIONS_TO_PROCEED_TO_GDS_AND_REAL_EM_VALIDATION"
            if status == "PASS"
            else "DO_NOT_TREAT_TANDEM_OUTPUTS_AS_BUILDABLE"
        ),
        "predictions_csv": str(predictions_csv),
        "config": str(config_path),
        "config_error": config_error,
        "prediction_count": len(rows),
        "audited_count": len(records),
        "valid_count": valid_count,
        "valid_fraction": valid_fraction,
        "missing_field_count": missing_count,
        "bounds_failure_count": bounds_failure_count,
        "topology_failure_count": topology_failure_count,
        "tsmc65_top_metal_failure_count": drc_failure_count,
        "failure_category_counts": dict(sorted(failure_reasons.items())),
        "checks": checks,
        "field_order": list(adapter.field_order()) if adapter is not None else [],
        "predicted_prefix": str(args.predicted_prefix),
        "audit_csv": str(audit_csv),
        "report": str(report_path),
        "scientific_boundary": (
            "PASS proves only analytical rebuildability under the production configuration, coupled topology rules, "
            "and the implemented TSMC65 top-metal rule subset. It is not GDS proof, foundry sign-off DRC, an EMX "
            "label, HFSS correlation, ADS validation, or measurement."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"valid_fraction={valid_fraction:.9f}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions-csv", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--predicted-prefix", default="predicted_geometry__")
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.min_rows) < 1:
        parser.error("--min-rows must be positive")
    if not str(args.predicted_prefix):
        parser.error("--predicted-prefix must be nonempty")
    return args


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _audit_rows(rows: list[dict[str, str]], adapter: Any, run_config: Any, prefix: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    field_order = list(adapter.field_order())
    sync_line_width = bool(run_config.emx.power_line_8port.enabled)
    for row_index, row in enumerate(rows):
        values: list[float] = []
        missing: list[str] = []
        shared_width = _value(row, prefix, "line_width_um")
        for field in field_order:
            value = None
            if sync_line_width and field in {"primary_width_um", "secondary_width_um"}:
                value = shared_width
            if value is None:
                value = _value(row, prefix, field)
            if value is None:
                missing.append(field)
            else:
                values.append(value)

        bounds_errors: list[str] = []
        topology_errors: list[str] = []
        drc_errors: list[str] = []
        build_error = ""
        if not missing:
            try:
                geometry = adapter.from_vector(values)
                if sync_line_width:
                    if shared_width is None:
                        raise ValueError("power_line_8port prediction is missing line_width_um")
                    geometry = geometry.with_shared_line_width(shared_width)
                bounds_errors = list(adapter.search_space.validate(geometry))
                topology_errors = list(geometry.validate())
                drc = audit_tsmc65_top_metal_geometry(geometry, run_config)
                drc_errors = [str(item) for item in drc.get("errors") or []]
            except Exception as exc:  # noqa: BLE001 - row-level provenance is required.
                build_error = f"{type(exc).__name__}: {exc}"

        categories = []
        if missing:
            categories.append("missing_fields")
        if build_error:
            categories.append("geometry_build")
        if bounds_errors:
            categories.append("configured_bounds")
        if topology_errors:
            categories.append("coupled_topology")
        if drc_errors:
            categories.append("tsmc65_top_metal")
        status = "PASS" if not categories else "FAIL"
        all_errors = [build_error] if build_error else []
        all_errors.extend(bounds_errors)
        all_errors.extend(topology_errors)
        all_errors.extend(drc_errors)
        records.append(
            {
                "row_index": row_index,
                "source_row_index": row.get("source_row_index", ""),
                "matrix_index": row.get("matrix_index", ""),
                "status": status,
                "failure_categories": ";".join(categories),
                "missing_fields": ";".join(missing),
                "build_error": build_error,
                "bounds_error_count": len(bounds_errors),
                "topology_error_count": len(topology_errors),
                "drc_error_count": len(drc_errors),
                "errors": " | ".join(all_errors),
            }
        )
    return records


def _value(row: dict[str, str], prefix: str, field: str) -> float | None:
    for key in (f"{prefix}{field}", field):
        raw = row.get(key)
        try:
            value = float(raw) if raw not in {None, ""} else None
        except (TypeError, ValueError):
            value = None
        if value is not None and math.isfinite(value):
            return value
    return None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    checks = summary.get("checks") or {}
    lines = [
        "# Tandem predicted-geometry feasibility audit",
        "",
        f"- Overall status: `{summary.get('overall_status')}`",
        f"- Predictions audited: `{summary.get('audited_count')}`",
        f"- Analytically feasible: `{summary.get('valid_count')}`",
        f"- Valid fraction: `{summary.get('valid_fraction')}`",
        f"- Bounds failures: `{summary.get('bounds_failure_count')}`",
        f"- Coupled-topology failures: `{summary.get('topology_failure_count')}`",
        f"- TSMC65 top-metal failures: `{summary.get('tsmc65_top_metal_failure_count')}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- `{name}`: `{'PASS' if value else 'FAIL'}`" for name, value in checks.items())
    lines.extend(["", "## Boundary", "", str(summary.get("scientific_boundary") or ""), ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
