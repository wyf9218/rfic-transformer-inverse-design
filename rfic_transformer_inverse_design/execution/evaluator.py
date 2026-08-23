"""EMX-backed evaluation flow for transformer geometries."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..sim.touchstone import load_touchstone

from ..analysis.extraction import extract_transformer_metrics, extract_transformer_metrics_from_differential
from ..analysis.objective import score_transformer_result
from ..core.topology import TransformerSpec
from ..core.types import TransformerEvalResult, TransformerRunConfig
from ..layout import export_transformer_layout, run_transformer_gdstk_checks
from ..layout.export import (
    POWER_LINE_8PORT_GEOMETRY_AUDIT_FILENAME,
    SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME,
    _signal_shield_clearance_errors_from_report,
)
from .remote_ssh import run_transformer_remote_ssh_roundtrip
from .serialization import _json_default
from .zeus_cadence import (
    CadenceRoundtripExport,
    prepare_transformer_touchstone_result,
    result_from_roundtrip_payload,
    run_transformer_zeus_cadence_roundtrip,
    run_transformer_zeus_cadence_roundtrip_batch,
)

logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_geometry_export_failure(exc: Exception) -> bool:
    if isinstance(exc, ValueError):
        return True
    message = str(exc).lower()
    geometry_markers = (
        "outside configured bounds",
        "straight-side span limit",
        "straight-side straight-section limit",
        "straight-section limit",
        "turn size collapsed",
        "terminal_y_span",
        "geometry validation failed",
        "gdstk geometry check failed",
    )
    return any(marker in message for marker in geometry_markers)


def _read_signal_shield_clearance_audit(layout_dir: Path) -> dict[str, Any] | None:
    path = Path(layout_dir) / SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "schema": "rfic_transformer_signal_shield_clearance_audit.v1",
            "enabled": True,
            "status": "missing_or_invalid_signal_shield_clearance_audit",
            "reason": f"invalid JSON: {exc}",
            "records": [],
            "direct_signal_shield_overlap_area_um2": 0.0,
            "signal_shield_clearance_violation_area_um2": 0.0,
        }
    return payload if isinstance(payload, dict) else None


def _read_power_line_8port_geometry_audit(layout_dir: Path) -> dict[str, Any] | None:
    path = Path(layout_dir) / POWER_LINE_8PORT_GEOMETRY_AUDIT_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "schema": "rfic_transformer_power_line_8port_geometry.v1",
            "enabled": False,
            "reason": f"invalid JSON: {exc}",
        }
    return payload if isinstance(payload, dict) else None


def _attach_power_line_8port_geometry_audit(
    geometry_check: dict[str, object] | None,
    audit: dict[str, Any],
) -> None:
    if geometry_check is None:
        return
    metrics = dict(geometry_check.get("metrics", {}) or {})
    metrics.update(
        {
            "power_line_8port_geometry_audit_enabled": bool(audit.get("enabled", False)),
            "power_line_8port_vertical_length_um": audit.get("vertical_length_um"),
            "power_line_8port_bridge_width_um": audit.get("bridge_width_um"),
            "power_line_8port_ground_frame_width_um": audit.get("ground_frame_width_um"),
            "power_line_8port_ground_frame_policy": audit.get("ground_frame_policy"),
        }
    )
    for side_key in ("primary_power_line", "secondary_power_line"):
        side = audit.get(side_key)
        if not isinstance(side, dict):
            continue
        prefix = side_key.replace("_power_line", "")
        metrics[f"power_line_8port_{prefix}_height_um"] = side.get("height_um")
        metrics[f"power_line_8port_{prefix}_center_x_um"] = side.get("center_x_um")
    for bridge_key in ("primary_bridge", "secondary_bridge"):
        bridge = audit.get(bridge_key)
        if not isinstance(bridge, dict):
            continue
        prefix = bridge_key.replace("_bridge", "")
        metrics[f"power_line_8port_{prefix}_bridge_width_um"] = bridge.get("width_um")
        metrics[f"power_line_8port_{prefix}_bridge_length_um"] = bridge.get("length_um")
        metrics[f"power_line_8port_{prefix}_bridge_delta_y_um"] = bridge.get("delta_y_um")
        metrics[f"power_line_8port_{prefix}_bridge_center_y_um"] = bridge.get("center_y_um")
        metrics[f"power_line_8port_{prefix}_bridge_edge_alignment_error_um"] = bridge.get("power_line_edge_alignment_error_um")
    geometry_check["metrics"] = metrics
    geometry_check["power_line_8port_geometry_audit"] = audit


def _attach_signal_shield_clearance_audit(
    geometry_check: dict[str, object] | None,
    audit: dict[str, Any],
) -> list[str]:
    if geometry_check is None:
        return []
    metrics = dict(geometry_check.get("metrics", {}) or {})
    direct_area = float(audit.get("direct_signal_shield_overlap_area_um2") or 0.0)
    violation_area = float(audit.get("signal_shield_clearance_violation_area_um2") or 0.0)
    status = str(audit.get("status") or "missing_or_invalid_signal_shield_clearance_audit")
    metrics.update(
        {
            "signal_shield_clearance_audit_enabled": bool(audit.get("enabled", False)),
            "signal_shield_clearance_status": status,
            "signal_shield_clearance_record_count": int(audit.get("record_count") or len(audit.get("records") or [])),
            "signal_shield_direct_overlap_area_um2": direct_area,
            "signal_shield_clearance_violation_area_um2": violation_area,
        }
    )
    for record in list(audit.get("records") or []):
        if not isinstance(record, dict):
            continue
        signal_name = str(record.get("signal_name") or "signal")
        metrics[f"{signal_name}_signal_shield_direct_overlap_area_um2"] = float(
            record.get("direct_signal_shield_overlap_area_um2") or 0.0
        )
        metrics[f"{signal_name}_signal_shield_clearance_violation_area_um2"] = float(
            record.get("signal_shield_clearance_violation_area_um2") or 0.0
        )
    geometry_check["metrics"] = metrics
    geometry_check["signal_shield_clearance_audit"] = audit

    if not bool(audit.get("enabled", False)):
        return []
    if status == "pass_signal_to_shield_clearance" and direct_area <= 1.0e-6 and violation_area <= 1.0e-6:
        return []

    clearance_errors = [
        error
        for record in list(audit.get("records") or [])
        if isinstance(record, dict)
        for error in _signal_shield_clearance_errors_from_report(record)
    ]
    if not clearance_errors:
        clearance_errors = [f"signal-to-shield clearance audit status={status}"]
    existing_errors = list(geometry_check.get("errors", []) or [])
    for clearance_error in clearance_errors:
        if clearance_error not in existing_errors:
            existing_errors.append(clearance_error)
    geometry_check["errors"] = existing_errors
    geometry_check["ok"] = False
    return clearance_errors


class TransformerEmxEvaluator:
    """Evaluate transformer geometries through the existing EMX backend."""

    def __init__(self, run_config: TransformerRunConfig, root_dir: Path):
        self.run_config = run_config
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.eval_dir = self.root_dir / "evaluations"
        self.eval_dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, geometry: TransformerSpec) -> str:
        payload = {
            "topology_mode": self.run_config.target.topology_mode,
            "target": asdict(self.run_config.target),
            "geometry": {
                name: (round(value, 6) if isinstance(value, float) else value)
                for name, value in geometry.flat_dict().items()
            },
            "emx": asdict(self.run_config.emx),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return digest

    def export_only(self, geometry: TransformerSpec) -> TransformerEvalResult:
        return self.evaluate_geometry(geometry=geometry, run_emx=False)

    def _evaluate_export(
        self,
        geometry: TransformerSpec,
        *,
        work_dir: Path,
        top_cell_override: str | None = None,
    ) -> tuple[object | None, str | None, dict[str, object] | None]:
        layout_dir = work_dir / "layout"
        error: str | None = None
        geometry_check: dict[str, object] | None = None
        layout = None
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            bounds_errors = list(self.run_config.bounds.validate(geometry))
            geometry_errors = [*bounds_errors, *geometry.validate()]
            if geometry_errors:
                geometry_check = {
                    "backend": "geometry",
                    "errors": geometry_errors,
                    "metrics": {
                        "bounds_validation_errors": len(bounds_errors),
                        "geometry_validation_errors": len(geometry_errors),
                    },
                    "ok": False,
                }
                error = "geometry validation failed: " + "; ".join(geometry_errors)
                return layout, error, geometry_check
            gdstk_result = run_transformer_gdstk_checks(geometry=geometry, run_config=self.run_config)
            geometry_check = {
                "backend": "gdstk",
                "errors": list(gdstk_result.errors),
                "warnings": list(gdstk_result.warnings),
                "metrics": gdstk_result.metrics,
                "ok": len(gdstk_result.errors) == 0,
            }
            if gdstk_result.errors:
                error = "gdstk geometry check failed: " + "; ".join(gdstk_result.errors)
            else:
                export_config = self.run_config
                if top_cell_override is not None:
                    export_config = replace(
                        self.run_config,
                        emx=replace(self.run_config.emx, top_cell_prefix=top_cell_override),
                    )
                layout = export_transformer_layout(
                    geometry=geometry,
                    run_config=export_config,
                    out_dir=layout_dir,
                    validate_geometry=False,
                )
                clearance_audit = _read_signal_shield_clearance_audit(layout_dir)
                if clearance_audit is not None:
                    clearance_errors = _attach_signal_shield_clearance_audit(geometry_check, clearance_audit)
                    if clearance_errors:
                        error = "gdstk geometry check failed: " + "; ".join(clearance_errors)
                power_line_audit = _read_power_line_8port_geometry_audit(layout_dir)
                if power_line_audit is not None:
                    _attach_power_line_8port_geometry_audit(geometry_check, power_line_audit)
        except Exception as exc:
            if _is_geometry_export_failure(exc):
                geometry_check = {
                    "backend": "layout_export",
                    "errors": [str(exc)],
                    "metrics": {
                        "layout_export_failures": 1,
                        "layout_export_geometry_failures": 1,
                    },
                    "ok": False,
                }
            error = f"layout export failed: {exc}"
        return layout, error, geometry_check

    def _write_result_summary(self, result: TransformerEvalResult) -> None:
        (result.work_dir / "summary.json").write_text(
            json.dumps(result.summary_dict(), indent=2, default=_json_default),
            encoding="utf-8",
        )

    def evaluate_geometry(self, geometry: TransformerSpec, run_emx: bool = True) -> TransformerEvalResult:
        key = self.cache_key(geometry)
        work_dir = self.eval_dir / key

        command: list[str] | None = None
        touchstone_path: Path | None = None
        single_result = None
        diff_result = None
        diff_z = None
        metrics = None
        objective = None

        layout, error, geometry_check = self._evaluate_export(geometry, work_dir=work_dir)

        if run_emx and layout is not None and error is None:
            try:
                if self.run_config.emx.uses_remote_ssh():
                    roundtrip_payload = run_transformer_remote_ssh_roundtrip(
                        run_config=self.run_config,
                        geometry=geometry,
                        local_work_dir=work_dir,
                        cache_key=key,
                    )
                else:
                    roundtrip_payload = run_transformer_zeus_cadence_roundtrip(
                        run_config=self.run_config,
                        geometry=geometry,
                        root_dir=self.root_dir,
                        stop_after="emx",
                        cadence_install_root=self.run_config.emx.cadence_install_root,
                        pdk_cds_lib=self.run_config.emx.cadence_pdk_cds_lib,
                        tech_lib_name=self.run_config.emx.cadence_tech_lib,
                        layer_map_path=self.run_config.emx.cadence_layer_map,
                    )
                roundtrip_result = result_from_roundtrip_payload(
                    payload=roundtrip_payload,
                    geometry=geometry,
                    run_config=self.run_config,
                    work_dir=work_dir,
                    cache_key=key,
                    geometry_check=geometry_check,
                )
                layout = roundtrip_result.layout
                metrics = roundtrip_result.metrics
                objective = roundtrip_result.objective
                single_result = roundtrip_result.single_ended_sparams
                diff_result = roundtrip_result.differential_sparams
                diff_z = roundtrip_result.differential_z
                touchstone_path = roundtrip_result.touchstone_path
                command = roundtrip_result.command
                error = roundtrip_result.error
                if error is None and diff_result is not None and diff_z is not None:
                    self._write_diff_analysis(
                        work_dir=work_dir,
                        differential_sparams=diff_result,
                        differential_z=diff_z,
                    )
                    if not _env_flag("RFIC_SKIP_LUMPED_COMPARE"):
                        try:
                            self._render_lumped_compare(work_dir=work_dir, differential_sparams=diff_result)
                        except Exception as exc:  # pragma: no cover - environment-dependent plotting stack
                            logger.warning("Skipping lumped compare render for %s: %s", work_dir, exc)
            except Exception as exc:  # pragma: no cover - exercised by integration path
                error = str(exc)

        result = TransformerEvalResult(
            cache_key=key,
            geometry=geometry,
            target=self.run_config.target,
            layout=layout,
            metrics=metrics,
            objective=objective,
            single_ended_sparams=single_result,
            differential_sparams=diff_result,
            differential_z=diff_z,
            work_dir=work_dir,
            touchstone_path=touchstone_path,
            command=command,
            geometry_check=geometry_check,
            error=error,
        )
        self._write_result_summary(result)
        return result

    def evaluate_geometry_batch(
        self,
        geometries: list[TransformerSpec] | tuple[TransformerSpec, ...],
        run_emx: bool = True,
    ) -> list[TransformerEvalResult]:
        geometry_list = list(geometries)
        if not geometry_list:
            return []
        if not run_emx or len(geometry_list) == 1:
            return [self.evaluate_geometry(geometry, run_emx=run_emx) for geometry in geometry_list]
        if self.run_config.emx.uses_remote_ssh():
            return [self.evaluate_geometry(geometry, run_emx=True) for geometry in geometry_list]

        records: list[tuple[TransformerSpec, str, Path, dict[str, object] | None]] = []
        results_by_key: dict[str, TransformerEvalResult] = {}
        batch_exports: list[CadenceRoundtripExport] = []

        for index, geometry in enumerate(geometry_list):
            key = self.cache_key(geometry)
            work_dir = self.eval_dir / key
            unique_top_cell = f"{self.run_config.emx.top_cell_prefix}_{index:03d}_{key[:8]}"
            layout, error, geometry_check = self._evaluate_export(
                geometry,
                work_dir=work_dir,
                top_cell_override=unique_top_cell,
            )
            records.append((geometry, key, work_dir, geometry_check))
            if error is not None or layout is None:
                result = TransformerEvalResult(
                    cache_key=key,
                    geometry=geometry,
                    target=self.run_config.target,
                    layout=layout,
                    metrics=None,
                    objective=None,
                    single_ended_sparams=None,
                    differential_sparams=None,
                    differential_z=None,
                    work_dir=work_dir,
                    touchstone_path=None,
                    command=None,
                    geometry_check=geometry_check,
                    error=error or "layout export failed without returning artifacts",
                )
                results_by_key[key] = result
                self._write_result_summary(result)
                continue
            batch_exports.append(
                CadenceRoundtripExport(
                    cache_key=key,
                    geometry=geometry,
                    work_dir=work_dir,
                    layout=layout,
                )
            )

        if batch_exports:
            payloads = run_transformer_zeus_cadence_roundtrip_batch(
                run_config=self.run_config,
                exports=tuple(batch_exports),
                stop_after="emx",
                cadence_install_root=self.run_config.emx.cadence_install_root,
                pdk_cds_lib=self.run_config.emx.cadence_pdk_cds_lib,
                tech_lib_name=self.run_config.emx.cadence_tech_lib,
                layer_map_path=self.run_config.emx.cadence_layer_map,
            )
            for geometry, key, work_dir, geometry_check in records:
                if key in results_by_key:
                    continue
                payload = payloads[key]
                result = result_from_roundtrip_payload(
                    payload=payload,
                    geometry=geometry,
                    run_config=self.run_config,
                    work_dir=work_dir,
                    cache_key=key,
                    geometry_check=geometry_check,
                )
                if result.error is None and result.differential_sparams is not None and result.differential_z is not None:
                    self._write_diff_analysis(
                        work_dir=work_dir,
                        differential_sparams=result.differential_sparams,
                        differential_z=result.differential_z,
                    )
                    if not _env_flag("RFIC_SKIP_LUMPED_COMPARE"):
                        try:
                            self._render_lumped_compare(work_dir=work_dir, differential_sparams=result.differential_sparams)
                        except Exception as exc:  # pragma: no cover - environment-dependent plotting stack
                            logger.warning("Skipping lumped compare render for %s: %s", work_dir, exc)
                results_by_key[key] = result
                self._write_result_summary(result)

        return [results_by_key[self.cache_key(geometry)] for geometry in geometry_list]

    def compare_touchstone(self, touchstone_path: Path, out_dir: Path | None = None) -> Path:
        out_dir = Path(out_dir) if out_dir is not None else Path(touchstone_path).resolve().parent
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_result = load_touchstone(touchstone_path)
        prepared = prepare_transformer_touchstone_result(
            raw_result=raw_result,
            target=self.run_config.target,
            raw_touchstone_path=touchstone_path,
            out_dir=out_dir,
            differential_port_pairs=self.run_config.emx.differential_port_pairs,
            ground_unused_s8p_ports=self.run_config.emx.ground_unused_s8p_ports,
        )
        diff_result = prepared["differential_result"]
        return self._render_lumped_compare(work_dir=out_dir, differential_sparams=diff_result)

    def _write_diff_analysis(self, work_dir: Path, differential_sparams, differential_z: np.ndarray) -> None:
        np.savez_compressed(
            work_dir / "differential_analysis.npz",
            freqs_hz=differential_sparams.freqs_hz,
            s_diff=differential_sparams.s_matrix,
            z_diff=differential_z,
        )

    def _render_lumped_compare(self, work_dir: Path, differential_sparams) -> Path:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from ..analysis.extraction import build_lumped_transformer_sparameters

        out_path = Path(work_dir) / "lumped_compare.png"
        lumped = build_lumped_transformer_sparameters(differential_sparams.freqs_hz, self.run_config.target)
        freq_ghz = differential_sparams.freqs_hz / 1e9
        fig, axes = plt.subplots(2, 1, figsize=(7.0, 6.0), dpi=180, sharex=True)
        for ax, (row, col, title) in zip(
            axes,
            ((0, 0, "Sdd11"), (1, 0, "Sdd21")),
        ):
            emx_mag = 20.0 * np.log10(np.maximum(np.abs(differential_sparams.s_matrix[:, row, col]), 1e-12))
            lumped_mag = 20.0 * np.log10(np.maximum(np.abs(lumped.s_matrix[:, row, col]), 1e-12))
            ax.plot(freq_ghz, emx_mag, label="EMX", linewidth=1.8)
            ax.plot(freq_ghz, lumped_mag, label="Lumped", linewidth=1.4, linestyle="--")
            ax.set_ylabel("Mag (dB)")
            ax.set_title(title)
            ax.grid(True, alpha=0.3)
        axes[1].set_xlabel("Frequency (GHz)")
        axes[0].legend(loc="best")
        fig.tight_layout()
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        return out_path
