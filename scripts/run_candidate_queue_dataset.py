#!/usr/bin/env python3
"""Run a fixed candidate-queue dataset through layout/Cadence/EMX evaluation.

This is the bridge after a sparse-bin response planner and candidate selector
have produced concrete geometries. Predicted response values are preserved as
provenance only; simulator labels in dataset_rows.csv come only from
TransformerEmxEvaluator results.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rfic_transformer_inverse_design.api import (
    TransformerEmxEvaluator,
    TransformerOptimizationAdapter,
    TransformerRunConfig,
    TransformerTargetSpec,
    load_run_config,
)
from rfic_transformer_inverse_design.dataset import (
    GROUND_CLEARANCE_AUDIT_FILENAME,
    SampledGeometries,
    result_to_dataset_row,
    target_frequency_report,
    write_dataset_csv,
    write_dataset_manifest,
    write_ground_clearance_audit,
)
from rfic_transformer_inverse_design.execution.serialization import _json_default
from rfic_transformer_inverse_design.execution.zeus_cadence import (
    collect_cadence_pin_labels,
    load_emx_layout_manifest,
)
from rfic_transformer_inverse_design.layout.drc_rules import audit_tsmc65_top_metal_geometry
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone


GEOMETRY_PREFIX_CANDIDATES = ("candidate__geom__", "geom__", "candidate__")
QUEUE_METADATA_COLUMNS = (
    "campaign_id",
    "campaign_contract_fingerprint",
    "campaign_phase",
    "acquisition_source",
    "geometry_id",
    "geometry_sha256",
    "selection_rank",
    "candidate_index",
    "candidate_id",
    "target_rank",
    "target_real_bin",
    "target_imag_bin",
    "target_real_ohm",
    "target_imag_ohm",
    "target_recommended_new_samples",
    "pred_real_ohm",
    "pred_imag_ohm",
    "pred_zin_center_real_ohm",
    "pred_zin_center_imag_ohm",
    "pred_zin_center_abs_ohm",
    "inside_target_bin",
    "selection_score",
    "drc_status",
    "drc_rule_source",
    "drc_shared_line_min_width_um",
    "drc_shared_line_max_width_um",
    "source_candidate_id",
    "geometry_fingerprint_sha256",
    "geometry_fingerprint_schema",
    "geometry_fingerprint_quantization_um",
    "target_bin_key",
    "inside_pairwise_target_bin",
    "selection_source",
    "pairwise_priority_score",
    "pairwise_deficit_score",
    "marginal_deficit_score",
    "four_d_novelty_score",
    "prediction_value_source",
    "prediction_calibration_sha256",
    "pred_neighbor_mean_distance",
    "pred_k_neighbors",
    "candidate_generation_mode",
    "pred_lp_nh_center",
    "pred_ls_nh_center",
    "pred_q_center",
    "pred_k_abs_center",
    "raw_pred_lp_nh_center",
    "raw_pred_ls_nh_center",
    "raw_pred_q_center",
    "raw_pred_k_abs_center",
    "calibrated_pred_lp_nh_center",
    "calibrated_pred_ls_nh_center",
    "calibrated_pred_q_center",
    "calibrated_pred_k_abs_center",
    "pred_uncertainty_lp_nh_center",
    "pred_uncertainty_ls_nh_center",
    "pred_uncertainty_q_center",
    "pred_uncertainty_k_abs_center",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cadence_streamout_only = bool(args.cadence_streamout_only)
    run_emx = not bool(args.create_only or cadence_streamout_only)
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_config = _apply_overrides(load_run_config(args.config), args)
    adapter = TransformerOptimizationAdapter(run_config.bounds)

    raw_rows = _read_csv(candidate_csv)
    selected_rows, input_checks = _select_input_rows(raw_rows, args)
    geometries, queue_metadata, geometry_checks = _geometry_from_rows(selected_rows, adapter, run_config)
    frequency_checks = _frequency_checks(run_config, args)
    checks = [
        _check("candidate_csv_exists", candidate_csv.is_file(), str(candidate_csv)),
        _check("candidate_rows_present", bool(raw_rows), f"rows={len(raw_rows)}"),
        *input_checks,
        *geometry_checks,
        *frequency_checks,
        _check("candidate_geometries_present", bool(geometries), f"geometries={len(geometries)}"),
    ]

    results = []
    rows: list[dict[str, object]] = []
    manifest: dict[str, Any] = {}
    csv_path = out_dir / "dataset_rows.csv"
    ground_clearance_audit_path = out_dir / GROUND_CLEARANCE_AUDIT_FILENAME
    if all(item["pass"] for item in checks):
        evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=out_dir)
        batch_size = max(1, int(args.batch_size))
        for start in range(0, len(geometries), batch_size):
            batch = geometries[start : start + batch_size]
            results.extend(
                evaluator.evaluate_geometry_batch(
                    batch,
                    run_emx=run_emx,
                    cadence_streamout_only=cadence_streamout_only,
                )
            )
        rows = [
            _merge_queue_metadata(result_to_dataset_row(result, z_load_ohm=float(args.z_load_ohm)), queue_metadata[idx])
            for idx, result in enumerate(results)
        ]
        samples = _sampled_geometries(geometries, adapter, run_config)
        ground_clearance_audit = write_ground_clearance_audit(results, ground_clearance_audit_path)
        manifest = write_dataset_manifest(
            path=out_dir / "dataset_manifest.json",
            run_config=run_config,
            samples=samples,
            results=results,
            sampler="candidate_queue",
            seed=0,
            batch_size=batch_size,
            z_load_ohm=float(args.z_load_ohm),
            csv_path=csv_path,
            ground_clearance_audit_path=ground_clearance_audit_path,
            ground_clearance_audit=ground_clearance_audit,
            uniformity_bins=int(args.uniformity_bins),
        )
        write_dataset_csv(rows, csv_path)
        touchstone_contract = _touchstone_output_contract(
            out_dir=out_dir,
            rows=rows,
            create_only=bool(args.create_only),
            cadence_streamout_only=cadence_streamout_only,
            expected_extension=str(args.expected_touchstone_extension),
            expected_ports=int(args.expected_ports),
            expected_frequency_start_ghz=args.expected_frequency_start_ghz,
            expected_frequency_stop_ghz=args.expected_frequency_stop_ghz,
            expected_frequency_step_ghz=args.expected_frequency_step_ghz,
            expected_frequency_points=args.expected_frequency_points,
            frequency_tolerance_hz=float(args.frequency_tolerance_hz),
            max_touchstone_checks=int(args.max_touchstone_checks),
        )
        checks.extend(touchstone_contract["checks"])
        cadence_streamout_contract = _cadence_streamout_output_contract(
            results=results,
            enabled=cadence_streamout_only,
            expected_ports=int(args.expected_ports),
        )
        checks.extend(cadence_streamout_contract["checks"])
    else:
        touchstone_contract = {
            "summary": {
                "checked": False,
                "reason": "input_or_geometry_checks_failed_before_evaluation",
                "expected_extension": _normalise_suffix(str(args.expected_touchstone_extension)),
                "expected_ports": int(args.expected_ports),
                "sampled_count": 0,
            },
            "checks": [],
        }
        cadence_streamout_contract = {
            "summary": {
                "checked": False,
                "reason": "input_or_geometry_checks_failed_before_evaluation",
                "expected_ports": int(args.expected_ports),
                "result_count": 0,
            },
            "checks": [],
        }

    fail_count = sum(1 for result in results if result.error is not None)
    if bool(args.fail_on_error) and fail_count:
        checks.append(_check("evaluations_have_no_errors", False, f"fail_count={fail_count}"))
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "CANDIDATE_QUEUE_DATASET_READY" if status == "PASS" else "DO_NOT_USE_CANDIDATE_QUEUE_DATASET",
        "candidate_csv": str(candidate_csv),
        "candidate_source": _file_source(candidate_csv),
        "out_dir": str(out_dir),
        "dataset_rows_csv": str(csv_path),
        "dataset_manifest": str(out_dir / "dataset_manifest.json"),
        "ground_clearance_audit": str(ground_clearance_audit_path),
        "input_row_count": len(raw_rows),
        "selected_row_count": len(selected_rows),
        "geometry_count": len(geometries),
        "result_count": len(results),
        "ok_count": int(sum(1 for result in results if result.ok())),
        "fail_count": int(fail_count),
        "run_emx": run_emx,
        "create_only": bool(args.create_only),
        "cadence_streamout_only": cadence_streamout_only,
        "port_mode": run_config.emx.port_mode,
        "cadence_pin_purpose": run_config.emx.cadence_pin_purpose,
        "target_frequency": target_frequency_report(run_config),
        "field_order": list(adapter.field_order()),
        "manifest_summary": {
            "requested_count": manifest.get("requested_count"),
            "ok_count": manifest.get("ok_count"),
            "fail_count": manifest.get("fail_count"),
            "zin_coverage": manifest.get("zin_coverage"),
            "sparameter_quality": manifest.get("sparameter_quality"),
            "geometry_quality": manifest.get("geometry_quality"),
        }
        if manifest
        else {},
        "touchstone_output_contract": touchstone_contract["summary"],
        "cadence_streamout_output_contract": cadence_streamout_contract["summary"],
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "Predicted response columns from the candidate queue are provenance only and are not simulator labels.",
            "Raw/calibrated physical-feature predictions are retained only so post-EMX bin-hit calibration can be audited on the same geometry.",
            "When --create-only is used, output is geometry/export evidence only; no EMX S4P or Zin label acceptance is implied.",
            "When --cadence-streamout-only is used, each result is a candidate-bound Cadence-pin GDS; no EMX Touchstone or physical label acceptance is implied.",
            "A final training chunk still needs downstream dataset quality gates, Zin uniformity audit, and random-sample EMX/HFSS validation.",
        ],
    }
    summary_path = out_dir / "candidate_queue_dataset_summary.json"
    report_path = out_dir / "candidate_queue_dataset_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"dataset_rows={csv_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config")
    parser.add_argument("--max-count", type=int)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--z-load-ohm", type=float, default=50.0)
    parser.add_argument("--uniformity-bins", type=int, default=10)
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument("--create-only", action="store_true")
    execution_mode.add_argument(
        "--cadence-streamout-only",
        action="store_true",
        help=(
            "Run export, Cadence strmin, dbCreatePin, and strmout for each "
            "candidate, then stop before EMX."
        ),
    )
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--allow-outside-target-bin", action="store_true")
    parser.add_argument("--force-port-mode")
    parser.add_argument("--force-cadence-pin-purpose", type=int)
    parser.add_argument("--force-wideband-5-50-0p1", action="store_true")
    parser.add_argument("--force-wideband-5-60-0p5", action="store_true")
    parser.add_argument("--force-wideband-5-60-1p0", action="store_true")
    parser.add_argument("--expected-port-mode")
    parser.add_argument("--expected-pin-purpose", type=int)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument(
        "--expected-touchstone-extension",
        default=".s8p",
        help="For non-create-only runs, fail unless successful rows point to this Touchstone extension.",
    )
    parser.add_argument(
        "--expected-ports",
        type=int,
        default=8,
        help="For non-create-only runs, parse sampled Touchstone files and fail unless this port count is found.",
    )
    parser.add_argument(
        "--max-touchstone-checks",
        type=int,
        default=500,
        help="Maximum number of successful Touchstone files to parse in the single-run output gate.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _apply_overrides(run_config: TransformerRunConfig, args: argparse.Namespace) -> TransformerRunConfig:
    cfg = run_config
    if args.force_wideband_5_60_1p0:
        target = replace(
            cfg.target,
            frequency_start_hz=5.0e9,
            frequency_stop_hz=60.0e9,
            frequency_step_hz=1.0e9,
            band_points=56,
        )
        cfg = replace(cfg, target=target)
    elif args.force_wideband_5_60_0p5:
        target = replace(
            cfg.target,
            frequency_start_hz=5.0e9,
            frequency_stop_hz=60.0e9,
            frequency_step_hz=0.5e9,
            band_points=111,
        )
        cfg = replace(cfg, target=target)
    elif args.force_wideband_5_50_0p1:
        target = replace(
            cfg.target,
            frequency_start_hz=5.0e9,
            frequency_stop_hz=50.0e9,
            frequency_step_hz=0.1e9,
            band_points=451,
        )
        cfg = replace(cfg, target=target)
    if args.force_port_mode or args.force_cadence_pin_purpose is not None:
        cfg = replace(
            cfg,
            emx=replace(
                cfg.emx,
                port_mode=args.force_port_mode or cfg.emx.port_mode,
                cadence_pin_purpose=(
                    int(args.force_cadence_pin_purpose)
                    if args.force_cadence_pin_purpose is not None
                    else cfg.emx.cadence_pin_purpose
                ),
            ),
        )
    return cfg


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _select_input_rows(rows: list[dict[str, str]], args: argparse.Namespace) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    selected = list(rows)
    if not args.allow_outside_target_bin and rows and "inside_target_bin" in rows[0]:
        outside = [row for row in rows if not _truthy(row.get("inside_target_bin"))]
        checks.append(_check("all_rows_inside_target_bin", not outside, f"outside={len(outside)}"))
        selected = [row for row in selected if _truthy(row.get("inside_target_bin"))]
    if args.max_count is not None:
        selected = selected[: max(0, int(args.max_count))]
        checks.append(_check("max_count_positive", int(args.max_count) > 0, args.max_count))
    checks.append(_check("selected_candidate_rows_present", bool(selected), f"rows={len(selected)}"))
    return selected, checks


def _geometry_from_rows(
    rows: list[dict[str, str]],
    adapter: TransformerOptimizationAdapter,
    run_config: TransformerRunConfig,
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    geometries = []
    metadata = []
    errors: list[str] = []
    field_order = adapter.field_order()
    sync_line_width = bool(run_config.emx.power_line_8port.enabled)
    for row_index, row in enumerate(rows):
        values: list[float] = []
        missing: list[str] = []
        if sync_line_width:
            width_contract_error = _shared_line_width_contract_error(row)
            if width_contract_error:
                errors.append(f"row {row_index} {width_contract_error}")
                continue
        shared_line_width, shared_line_width_source = _shared_line_width_from_row(row) if sync_line_width else (None, None)
        for field in field_order:
            if sync_line_width and field in {"primary_width_um", "secondary_width_um"} and shared_line_width is not None:
                value = shared_line_width
            else:
                value = _find_geometry_value(row, field)
            if value is None:
                missing.append(field)
            else:
                values.append(value)
        if missing:
            errors.append(f"row {row_index} missing geometry fields: {', '.join(missing)}")
            continue
        try:
            geometry = adapter.from_vector(values)
            if sync_line_width:
                if shared_line_width is None:
                    raise ValueError("power_line_8port requires line_width_um or primary_width_um/secondary_width_um")
                geometry = geometry.with_shared_line_width(shared_line_width)
        except Exception as exc:  # noqa: BLE001 - exact row failure is recorded for provenance.
            errors.append(f"row {row_index} geometry build failed: {type(exc).__name__}: {exc}")
            continue
        bound_errors = adapter.search_space.validate(geometry)
        if bound_errors:
            errors.append(f"row {row_index} outside bounds: {'; '.join(bound_errors)}")
            continue
        if sync_line_width:
            drc_audit = audit_tsmc65_top_metal_geometry(geometry, run_config)
            if not bool(drc_audit["ok"]):
                errors.append(f"row {row_index} violates TSMC65 top-metal DRC gate: {'; '.join(drc_audit['errors'])}")
                continue
        geometries.append(geometry)
        meta = _queue_metadata(row, row_index)
        if sync_line_width and shared_line_width is not None:
            meta["queue__line_width_um"] = shared_line_width
            meta["queue__line_width_sync_source"] = shared_line_width_source
        metadata.append(meta)
    checks = [
        _check("geometry_fields_parse", not errors, errors[:20] if errors else "all parsed"),
        _check("geometry_count_matches_selected_rows", len(geometries) == len(rows), f"geometries={len(geometries)} rows={len(rows)}"),
    ]
    return geometries, metadata, checks


def _shared_line_width_from_row(row: dict[str, str]) -> tuple[float | None, str | None]:
    for field, source in (
        ("line_width_um", "line_width_um"),
        ("primary_width_um", "primary_width_um"),
        ("secondary_width_um", "secondary_width_um"),
    ):
        value = _find_geometry_value(row, field)
        if value is not None:
            return value, source
    return None, None


def _shared_line_width_contract_error(row: dict[str, str]) -> str:
    line_width = _find_geometry_value(row, "line_width_um")
    primary_width = _find_geometry_value(row, "primary_width_um")
    secondary_width = _find_geometry_value(row, "secondary_width_um")
    if line_width is not None:
        return ""
    if primary_width is None or secondary_width is None:
        return ""
    if abs(float(primary_width) - float(secondary_width)) <= 1.0e-9:
        return ""
    return (
        "has no line_width_um but primary_width_um and secondary_width_um differ "
        f"({primary_width:g} vs {secondary_width:g}); power_line_8port would otherwise "
        "silently use primary_width_um as the shared line width"
    )


def _find_geometry_value(row: dict[str, str], field: str) -> float | None:
    candidates = [f"{prefix}{field}" for prefix in GEOMETRY_PREFIX_CANDIDATES] + [field]
    for key in candidates:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _queue_metadata(row: dict[str, str], row_index: int) -> dict[str, Any]:
    meta: dict[str, Any] = {"queue_row_index": row_index}
    for key in QUEUE_METADATA_COLUMNS:
        if key in row:
            meta[f"queue__{key}"] = row[key]
    return meta


def _merge_queue_metadata(row: dict[str, object], metadata: dict[str, Any]) -> dict[str, object]:
    merged = dict(row)
    merged.update(metadata)
    return merged


def _sampled_geometries(
    geometries: list[Any],
    adapter: TransformerOptimizationAdapter,
    run_config: TransformerRunConfig,
) -> SampledGeometries:
    bounds = tuple(tuple(map(float, item)) for item in run_config.bounds.to_scipy_bounds())
    vectors = np.asarray([adapter.to_vector(geometry) for geometry in geometries], dtype=float)
    if len(bounds):
        lower = np.asarray([item[0] for item in bounds], dtype=float)
        upper = np.asarray([item[1] for item in bounds], dtype=float)
        unit = (vectors - lower) / np.maximum(upper - lower, 1.0e-12)
        unit = np.clip(unit, 0.0, 1.0)
    else:
        unit = np.empty((len(geometries), 0), dtype=float)
    return SampledGeometries(
        geometries=tuple(geometries),
        unit_vectors=unit,
        field_order=adapter.field_order(),
        bounds=bounds,
    )


def _frequency_checks(run_config: TransformerRunConfig, args: argparse.Namespace) -> list[dict[str, Any]]:
    report = target_frequency_report(run_config)
    checks: list[dict[str, Any]] = []
    if args.expected_port_mode:
        checks.append(_check("expected_port_mode", run_config.emx.port_mode == args.expected_port_mode, run_config.emx.port_mode))
    if args.expected_pin_purpose is not None:
        checks.append(
            _check(
                "expected_cadence_pin_purpose",
                int(run_config.emx.cadence_pin_purpose) == int(args.expected_pin_purpose),
                run_config.emx.cadence_pin_purpose,
            )
        )
    tol_hz = float(args.frequency_tolerance_hz)
    expected = {
        "expected_frequency_start_ghz": ("start_hz", args.expected_frequency_start_ghz, 1.0e9),
        "expected_frequency_stop_ghz": ("stop_hz", args.expected_frequency_stop_ghz, 1.0e9),
        "expected_frequency_step_ghz": ("step_hz", args.expected_frequency_step_ghz, 1.0e9),
    }
    for name, (key, value, scale) in expected.items():
        if value is None:
            continue
        actual = report.get(key)
        checks.append(_check(name, actual is not None and abs(float(actual) - float(value) * scale) <= tol_hz, actual))
    if args.expected_frequency_points is not None:
        checks.append(_check("expected_frequency_points", report.get("points") == int(args.expected_frequency_points), report.get("points")))
    return checks


def _file_source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _cadence_streamout_output_contract(
    *,
    results: list[Any],
    enabled: bool,
    expected_ports: int,
) -> dict[str, Any]:
    if not enabled:
        return {
            "summary": {
                "checked": False,
                "reason": "cadence_streamout_only_not_requested",
                "expected_ports": int(expected_ports),
                "result_count": len(results),
            },
            "checks": [],
        }

    records: list[dict[str, Any]] = []
    for result in results:
        work_dir = Path(result.work_dir).resolve()
        expected_gds = (work_dir / "streamout" / "transformer_layout_cadpins.gds").resolve()
        roundtrip_summary = work_dir / "summary_cadence_roundtrip.json"
        payload: dict[str, Any] = {}
        payload_error: str | None = None
        try:
            payload = json.loads(roundtrip_summary.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - retain the exact evidence failure.
            payload_error = str(exc)

        layout = result.layout
        layout_gds = None if layout is None else Path(layout.gds_path).resolve()
        manifest_path = None if layout is None else Path(layout.manifest_path).resolve()
        artifact_gds_raw = (payload.get("artifacts") or {}).get("cadence_gds")
        artifact_gds = Path(str(artifact_gds_raw)).resolve() if artifact_gds_raw else None
        recorded_labels = tuple(
            str(value) for value in ((payload.get("cadence") or {}).get("pin_labels") or ())
        )
        manifest_error: str | None = None
        expected_labels: tuple[str, ...] = ()
        manifest_ports = 0
        try:
            if manifest_path is None:
                raise ValueError("result layout manifest path is missing")
            manifest = load_emx_layout_manifest(manifest_path)
            expected_labels = collect_cadence_pin_labels(manifest)
            manifest_ports = len(manifest.ports)
        except Exception as exc:  # noqa: BLE001 - retain the exact evidence failure.
            manifest_error = str(exc)
        touchstones = sorted(work_dir.rglob("*.s?p"))
        records.append(
            {
                "cache_key": str(result.cache_key),
                "result_ok": result.error is None,
                "roundtrip_summary": str(roundtrip_summary),
                "roundtrip_summary_ok": payload_error is None,
                "roundtrip_payload_ok": payload.get("ok") is True,
                "roundtrip_stop_after": payload.get("stop_after"),
                "expected_gds": str(expected_gds),
                "layout_gds": None if layout_gds is None else str(layout_gds),
                "artifact_gds": None if artifact_gds is None else str(artifact_gds),
                "gds_exists_nonzero": expected_gds.is_file()
                and expected_gds.stat().st_size > 0,
                "candidate_bound_gds": layout_gds == expected_gds
                and artifact_gds == expected_gds,
                "manifest_path": None if manifest_path is None else str(manifest_path),
                "manifest_ok": manifest_error is None,
                "manifest_port_count": manifest_ports,
                "pin_labels_match_manifest": bool(expected_labels)
                and recorded_labels == expected_labels,
                "touchstone_path_is_none": result.touchstone_path is None,
                "touchstone_file_count": len(touchstones),
                "error": result.error or payload_error or manifest_error,
            }
        )

    checks = [
        _check(
            "cadence_streamout_results_present",
            bool(records),
            f"result_count={len(records)}",
        ),
        _check(
            "cadence_streamout_results_have_no_errors",
            bool(records) and all(record["result_ok"] for record in records),
            f"error_count={sum(not record['result_ok'] for record in records)}",
        ),
        _check(
            "cadence_streamout_roundtrip_summaries_pass",
            bool(records)
            and all(
                record["roundtrip_summary_ok"]
                and record["roundtrip_payload_ok"]
                and record["roundtrip_stop_after"] == "strmout"
                for record in records
            ),
            f"invalid_count={sum(not (record['roundtrip_summary_ok'] and record['roundtrip_payload_ok'] and record['roundtrip_stop_after'] == 'strmout') for record in records)}",
        ),
        _check(
            "cadence_streamout_gds_files_are_candidate_bound_and_nonzero",
            bool(records)
            and all(
                record["gds_exists_nonzero"] and record["candidate_bound_gds"]
                for record in records
            ),
            f"invalid_count={sum(not (record['gds_exists_nonzero'] and record['candidate_bound_gds']) for record in records)}",
        ),
        _check(
            "cadence_streamout_manifest_ports_and_pin_labels_match",
            bool(records)
            and all(
                record["manifest_ok"]
                and record["manifest_port_count"] == int(expected_ports)
                and record["pin_labels_match_manifest"]
                for record in records
            ),
            f"expected_ports={expected_ports}, invalid_count={sum(not (record['manifest_ok'] and record['manifest_port_count'] == int(expected_ports) and record['pin_labels_match_manifest']) for record in records)}",
        ),
        _check(
            "cadence_streamout_stopped_before_emx",
            bool(records)
            and all(
                record["touchstone_path_is_none"]
                and record["touchstone_file_count"] == 0
                for record in records
            ),
            f"touchstone_file_count={sum(int(record['touchstone_file_count']) for record in records)}",
        ),
    ]
    return {
        "summary": {
            "checked": True,
            "reason": "candidate_bound_cadence_pin_gds_required_before_foundry_drc",
            "expected_ports": int(expected_ports),
            "result_count": len(records),
            "valid_candidate_bound_gds_count": sum(
                bool(record["gds_exists_nonzero"] and record["candidate_bound_gds"])
                for record in records
            ),
            "touchstone_file_count": sum(
                int(record["touchstone_file_count"]) for record in records
            ),
            "records": records,
        },
        "checks": checks,
    }


def _touchstone_output_contract(
    *,
    out_dir: Path,
    rows: list[dict[str, Any]],
    create_only: bool,
    cadence_streamout_only: bool = False,
    expected_extension: str,
    expected_ports: int,
    expected_frequency_start_ghz: float | None,
    expected_frequency_stop_ghz: float | None,
    expected_frequency_step_ghz: float | None,
    expected_frequency_points: int | None,
    frequency_tolerance_hz: float,
    max_touchstone_checks: int,
) -> dict[str, Any]:
    expected_extension = _normalise_suffix(expected_extension)
    if create_only or cadence_streamout_only:
        reason = (
            "cadence_streamout_only_has_no_emx_touchstone_output"
            if cadence_streamout_only
            else "create_only_run_has_no_emx_touchstone_output"
        )
        check_name = (
            "touchstone_output_contract_skipped_for_cadence_streamout_only"
            if cadence_streamout_only
            else "touchstone_output_contract_skipped_for_create_only"
        )
        summary = {
            "checked": False,
            "reason": reason,
            "expected_extension": expected_extension,
            "expected_ports": int(expected_ports),
            "sampled_count": 0,
        }
        return {
            "summary": summary,
            "checks": [
                _check(
                    check_name,
                    True,
                    "layout-only modes intentionally do not require Touchstone output",
                )
            ],
        }

    ok_rows = [row for row in rows if _truthy(row.get("ok"))]
    resolved = [_resolve_touchstone_path(row, out_dir) for row in ok_rows]
    paths = [path for raw, path in resolved if raw and path is not None]
    missing_path_rows = [row for row, (raw, path) in zip(ok_rows, resolved) if not raw or path is None]
    existing_paths = [path for path in paths if path.is_file()]
    nonzero_paths = [path for path in existing_paths if path.stat().st_size > 0]
    extension_paths = [path for path in nonzero_paths if path.suffix.lower() == expected_extension]
    sample_paths = extension_paths[: max(0, int(max_touchstone_checks))]

    parse_errors: list[str] = []
    port_errors: list[str] = []
    frequency_errors: list[str] = []
    parsed_count = 0
    for path in sample_paths:
        try:
            result = load_touchstone(path)
            parsed_count += 1
        except Exception as exc:  # noqa: BLE001 - exact file failure is recorded for gate provenance.
            parse_errors.append(f"{path}: {exc}")
            continue
        if int(result.num_ports) != int(expected_ports):
            port_errors.append(f"{path}: ports={result.num_ports}, expected={expected_ports}")
        frequency_error = _frequency_grid_error(
            result.freqs_hz,
            expected_start_ghz=expected_frequency_start_ghz,
            expected_stop_ghz=expected_frequency_stop_ghz,
            expected_step_ghz=expected_frequency_step_ghz,
            expected_points=expected_frequency_points,
            tolerance_hz=frequency_tolerance_hz,
        )
        if frequency_error is not None:
            frequency_errors.append(f"{path}: {frequency_error}")

    missing_files = [str(path) for path in paths if not path.is_file()]
    zero_files = [str(path) for path in existing_paths if path.stat().st_size <= 0]
    wrong_extensions = [str(path) for path in nonzero_paths if path.suffix.lower() != expected_extension]
    frequency_check_requested = any(
        value is not None
        for value in (
            expected_frequency_start_ghz,
            expected_frequency_stop_ghz,
            expected_frequency_step_ghz,
            expected_frequency_points,
        )
    )
    summary = {
        "checked": True,
        "ok_row_count": len(ok_rows),
        "missing_touchstone_path_row_count": len(missing_path_rows),
        "resolved_path_count": len(paths),
        "existing_file_count": len(existing_paths),
        "nonzero_file_count": len(nonzero_paths),
        "expected_extension": expected_extension,
        "extension_match_count": len(extension_paths),
        "expected_ports": int(expected_ports),
        "sampled_count": len(sample_paths),
        "parsed_count": parsed_count,
        "parse_error_count": len(parse_errors),
        "port_error_count": len(port_errors),
        "frequency_error_count": len(frequency_errors),
        "expected_frequency": {
            "start_ghz": expected_frequency_start_ghz,
            "stop_ghz": expected_frequency_stop_ghz,
            "step_ghz": expected_frequency_step_ghz,
            "points": expected_frequency_points,
            "tolerance_hz": frequency_tolerance_hz,
        },
        "example_missing_files": missing_files[:5],
        "example_zero_files": zero_files[:5],
        "example_wrong_extensions": wrong_extensions[:5],
        "example_parse_errors": parse_errors[:5],
        "example_port_errors": port_errors[:5],
        "example_frequency_errors": frequency_errors[:5],
    }
    checks = [
        _check(
            "success_rows_present_for_touchstone_check",
            len(ok_rows) > 0,
            f"ok_rows={len(ok_rows)}",
        ),
        _check(
            "ok_rows_have_touchstone_paths",
            len(missing_path_rows) == 0 and len(paths) == len(ok_rows),
            f"resolved_paths={len(paths)}, ok_rows={len(ok_rows)}, missing_path_rows={len(missing_path_rows)}",
        ),
        _check(
            "touchstone_files_exist",
            len(existing_paths) == len(paths) and len(paths) > 0,
            f"existing_files={len(existing_paths)}, resolved_paths={len(paths)}, examples={missing_files[:3]}",
        ),
        _check(
            "touchstone_files_nonzero",
            len(nonzero_paths) == len(existing_paths) and len(existing_paths) > 0,
            f"nonzero_files={len(nonzero_paths)}, existing_files={len(existing_paths)}, examples={zero_files[:3]}",
        ),
        _check(
            "touchstone_extensions_match_expected",
            len(extension_paths) == len(nonzero_paths) and len(nonzero_paths) > 0,
            f"extension={expected_extension}, matching={len(extension_paths)}, nonzero_files={len(nonzero_paths)}, examples={wrong_extensions[:3]}",
        ),
        _check(
            "sampled_touchstone_files_parse",
            len(sample_paths) > 0 and len(parse_errors) == 0 and parsed_count == len(sample_paths),
            f"sampled={len(sample_paths)}, parsed={parsed_count}, errors={parse_errors[:3]}",
        ),
        _check(
            "sampled_touchstone_ports_match_expected",
            len(sample_paths) > 0 and len(port_errors) == 0 and parsed_count == len(sample_paths),
            f"expected_ports={expected_ports}, sampled={len(sample_paths)}, errors={port_errors[:3]}",
        ),
        _check(
            "sampled_touchstone_frequency_grid_matches_expected",
            (not frequency_check_requested)
            or (len(sample_paths) > 0 and len(frequency_errors) == 0 and parsed_count == len(sample_paths)),
            (
                "frequency grid check not requested"
                if not frequency_check_requested
                else f"sampled={len(sample_paths)}, errors={frequency_errors[:3]}"
            ),
        ),
    ]
    return {"summary": summary, "checks": checks}


def _resolve_touchstone_path(row: dict[str, Any], out_dir: Path) -> tuple[str, Path | None]:
    raw = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
    if not raw or raw.lower() in {"none", "null", "nan"}:
        return raw, None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return raw, path
    candidate = out_dir / path
    return raw, candidate


def _normalise_suffix(value: str) -> str:
    suffix = str(value or "").strip().lower()
    if not suffix:
        return ".s8p"
    return suffix if suffix.startswith(".") else f".{suffix}"


def _frequency_grid_error(
    freqs_hz: Any,
    *,
    expected_start_ghz: float | None,
    expected_stop_ghz: float | None,
    expected_step_ghz: float | None,
    expected_points: int | None,
    tolerance_hz: float,
) -> str | None:
    freqs = [float(value) for value in freqs_hz]
    if expected_points is not None and len(freqs) != int(expected_points):
        return f"points={len(freqs)}, expected={expected_points}"
    if not freqs:
        return "no frequency points"
    tolerance = float(tolerance_hz)
    if expected_start_ghz is not None:
        expected = float(expected_start_ghz) * 1.0e9
        if abs(freqs[0] - expected) > tolerance:
            return f"start_hz={freqs[0]}, expected={expected}, tolerance={tolerance}"
    if expected_stop_ghz is not None:
        expected = float(expected_stop_ghz) * 1.0e9
        if abs(freqs[-1] - expected) > tolerance:
            return f"stop_hz={freqs[-1]}, expected={expected}, tolerance={tolerance}"
    if expected_step_ghz is not None and len(freqs) > 1:
        expected = float(expected_step_ghz) * 1.0e9
        max_error = max(abs((freqs[index] - freqs[index - 1]) - expected) for index in range(1, len(freqs)))
        if max_error > tolerance:
            return f"max_step_error_hz={max_error}, expected_step_hz={expected}, tolerance={tolerance}"
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "pass", "ok"}


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Candidate Queue Dataset Run",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Input rows: `{summary['input_row_count']}`",
        f"Selected rows: `{summary['selected_row_count']}`",
        f"Results: `{summary['result_count']}`; ok `{summary['ok_count']}`; fail `{summary['fail_count']}`",
        f"Run EMX: `{summary['run_emx']}`",
        f"Dataset rows: `{summary['dataset_rows_csv']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
