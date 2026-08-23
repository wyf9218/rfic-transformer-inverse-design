"""Dataset sampling, export, and coverage checks for transformer EMX runs."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import qmc

from .core.adapter import TransformerOptimizationAdapter
from .core.topology import TransformerSpec
from .core.types import TransformerEvalResult, TransformerRunConfig
from .execution.evaluator import TransformerEmxEvaluator
from .execution.serialization import _json_default

DatasetSampler = Literal["lhs", "lhs_optimized", "sobol"]
GROUND_CLEARANCE_AUDIT_FILENAME = "final500_ground_clearance_audit.json"
POWER_LINE_8PORT_GROUND_FRAME_POLICY = "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"


@dataclass(frozen=True)
class SampledGeometries:
    """Geometry samples plus their normalized unit-hypercube coordinates."""

    geometries: tuple[TransformerSpec, ...]
    unit_vectors: np.ndarray
    field_order: tuple[str, ...]
    bounds: tuple[tuple[float, float], ...]


def loaded_input_impedance(z_diff: np.ndarray, z_load_ohm: complex | float = 50.0) -> np.ndarray:
    """Compute loaded input impedance from a differential 2-port Z matrix."""

    z = np.asarray(z_diff, dtype=np.complex128)
    if z.ndim != 3 or z.shape[1:] != (2, 2):
        raise ValueError(f"Expected differential Z with shape (N,2,2), got {z.shape}")
    load = complex(z_load_ohm)
    return z[:, 0, 0] - (z[:, 0, 1] * z[:, 1, 0]) / (z[:, 1, 1] + load)


def sample_geometries(
    run_config: TransformerRunConfig,
    *,
    count: int,
    sampler: DatasetSampler = "lhs",
    seed: int = 1234,
) -> SampledGeometries:
    """Sample geometries uniformly over the active search-space dimensions."""

    total = int(count)
    if total <= 0:
        raise ValueError("count must be positive")
    adapter = TransformerOptimizationAdapter(run_config.bounds)
    field_order = adapter.field_order()
    bounds = tuple(tuple(map(float, item)) for item in run_config.bounds.to_scipy_bounds())
    if not field_order:
        raise ValueError("run_config has no optimizable geometry fields")
    lower = np.array([item[0] for item in bounds], dtype=float)
    upper = np.array([item[1] for item in bounds], dtype=float)
    if np.any(upper < lower):
        raise ValueError("all search-space upper bounds must be >= lower bounds")

    if sampler == "lhs":
        unit = qmc.LatinHypercube(d=len(field_order), seed=int(seed)).random(n=total)
    elif sampler == "lhs_optimized":
        unit = qmc.LatinHypercube(d=len(field_order), seed=int(seed), optimization="random-cd").random(n=total)
    elif sampler == "sobol":
        engine = qmc.Sobol(d=len(field_order), scramble=True, seed=int(seed))
        if total > 0 and (total & (total - 1)) == 0:
            unit = engine.random_base2(m=int(math.log2(total)))
        else:
            unit = engine.random(n=total)
    else:
        raise ValueError(f"Unsupported dataset sampler: {sampler}")

    vectors = qmc.scale(unit, lower, upper)
    geometries = tuple(_sync_shared_line_width_if_required(run_config, adapter.from_vector(vector)) for vector in vectors)
    vectors = np.asarray([adapter.to_vector(geometry) for geometry in geometries], dtype=float)
    unit = (vectors - lower) / np.maximum(upper - lower, 1.0e-12)
    unit = np.clip(unit, 0.0, 1.0)
    return SampledGeometries(
        geometries=geometries,
        unit_vectors=np.asarray(unit, dtype=float),
        field_order=field_order,
        bounds=bounds,
    )


def _sync_shared_line_width_if_required(run_config: TransformerRunConfig, geometry: TransformerSpec) -> TransformerSpec:
    if not bool(run_config.emx.power_line_8port.enabled):
        return geometry
    return geometry.with_shared_line_width(geometry.primary.trace_width_um)


def result_to_dataset_row(result: TransformerEvalResult, *, z_load_ohm: complex | float = 50.0) -> dict[str, object]:
    """Flatten one evaluation result into a CSV-friendly dataset row."""

    row: dict[str, object] = {
        "evaluation": result.cache_key,
        "ok": bool(result.ok()),
        "error": result.error,
        "work_dir": str(result.work_dir),
        "touchstone_path": None if result.touchstone_path is None else str(result.touchstone_path),
    }
    for key, value in result.geometry.flat_dict().items():
        row[f"geom__{key}"] = value
    if result.metrics is not None:
        for key, value in result.metrics.as_dict().items():
            _flatten_value(row, f"metrics__{key}", value)
    if result.objective is not None:
        for key, value in result.objective.as_dict().items():
            _flatten_value(row, f"objective__{key}", value)
    if result.geometry_check is not None:
        row["geometry_check_ok"] = bool(result.geometry_check.get("ok", False))
        row["geometry_check_backend"] = result.geometry_check.get("backend")
        metrics = result.geometry_check.get("metrics", {})
        if isinstance(metrics, dict):
            for key, value in metrics.items():
                _flatten_value(row, f"geometry_check__{key}", value)
    if result.differential_z is not None and result.differential_sparams is not None:
        _add_frequency_coverage_columns(row, freqs_hz=result.differential_sparams.freqs_hz)
        _add_physical_feature_columns(
            row,
            freqs_hz=result.differential_sparams.freqs_hz,
            z_diff=result.differential_z,
            target_f0_hz=result.target.f0_hz,
        )
        _add_zin_columns(
            row,
            freqs_hz=result.differential_sparams.freqs_hz,
            z_diff=result.differential_z,
            target_f0_hz=result.target.f0_hz,
            z_load_ohm=z_load_ohm,
        )
        _add_sparameter_quality_columns(
            row,
            s_diff=result.differential_sparams.s_matrix,
            target_f0_hz=result.target.f0_hz,
            freqs_hz=result.differential_sparams.freqs_hz,
        )
    return row


def write_dataset_csv(rows: Iterable[dict[str, object]], path: Path) -> Path:
    """Write rows with a stable header sorted after common leading columns."""

    row_list = list(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: set[str] = set()
    for row in row_list:
        keys.update(row)
    leading = ["evaluation", "ok", "error", "work_dir", "touchstone_path"]
    fieldnames = [key for key in leading if key in keys]
    fieldnames.extend(sorted(key for key in keys if key not in set(fieldnames)))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(row_list)
    return path


def write_dataset_manifest(
    *,
    path: Path,
    run_config: TransformerRunConfig,
    samples: SampledGeometries,
    results: Iterable[TransformerEvalResult],
    sampler: DatasetSampler,
    seed: int,
    batch_size: int,
    z_load_ohm: complex | float,
    csv_path: Path,
    ground_clearance_audit_path: Path | None = None,
    ground_clearance_audit: dict[str, object] | None = None,
    uniformity_bins: int = 10,
) -> dict[str, object]:
    """Write a JSON manifest covering sampling, pass/fail, uniformity, and Zin range."""

    result_list = list(results)
    rows = [result_to_dataset_row(result, z_load_ohm=z_load_ohm) for result in result_list]
    power_line_8port = run_config.emx.power_line_8port.as_dict()
    if bool(power_line_8port.get("enabled")):
        shield_width_um = 0.0 if run_config.bounds.shield.width_um is None else float(run_config.bounds.shield.width_um)
        shield_margin_um = 0.0 if run_config.bounds.shield.margin_um is None else float(run_config.bounds.shield.margin_um)
        power_line_8port["ground_frame_width_um"] = max(shield_width_um, shield_margin_um)
        power_line_8port["ground_frame_policy"] = POWER_LINE_8PORT_GROUND_FRAME_POLICY
        power_line_8port["shield_width_um"] = shield_width_um
        power_line_8port["shield_margin_um"] = shield_margin_um

    manifest = {
        "sampler": sampler,
        "seed": int(seed),
        "requested_count": int(len(samples.geometries)),
        "batch_size": int(batch_size),
        "z_load_ohm": _complex_or_float(z_load_ohm),
        "csv_path": str(csv_path),
        "port_mode": run_config.emx.port_mode,
        "cadence_pin_purpose": run_config.emx.cadence_pin_purpose,
        "differential_port_pairs": (
            None
            if run_config.emx.differential_port_pairs is None
            else [[int(a) + 1, int(b) + 1] for a, b in run_config.emx.differential_port_pairs]
        ),
        "ground_unused_s8p_ports": bool(run_config.emx.ground_unused_s8p_ports),
        "power_line_8port": power_line_8port,
        "shield_enabled": bool(run_config.bounds.shield.enabled),
        "field_order": list(samples.field_order),
        "bounds": {name: list(bound) for name, bound in zip(samples.field_order, samples.bounds)},
        "target_frequency": target_frequency_report(run_config),
        "ok_count": int(sum(1 for result in result_list if result.ok())),
        "fail_count": int(sum(1 for result in result_list if not result.ok())),
        "uniformity": uniformity_report(samples.unit_vectors, samples.field_order, bins=uniformity_bins),
        "zin_coverage": zin_coverage_report(rows),
        "sparameter_quality": sparameter_quality_report(rows),
        "geometry_quality": geometry_quality_report(result_list),
    }
    if ground_clearance_audit_path is not None:
        manifest["ground_clearance_audit_path"] = str(ground_clearance_audit_path)
    if ground_clearance_audit is not None:
        manifest["ground_clearance_quality"] = ground_clearance_quality_summary(ground_clearance_audit)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8")
    return manifest


def write_ground_clearance_audit(results: Iterable[TransformerEvalResult], path: Path) -> dict[str, object]:
    """Write a dataset-level signal-to-ground shield clearance audit."""

    result_list = list(results)
    records = [_ground_clearance_record_from_result(result) for result in result_list]
    pass_count = sum(1 for record in records if record["status"] == "pass_signal_to_shield_clearance")
    reject_count = sum(1 for record in records if record["status"] == "reject_signal_to_shield_clearance")
    missing_or_other_count = len(records) - pass_count - reject_count
    selected = next((record for record in records if record["status"] == "pass_signal_to_shield_clearance"), None)
    audit: dict[str, object] = {
        "schema": "rfic_transformer_dataset_ground_clearance_audit.v1",
        "source": "sample-dataset geometry export sidecar audits",
        "candidate_count": len(records),
        "pass_count": pass_count,
        "reject_count": reject_count,
        "missing_or_other_count": missing_or_other_count,
        "selected": selected,
        "records": records,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2, default=_json_default), encoding="utf-8")
    return audit


def ground_clearance_quality_summary(audit: dict[str, object]) -> dict[str, object]:
    return {
        "candidate_count": audit.get("candidate_count"),
        "pass_count": audit.get("pass_count"),
        "reject_count": audit.get("reject_count"),
        "missing_or_other_count": audit.get("missing_or_other_count"),
        "selected_cache_key": (audit.get("selected") or {}).get("cache_key") if isinstance(audit.get("selected"), dict) else None,
        "selected_status": (audit.get("selected") or {}).get("status") if isinstance(audit.get("selected"), dict) else None,
    }


def _ground_clearance_record_from_result(result: TransformerEvalResult) -> dict[str, object]:
    geometry_check = result.geometry_check if isinstance(result.geometry_check, dict) else {}
    metrics = geometry_check.get("metrics", {}) if isinstance(geometry_check, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    audit = geometry_check.get("signal_shield_clearance_audit") if isinstance(geometry_check, dict) else None
    audit = audit if isinstance(audit, dict) else {}

    direct_area = _first_number(
        audit.get("direct_signal_shield_overlap_area_um2"),
        metrics.get("signal_shield_direct_overlap_area_um2"),
    )
    violation_area = _first_number(
        audit.get("signal_shield_clearance_violation_area_um2"),
        metrics.get("signal_shield_clearance_violation_area_um2"),
    )
    audit_enabled = bool(audit.get("enabled", metrics.get("signal_shield_clearance_audit_enabled", False)))
    audit_status = str(audit.get("status") or metrics.get("signal_shield_clearance_status") or "")
    if audit_enabled and audit_status == "pass_signal_to_shield_clearance" and direct_area <= 1.0e-6 and violation_area <= 1.0e-6:
        status = "pass_signal_to_shield_clearance"
    elif audit_enabled and (
        audit_status == "reject_signal_to_shield_clearance"
        or direct_area > 1.0e-6
        or violation_area > 1.0e-6
    ):
        status = "reject_signal_to_shield_clearance"
    else:
        status = "missing_or_other_clearance_evidence"

    record: dict[str, object] = {
        "cache_key": result.cache_key,
        "status": status,
        "ok": bool(result.ok()),
        "geometry_check_ok": bool(geometry_check.get("ok", False)) if isinstance(geometry_check, dict) else False,
        "work_dir": str(result.work_dir),
        "touchstone_path": None if result.touchstone_path is None else str(result.touchstone_path),
        "error": result.error,
        "audit_enabled": audit_enabled,
        "audit_status": audit_status or None,
        "audit_reason": audit.get("reason"),
        "direct_signal_shield_overlap_area_um2": direct_area,
        "signal_shield_clearance_violation_area_um2": violation_area,
    }
    per_signal = audit.get("records")
    if isinstance(per_signal, list):
        record["per_signal_records"] = per_signal
    return record


def run_sample_dataset(
    *,
    run_config: TransformerRunConfig,
    out_dir: Path,
    count: int,
    batch_size: int = 10,
    sampler: DatasetSampler = "lhs",
    seed: int = 1234,
    run_emx: bool = True,
    z_load_ohm: complex | float = 50.0,
    uniformity_bins: int = 10,
) -> dict[str, object]:
    """Sample, evaluate, write CSV, and write a coverage manifest."""

    out_root = Path(out_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    samples = sample_geometries(run_config, count=count, sampler=sampler, seed=seed)
    evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=out_root)
    results: list[TransformerEvalResult] = []
    batch = max(1, int(batch_size))
    for start in range(0, len(samples.geometries), batch):
        chunk = samples.geometries[start : start + batch]
        results.extend(evaluator.evaluate_geometry_batch(chunk, run_emx=run_emx))
    rows = [result_to_dataset_row(result, z_load_ohm=z_load_ohm) for result in results]
    csv_path = write_dataset_csv(rows, out_root / "dataset_rows.csv")
    ground_clearance_audit_path = out_root / GROUND_CLEARANCE_AUDIT_FILENAME
    ground_clearance_audit = write_ground_clearance_audit(results, ground_clearance_audit_path)
    manifest = write_dataset_manifest(
        path=out_root / "dataset_manifest.json",
        run_config=run_config,
        samples=samples,
        results=results,
        sampler=sampler,
        seed=seed,
        batch_size=batch,
        z_load_ohm=z_load_ohm,
        csv_path=csv_path,
        ground_clearance_audit_path=ground_clearance_audit_path,
        ground_clearance_audit=ground_clearance_audit,
        uniformity_bins=uniformity_bins,
    )
    return manifest


def uniformity_report(unit_vectors: np.ndarray, field_order: tuple[str, ...], *, bins: int = 10) -> dict[str, object]:
    """Report marginal uniformity of unit-hypercube samples."""

    unit = np.asarray(unit_vectors, dtype=float)
    report: dict[str, object] = {
        "bins": int(bins),
        "count": int(unit.shape[0]),
        "fields": {},
        "space_filling": {},
    }
    if unit.ndim != 2:
        return report
    report["space_filling"] = _space_filling_report(unit)
    for idx, name in enumerate(field_order):
        column = unit[:, idx]
        hist, _ = np.histogram(column, bins=int(bins), range=(0.0, 1.0))
        expected = len(column) / float(bins)
        chi_square = float(np.sum((hist - expected) ** 2 / max(expected, 1.0e-12)))
        report["fields"][name] = {
            "min": float(np.min(column)),
            "max": float(np.max(column)),
            "mean": float(np.mean(column)),
            "std": float(np.std(column)),
            "histogram": hist.astype(int).tolist(),
            "histogram_min": int(np.min(hist)),
            "histogram_max": int(np.max(hist)),
            "chi_square": chi_square,
        }
    return report


def _space_filling_report(unit_vectors: np.ndarray) -> dict[str, object]:
    unit = np.asarray(unit_vectors, dtype=float)
    if unit.ndim != 2 or unit.shape[0] == 0:
        return {}
    report: dict[str, object] = {
        "dimension": int(unit.shape[1]),
        "centered_l2_discrepancy": float(qmc.discrepancy(unit, method="CD")),
    }
    if unit.shape[0] <= 1:
        report["nearest_neighbor_distance"] = _range_summary([])
        report["pairwise_abs_correlation"] = _range_summary([])
        return report

    tree = cKDTree(unit)
    distances, _ = tree.query(unit, k=2)
    nearest = distances[:, 1] if distances.ndim == 2 else []
    report["nearest_neighbor_distance"] = _range_summary([float(value) for value in nearest])

    if unit.shape[1] <= 1:
        report["pairwise_abs_correlation"] = _range_summary([])
        return report
    corr = np.corrcoef(unit, rowvar=False)
    tri = np.triu_indices(unit.shape[1], k=1)
    pair_corr = np.abs(corr[tri])
    pair_corr = pair_corr[np.isfinite(pair_corr)]
    report["pairwise_abs_correlation"] = _range_summary([float(value) for value in pair_corr])
    return report


def zin_coverage_report(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    """Summarize center-frequency loaded Zin coverage from dataset rows."""

    real: list[float] = []
    imag: list[float] = []
    mag: list[float] = []
    band_real_min: list[float] = []
    band_real_max: list[float] = []
    band_imag_min: list[float] = []
    band_imag_max: list[float] = []
    band_abs_min: list[float] = []
    band_abs_max: list[float] = []
    for row in rows:
        if not bool(row.get("ok")):
            continue
        r = row.get("zin_center_real_ohm")
        x = row.get("zin_center_imag_ohm")
        m = row.get("zin_center_abs_ohm")
        if isinstance(r, (int, float)) and isinstance(x, (int, float)) and isinstance(m, (int, float)):
            real.append(float(r))
            imag.append(float(x))
            mag.append(float(m))
        _append_if_number(band_real_min, row.get("zin_real_min_ohm"))
        _append_if_number(band_real_max, row.get("zin_real_max_ohm"))
        _append_if_number(band_imag_min, row.get("zin_imag_min_ohm"))
        _append_if_number(band_imag_max, row.get("zin_imag_max_ohm"))
        _append_if_number(band_abs_min, row.get("zin_abs_min_ohm"))
        _append_if_number(band_abs_max, row.get("zin_abs_max_ohm"))
    return {
        "valid_zin_count": len(real),
        "real_ohm": _range_summary(real),
        "imag_ohm": _range_summary(imag),
        "abs_ohm": _range_summary(mag),
        "band_real_min_ohm": _range_summary(band_real_min),
        "band_real_max_ohm": _range_summary(band_real_max),
        "band_imag_min_ohm": _range_summary(band_imag_min),
        "band_imag_max_ohm": _range_summary(band_imag_max),
        "band_abs_min_ohm": _range_summary(band_abs_min),
        "band_abs_max_ohm": _range_summary(band_abs_max),
    }


def sparameter_quality_report(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    """Summarize label-level S-parameter sanity checks for passive surrogate training."""

    freq_points: list[float] = []
    freq_start: list[float] = []
    freq_stop: list[float] = []
    freq_step: list[float] = []
    freq_step_span: list[float] = []
    reciprocity: list[float] = []
    sigma_max: list[float] = []
    sigma_excess: list[float] = []
    s11_db: list[float] = []
    s21_db: list[float] = []
    for row in rows:
        if not bool(row.get("ok")):
            continue
        _append_if_number(freq_points, row.get("sparam_freq_points"))
        _append_if_number(freq_start, row.get("sparam_freq_start_hz"))
        _append_if_number(freq_stop, row.get("sparam_freq_stop_hz"))
        _append_if_number(freq_step, row.get("sparam_freq_step_hz"))
        _append_if_number(freq_step_span, row.get("sparam_freq_step_span_hz"))
        _append_if_number(reciprocity, row.get("sparam_reciprocity_error_abs_max"))
        _append_if_number(sigma_max, row.get("sparam_passivity_sigma_max"))
        _append_if_number(sigma_excess, row.get("sparam_passivity_excess_max"))
        _append_if_number(s11_db, row.get("sdd11_center_db"))
        _append_if_number(s21_db, row.get("sdd21_center_db"))
    return {
        "valid_sparameter_count": len(sigma_max),
        "frequency_point_count": _range_summary(freq_points),
        "frequency_start_hz": _range_summary(freq_start),
        "frequency_stop_hz": _range_summary(freq_stop),
        "frequency_step_hz": _range_summary(freq_step),
        "frequency_step_span_hz": _range_summary(freq_step_span),
        "reciprocity_error_abs": _range_summary(reciprocity),
        "passivity_sigma_max": _range_summary(sigma_max),
        "passivity_excess": _range_summary(sigma_excess),
        "passivity_excess_count_gt_1e_3": int(sum(1 for value in sigma_excess if value > 1.0e-3)),
        "sdd11_center_db": _range_summary(s11_db),
        "sdd21_center_db": _range_summary(s21_db),
    }


def target_frequency_report(run_config: TransformerRunConfig) -> dict[str, object]:
    """Summarize the requested frequency grid for EMX/HFSS training labels."""

    freqs = np.asarray(run_config.target.frequency_points_hz(), dtype=float)
    diffs = np.diff(freqs)
    return {
        "f0_hz": float(run_config.target.f0_hz),
        "start_hz": float(freqs[0]) if freqs.size else None,
        "stop_hz": float(freqs[-1]) if freqs.size else None,
        "step_hz": float(diffs[0]) if diffs.size else None,
        "points": int(freqs.size),
        "step_min_hz": float(np.min(diffs)) if diffs.size else None,
        "step_max_hz": float(np.max(diffs)) if diffs.size else None,
        "explicit_sweep": bool(
            run_config.target.frequency_start_hz is not None
            and run_config.target.frequency_stop_hz is not None
            and run_config.target.frequency_step_hz is not None
        ),
    }


def geometry_quality_report(results: Iterable[TransformerEvalResult]) -> dict[str, object]:
    """Summarize source-level geometry checks, including winding angle compliance."""

    result_list = list(results)
    checked = [result.geometry_check for result in result_list if result.geometry_check is not None]
    ok_checks = [check for check in checked if bool(check.get("ok", False))]
    primary_internal_min: list[float] = []
    primary_internal_max: list[float] = []
    secondary_internal_min: list[float] = []
    secondary_internal_max: list[float] = []
    primary_terminal_min: list[float] = []
    primary_terminal_max: list[float] = []
    secondary_terminal_min: list[float] = []
    secondary_terminal_max: list[float] = []
    angle_checked_count = 0
    for check in checked:
        metrics = check.get("metrics", {})
        if not isinstance(metrics, dict):
            continue
        if "primary_winding_centerline_internal_turn_count" in metrics:
            angle_checked_count += 1
        _append_if_number(primary_internal_min, metrics.get("primary_winding_centerline_min_internal_angle_deg"))
        _append_if_number(primary_internal_max, metrics.get("primary_winding_centerline_max_internal_angle_deg"))
        _append_if_number(secondary_internal_min, metrics.get("secondary_winding_centerline_min_internal_angle_deg"))
        _append_if_number(secondary_internal_max, metrics.get("secondary_winding_centerline_max_internal_angle_deg"))
        _append_if_number(primary_terminal_min, metrics.get("primary_winding_centerline_min_terminal_angle_deg"))
        _append_if_number(primary_terminal_max, metrics.get("primary_winding_centerline_max_terminal_angle_deg"))
        _append_if_number(secondary_terminal_min, metrics.get("secondary_winding_centerline_min_terminal_angle_deg"))
        _append_if_number(secondary_terminal_max, metrics.get("secondary_winding_centerline_max_terminal_angle_deg"))

    return {
        "geometry_check_count": len(checked),
        "geometry_check_ok_count": len(ok_checks),
        "angle_checked_count": int(angle_checked_count),
        "primary_internal_angle_deg": {
            "min": _range_summary(primary_internal_min),
            "max": _range_summary(primary_internal_max),
        },
        "secondary_internal_angle_deg": {
            "min": _range_summary(secondary_internal_min),
            "max": _range_summary(secondary_internal_max),
        },
        "primary_terminal_interface_angle_deg": {
            "min": _range_summary(primary_terminal_min),
            "max": _range_summary(primary_terminal_max),
        },
        "secondary_terminal_interface_angle_deg": {
            "min": _range_summary(secondary_terminal_min),
            "max": _range_summary(secondary_terminal_max),
        },
    }


def _add_frequency_coverage_columns(row: dict[str, object], *, freqs_hz: np.ndarray) -> None:
    freqs = np.asarray(freqs_hz, dtype=float)
    if freqs.ndim != 1 or freqs.size == 0:
        return
    diffs = np.diff(freqs)
    row["sparam_freq_start_hz"] = float(freqs[0])
    row["sparam_freq_stop_hz"] = float(freqs[-1])
    row["sparam_freq_points"] = int(freqs.size)
    if diffs.size:
        row["sparam_freq_step_hz"] = float(diffs[0])
        row["sparam_freq_step_min_hz"] = float(np.min(diffs))
        row["sparam_freq_step_max_hz"] = float(np.max(diffs))
        row["sparam_freq_step_span_hz"] = float(np.max(diffs) - np.min(diffs))
    else:
        row["sparam_freq_step_hz"] = None
        row["sparam_freq_step_min_hz"] = None
        row["sparam_freq_step_max_hz"] = None
        row["sparam_freq_step_span_hz"] = None


def _add_zin_columns(
    row: dict[str, object],
    *,
    freqs_hz: np.ndarray,
    z_diff: np.ndarray,
    target_f0_hz: float,
    z_load_ohm: complex | float,
) -> None:
    freqs = np.asarray(freqs_hz, dtype=float)
    z = np.asarray(z_diff, dtype=np.complex128)
    center_idx = int(np.argmin(np.abs(freqs - float(target_f0_hz))))
    zin = loaded_input_impedance(z, z_load_ohm=z_load_ohm)
    center = complex(zin[center_idx])
    row["zin_load_ohm"] = _complex_or_float(z_load_ohm)
    row["zin_center_freq_hz"] = float(freqs[center_idx])
    row["zin_center_real_ohm"] = float(center.real)
    row["zin_center_imag_ohm"] = float(center.imag)
    row["zin_center_abs_ohm"] = float(abs(center))
    row["zin_real_min_ohm"] = float(np.min(np.real(zin)))
    row["zin_real_max_ohm"] = float(np.max(np.real(zin)))
    row["zin_imag_min_ohm"] = float(np.min(np.imag(zin)))
    row["zin_imag_max_ohm"] = float(np.max(np.imag(zin)))
    row["zin_abs_min_ohm"] = float(np.min(np.abs(zin)))
    row["zin_abs_max_ohm"] = float(np.max(np.abs(zin)))
    gamma = (zin - 50.0) / (zin + 50.0)
    gamma_abs = np.abs(gamma)
    center_gamma_abs = float(gamma_abs[center_idx])
    row["zin_center_reflection_abs_50ohm"] = center_gamma_abs
    row["zin_center_return_loss_db_50ohm"] = float(-20.0 * np.log10(max(center_gamma_abs, 1.0e-12)))
    row["zin_reflection_abs_max_50ohm"] = float(np.max(gamma_abs))
    row["zin_return_loss_min_db_50ohm"] = float(-20.0 * np.log10(max(float(np.max(gamma_abs)), 1.0e-12)))
    z0 = z[center_idx]
    for r in range(2):
        for c in range(2):
            value = complex(z0[r, c])
            row[f"zdd{r + 1}{c + 1}_center_real_ohm"] = float(value.real)
            row[f"zdd{r + 1}{c + 1}_center_imag_ohm"] = float(value.imag)


def _add_physical_feature_columns(
    row: dict[str, object],
    *,
    freqs_hz: np.ndarray,
    z_diff: np.ndarray,
    target_f0_hz: float,
) -> None:
    freqs = np.asarray(freqs_hz, dtype=float)
    z = np.asarray(z_diff, dtype=np.complex128)
    if freqs.ndim != 1 or z.ndim != 3 or z.shape[1:] != (2, 2) or len(freqs) != z.shape[0] or freqs.size == 0:
        return
    center_idx = int(np.argmin(np.abs(freqs - float(target_f0_hz))))
    omega = 2.0 * np.pi * freqs
    z11 = z[:, 0, 0]
    z22 = z[:, 1, 1]
    z21 = z[:, 1, 0]
    lp_h = np.imag(z11) / omega
    ls_h = np.imag(z22) / omega
    mutual_h = np.imag(z21) / omega
    denom = np.sqrt(np.maximum(np.abs(lp_h * ls_h), 1.0e-30))
    k = mutual_h / denom
    qp = _safe_div_array(np.imag(z11), np.real(z11))
    qs = _safe_div_array(np.imag(z22), np.real(z22))

    row["physical_feature_center_freq_hz"] = float(freqs[center_idx])
    row["lp_nh_center"] = float(lp_h[center_idx] * 1.0e9)
    row["ls_nh_center"] = float(ls_h[center_idx] * 1.0e9)
    row["m_nh_center"] = float(mutual_h[center_idx] * 1.0e9)
    row["k_center"] = float(k[center_idx])
    row["qp_center"] = float(qp[center_idx])
    row["qs_center"] = float(qs[center_idx])
    row["lp_nh_min"] = float(np.min(lp_h) * 1.0e9)
    row["lp_nh_max"] = float(np.max(lp_h) * 1.0e9)
    row["ls_nh_min"] = float(np.min(ls_h) * 1.0e9)
    row["ls_nh_max"] = float(np.max(ls_h) * 1.0e9)
    row["k_min"] = float(np.min(k))
    row["k_max"] = float(np.max(k))
    row["qp_min"] = float(np.min(qp))
    row["qp_max"] = float(np.max(qp))
    row["qs_min"] = float(np.min(qs))
    row["qs_max"] = float(np.max(qs))


def _add_sparameter_quality_columns(
    row: dict[str, object],
    *,
    s_diff: np.ndarray,
    target_f0_hz: float,
    freqs_hz: np.ndarray,
) -> None:
    s = np.asarray(s_diff, dtype=np.complex128)
    freqs = np.asarray(freqs_hz, dtype=float)
    if s.ndim != 3 or s.shape[1:] != (2, 2) or len(freqs) != s.shape[0]:
        return
    center_idx = int(np.argmin(np.abs(freqs - float(target_f0_hz))))
    reciprocity_error = np.abs(s[:, 0, 1] - s[:, 1, 0])
    singular_values = np.linalg.svd(s, compute_uv=False)
    sigma_max = np.max(singular_values, axis=1)
    sigma_excess = np.maximum(sigma_max - 1.0, 0.0)
    s11_abs = np.abs(s[:, 0, 0])
    s21_abs = np.abs(s[:, 1, 0])
    row["sparam_reciprocity_error_abs_max"] = float(np.max(reciprocity_error))
    row["sparam_passivity_sigma_max"] = float(np.max(sigma_max))
    row["sparam_passivity_excess_max"] = float(np.max(sigma_excess))
    row["sdd11_center_abs"] = float(s11_abs[center_idx])
    row["sdd11_center_db"] = _mag_to_db(s11_abs[center_idx])
    row["sdd11_abs_max"] = float(np.max(s11_abs))
    row["sdd11_db_max"] = _mag_to_db(np.max(s11_abs))
    row["sdd21_center_abs"] = float(s21_abs[center_idx])
    row["sdd21_center_db"] = _mag_to_db(s21_abs[center_idx])
    row["sdd21_abs_min"] = float(np.min(s21_abs))
    row["sdd21_abs_max"] = float(np.max(s21_abs))
    row["sdd21_db_min"] = _mag_to_db(np.min(s21_abs))
    row["sdd21_db_max"] = _mag_to_db(np.max(s21_abs))


def _flatten_value(row: dict[str, object], prefix: str, value: object) -> None:
    if isinstance(value, (int, float, str, bool)) or value is None:
        row[prefix] = value
    elif isinstance(value, complex):
        row[f"{prefix}_real"] = float(value.real)
        row[f"{prefix}_imag"] = float(value.imag)
    elif isinstance(value, dict) and set(value) == {"real", "imag"}:
        row[f"{prefix}_real"] = float(value["real"])
        row[f"{prefix}_imag"] = float(value["imag"])
    elif isinstance(value, (tuple, list)):
        arr = np.asarray(value)
        if arr.ndim == 2 and arr.shape == (2, 2):
            for r in range(2):
                for c in range(2):
                    _flatten_value(row, f"{prefix}_{r + 1}{c + 1}", arr[r, c].item())


def _complex_or_float(value: complex | float) -> object:
    item = complex(value)
    if abs(item.imag) < 1.0e-15:
        return float(item.real)
    return {"real": float(item.real), "imag": float(item.imag)}


def _append_if_number(values: list[float], value: object) -> None:
    if isinstance(value, (int, float)):
        values.append(float(value))


def _first_number(*values: Any) -> float:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _mag_to_db(value: float | np.floating) -> float:
    return float(20.0 * np.log10(max(float(value), 1.0e-12)))


def _safe_div_array(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    den = np.asarray(denominator, dtype=float)
    num = np.asarray(numerator, dtype=float)
    return np.divide(num, den, out=np.zeros_like(num, dtype=float), where=np.abs(den) > 1.0e-12)


def _range_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "std": None}
    arr = np.asarray(values, dtype=float)
    return {
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
    }
