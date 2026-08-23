"""Optimizer backend implementations for transformer search."""

from __future__ import annotations

import importlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
import time
from time import perf_counter
from typing import Any, Callable

import numpy as np
from scipy.stats import qmc

from ..core import TransformerOptimizationAdapter
from ..core.topology import TransformerSpec
from ..core.types import (
    TransformerEvalResult,
    TransformerLayoutExport,
    TransformerMetrics,
    TransformerObjectiveBreakdown,
)
from ..execution.evaluator import TransformerEmxEvaluator
from ..execution.serialization import _json_default


_PENALTY_COST = 1.0e6
_CHECKPOINT_FORMAT_VERSION = 1


class _OptimizationCancelled(RuntimeError):
    """Internal control-flow exception used to stop long optimization loops."""


def _complex_from_json(value: object) -> complex:
    if isinstance(value, dict) and {"real", "imag"}.issubset(value):
        return complex(float(value.get("real", 0.0)), float(value.get("imag", 0.0)))
    return complex(value)  # type: ignore[arg-type]


def _geometry_from_flat_dict(bounds_model, flat_payload: dict[str, object]) -> TransformerSpec:
    return TransformerSpec.from_flat_dict(
        dict(flat_payload),
        topology_mode=bounds_model.topology_mode,
        primary_turns=bounds_model.primary_turns,
        secondary_turns=bounds_model.secondary_turns,
        primary_center_tap=bounds_model.primary_center_tap,
        secondary_center_tap=bounds_model.secondary_center_tap,
        primary_spacing_um=bounds_model.midpoint().primary_spacing_um,
        secondary_spacing_um=bounds_model.midpoint().secondary_spacing_um,
        primary_bridge_layer=bounds_model.primary.bridge_layer,
        secondary_bridge_layer=bounds_model.secondary.bridge_layer,
        primary_bridge_via_layer=bounds_model.primary.bridge_via_layer,
        secondary_bridge_via_layer=bounds_model.secondary.bridge_via_layer,
        primary_bridge_lower_layer=bounds_model.primary.bridge_lower_layer,
        secondary_bridge_lower_layer=bounds_model.secondary.bridge_lower_layer,
        primary_bridge_lower_via_layer=bounds_model.primary.bridge_lower_via_layer,
        secondary_bridge_lower_via_layer=bounds_model.secondary.bridge_lower_via_layer,
        primary_bridge_section=bounds_model.primary.bridge_section_spec(),
        secondary_bridge_section=bounds_model.secondary.bridge_section_spec(),
        primary_vdd_bar=bounds_model.primary.vdd_bar,
        secondary_vdd_bar=bounds_model.secondary.vdd_bar,
        shield=bounds_model.shield,
    )


def _result_to_checkpoint_payload(result: TransformerEvalResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "cache_key": str(result.cache_key),
        "geometry": result.geometry.flat_dict(),
        "work_dir": str(result.work_dir),
        "touchstone_path": None if result.touchstone_path is None else str(result.touchstone_path),
        "command": None if result.command is None else list(result.command),
        "geometry_check": result.geometry_check,
        "error": result.error,
    }
    if result.layout is not None:
        payload["layout"] = {
            "gds_path": str(result.layout.gds_path),
            "manifest_path": str(result.layout.manifest_path),
            "preview_path": str(result.layout.preview_path),
            "debug_preview_path": str(result.layout.debug_preview_path),
            "top_cell": str(result.layout.top_cell),
        }
    if result.metrics is not None:
        payload["metrics"] = result.metrics.as_dict()
    if result.objective is not None:
        payload["objective"] = result.objective.as_dict()
    return payload


def _result_from_checkpoint_payload(
    payload: dict[str, object],
    *,
    bounds_model,
    target,
) -> TransformerEvalResult:
    geometry_payload = dict(payload.get("geometry", {}) or {})
    geometry = _geometry_from_flat_dict(bounds_model, geometry_payload)

    layout_payload = dict(payload.get("layout", {}) or {})
    layout = None
    if layout_payload:
        layout = TransformerLayoutExport(
            gds_path=Path(str(layout_payload["gds_path"])),
            manifest_path=Path(str(layout_payload["manifest_path"])),
            preview_path=Path(str(layout_payload["preview_path"])),
            debug_preview_path=Path(str(layout_payload["debug_preview_path"])),
            top_cell=str(layout_payload["top_cell"]),
        )

    metrics_payload = dict(payload.get("metrics", {}) or {})
    metrics = None
    if metrics_payload:
        z_diff_center = metrics_payload.get("z_diff_center")
        metrics = TransformerMetrics(
            center_frequency_hz=float(metrics_payload["center_frequency_hz"]),
            lp_h=float(metrics_payload["lp_h"]),
            ls_h=float(metrics_payload["ls_h"]),
            mutual_h=float(metrics_payload["mutual_h"]),
            k=float(metrics_payload["k"]),
            q_primary=float(metrics_payload["q_primary"]),
            q_secondary=float(metrics_payload["q_secondary"]),
            real_z11_ohm=float(metrics_payload["real_z11_ohm"]),
            real_z22_ohm=float(metrics_payload["real_z22_ohm"]),
            z_diff_center=tuple(
                tuple(_complex_from_json(value) for value in row)
                for row in (z_diff_center or ((0j, 0j), (0j, 0j)))
            ),
        )

    objective_payload = dict(payload.get("objective", {}) or {})
    objective = None
    if objective_payload:
        objective = TransformerObjectiveBreakdown(
            lp_rel_error=float(objective_payload["lp_rel_error"]),
            ls_rel_error=float(objective_payload["ls_rel_error"]),
            k_rel_error=float(objective_payload["k_rel_error"]),
            primary_term=float(objective_payload["primary_term"]),
            q_reward=float(objective_payload["q_reward"]),
            total_cost=float(objective_payload["total_cost"]),
            q_target_term=float(objective_payload.get("q_target_term", 0.0)),
            q_primary_rel_error=(
                None
                if objective_payload.get("q_primary_rel_error") is None
                else float(objective_payload["q_primary_rel_error"])
            ),
            q_secondary_rel_error=(
                None
                if objective_payload.get("q_secondary_rel_error") is None
                else float(objective_payload["q_secondary_rel_error"])
            ),
        )

    command_payload = payload.get("command")
    command = None if command_payload is None else [str(item) for item in command_payload]
    touchstone_path = payload.get("touchstone_path")
    return TransformerEvalResult(
        cache_key=str(payload["cache_key"]),
        geometry=geometry,
        target=target,
        layout=layout,
        metrics=metrics,
        objective=objective,
        single_ended_sparams=None,
        differential_sparams=None,
        differential_z=None,
        work_dir=Path(str(payload["work_dir"])),
        touchstone_path=None if touchstone_path is None else Path(str(touchstone_path)),
        command=command,
        geometry_check=dict(payload.get("geometry_check", {}) or {}) or None,
        error=None if payload.get("error") is None else str(payload.get("error")),
    )


def _require_module(import_name: str, package_name: str):
    try:
        return importlib.import_module(import_name)
    except ImportError as exc:  # pragma: no cover - exercised by dependency-gated tests
        raise RuntimeError(
            f"Optimizer dependency missing: install package '{package_name}' to use import '{import_name}'."
        ) from exc


def _invalid_geometry_penalty(result: TransformerEvalResult) -> float | None:
    geometry_check = result.geometry_check
    if not geometry_check or bool(geometry_check.get("ok", True)):
        return None
    metrics = geometry_check.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    errors = geometry_check.get("errors", ())
    if not isinstance(errors, (list, tuple)):
        errors = ()

    penalty = _PENALTY_COST
    penalty += 1.0e4 * float(len(errors))

    violation_terms = (
        "same_layer_spacing_violations",
        "primary_feed_clearance_violations",
        "secondary_feed_clearance_violations",
        "primary_to_secondary_bridge_feed_clearance_violations",
        "primary_intermediate_bridge_pad_clearance_violations",
        "layout_export_geometry_failures",
    )
    penalty += 1.0e3 * sum(float(metrics.get(name, 0) or 0) for name in violation_terms)

    component_penalty = 0.0
    for name in ("primary_conductive_components", "secondary_conductive_components"):
        count = float(metrics.get(name, 1) or 1)
        component_penalty += abs(count - 1.0)
    penalty += 1.0e3 * component_penalty

    # Add a small deterministic geometry-dependent term so invalid candidates are not all identical.
    tie_break = 0.0
    for index, value in enumerate(result.geometry.flat_dict().values(), start=1):
        if isinstance(value, bool):
            numeric = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            numeric = float(value)
        else:
            continue
        tie_break += index * abs(numeric)
    penalty += 1.0e-3 * tie_break
    return penalty


@dataclass
class _TuRBOState:
    dim: int
    batch_size: int
    length: float
    length_min: float
    length_max: float
    success_tolerance: int
    best_value: float = -float("inf")
    failure_counter: int = 0
    failure_tolerance: int = 1
    success_counter: int = 0
    restart_triggered: bool = False

    def __post_init__(self) -> None:
        self.failure_tolerance = max(1, math.ceil(max(4.0 / self.batch_size, float(self.dim) / self.batch_size)))

    def update(self, y_next: float) -> "_TuRBOState":
        threshold = self.best_value + 1e-3 * abs(self.best_value)
        if y_next > threshold:
            self.success_counter += 1
            self.failure_counter = 0
        else:
            self.success_counter = 0
            self.failure_counter += 1

        if self.success_counter >= self.success_tolerance:
            self.length = min(2.0 * self.length, self.length_max)
            self.success_counter = 0
        elif self.failure_counter >= self.failure_tolerance:
            self.length *= 0.5
            self.failure_counter = 0

        self.best_value = max(self.best_value, y_next)
        if self.length < self.length_min:
            self.restart_triggered = True
        return self


class _OptimizerBackend:
    """Shared helper logic for all transformer optimizer backends."""

    backend_name = "unknown"

    def __init__(
        self,
        evaluator: TransformerEmxEvaluator,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
    ):
        self.evaluator = evaluator
        self.bounds_model = evaluator.run_config.bounds
        self.geometry_adapter = TransformerOptimizationAdapter(self.bounds_model)
        self.bounds = self.bounds_model.to_scipy_bounds()
        self.lower = np.array([bound[0] for bound in self.bounds], dtype=float)
        self.upper = np.array([bound[1] for bound in self.bounds], dtype=float)
        self.span = self.upper - self.lower
        self.optimizer_cfg = evaluator.run_config.optimizer
        self.best_result: TransformerEvalResult | None = None
        self.total_evaluation_count = 0
        self.unique_cache_keys: set[str] = set()
        self._warm_results: list[TransformerEvalResult] | None = None
        self._result_history: list[TransformerEvalResult] = []
        self._start_time = perf_counter()
        self._progress_callback = progress_callback
        self._should_stop = should_stop
        self._should_pause = should_pause
        self._checkpoint_path = Path(self.evaluator.root_dir) / "optimization_checkpoint.json"
        self._last_checkpoint_evaluation_count = -1
        self._checkpoint_loaded = False
        self._resume_backend_state: dict[str, object] = {}
        self._imported_warm_start_geometries: list[TransformerSpec] | None = None
        if bool(self.optimizer_cfg.resume_from_checkpoint):
            self._load_checkpoint_if_available()

    def optimize(self) -> TransformerEvalResult:
        cancelled = False
        if len(self.bounds) == 0:
            metadata = {"termination_reason": "no_optimizable_variables", "evaluation_count": 0}
        else:
            try:
                metadata = self._optimize_impl()
            except _OptimizationCancelled:
                cancelled = True
                metadata = {"termination_reason": "cancelled"}
        if self.best_result is None:
            midpoint = self.bounds_model.midpoint()
            self.best_result = self.evaluator.evaluate_geometry(midpoint, run_emx=True)
        metadata = dict(metadata)
        metadata["cancelled"] = bool(cancelled)
        metadata["resumed_from_checkpoint"] = bool(self._checkpoint_loaded)
        metadata["checkpoint_path"] = str(self._checkpoint_path)
        self._maybe_write_checkpoint(force=True)
        self.write_summary(metadata)
        return self.best_result

    def _optimize_impl(self) -> dict[str, Any]:
        raise NotImplementedError

    def remaining_budget(self) -> int:
        return max(0, int(self.optimizer_cfg.max_evaluations) - int(self.total_evaluation_count))

    def clip_vector(self, vector: np.ndarray | list[float] | tuple[float, ...]) -> np.ndarray:
        return np.clip(np.asarray(vector, dtype=float), self.lower, self.upper)

    def geometry_from_vector(self, vector: np.ndarray | list[float] | tuple[float, ...]) -> TransformerSpec:
        return self.geometry_adapter.from_vector(np.asarray(vector, dtype=float))

    def normalize_vector(self, vector: np.ndarray | list[float] | tuple[float, ...]) -> np.ndarray:
        clipped = self.clip_vector(vector)
        return (clipped - self.lower) / np.where(self.span > 0.0, self.span, 1.0)

    def denormalize_vector(self, unit_vector: np.ndarray | list[float] | tuple[float, ...]) -> np.ndarray:
        unit = np.clip(np.asarray(unit_vector, dtype=float), 0.0, 1.0)
        return self.lower + unit * self.span

    def evaluate_result(self, vector: np.ndarray | list[float] | tuple[float, ...]) -> TransformerEvalResult:
        return self.evaluate_results([vector])[0]

    def evaluate_results(
        self,
        vectors: list[np.ndarray | list[float] | tuple[float, ...]] | tuple[np.ndarray | list[float] | tuple[float, ...], ...],
    ) -> list[TransformerEvalResult]:
        self._check_cancelled()
        self._wait_if_paused()
        vector_list = list(vectors)
        if not vector_list:
            return []
        geometry_list = [self.geometry_from_vector(vector) for vector in vector_list]
        self.total_evaluation_count += len(geometry_list)

        ordered_results: list[TransformerEvalResult] = []
        if hasattr(self.evaluator, "evaluate_geometry_batch") and hasattr(self.evaluator, "cache_key"):
            unique_geometries: list[TransformerSpec] = []
            geometry_by_key: dict[str, TransformerSpec] = {}
            ordered_keys: list[str] = []
            for geometry in geometry_list:
                key = self.evaluator.cache_key(geometry)
                ordered_keys.append(key)
                if key not in geometry_by_key:
                    geometry_by_key[key] = geometry
                    unique_geometries.append(geometry)
            unique_results = self.evaluator.evaluate_geometry_batch(unique_geometries, run_emx=True)
            result_by_key = {result.cache_key: result for result in unique_results}
            ordered_results = [result_by_key[key] for key in ordered_keys]
        else:
            ordered_results = [self.evaluator.evaluate_geometry(geometry, run_emx=True) for geometry in geometry_list]

        for result in ordered_results:
            self._wait_if_paused()
            self.unique_cache_keys.add(result.cache_key)
            self._result_history.append(result)
            is_best = False
            if self.best_result is None or self.cost_from_result(result) < self.cost_from_result(self.best_result):
                self.best_result = result
                is_best = True
            self._emit_progress(result, is_best=is_best)
            self._maybe_write_checkpoint()
        return ordered_results

    def objective_cost(self, vector: np.ndarray | list[float] | tuple[float, ...]) -> float:
        return self.cost_from_result(self.evaluate_result(vector))

    def objective_reward(self, vector: np.ndarray | list[float] | tuple[float, ...]) -> float:
        return -self.objective_cost(vector)

    def cost_from_result(self, result: TransformerEvalResult | None) -> float:
        if result is None:
            return _PENALTY_COST
        invalid_geometry_penalty = _invalid_geometry_penalty(result)
        if invalid_geometry_penalty is not None:
            return invalid_geometry_penalty
        if result.objective is None or result.error is not None:
            return _PENALTY_COST
        return float(result.objective.total_cost)

    def run_warm_start(self) -> list[TransformerEvalResult]:
        if self._warm_results is not None:
            return self._warm_results
        self._wait_if_paused()
        seeded_results: list[TransformerEvalResult] = []
        warm_geometries = self._warm_start_geometries()
        if warm_geometries:
            warm_vectors = [self.geometry_adapter.to_vector(geometry) for geometry in warm_geometries]
            seeded_results.extend(self.evaluate_results(warm_vectors))
        count = min(max(0, int(self.optimizer_cfg.warm_start_samples)), self.remaining_budget())
        if count <= 0:
            self._warm_results = seeded_results
            return self._warm_results
        sampler = qmc.LatinHypercube(d=len(self.bounds), seed=self.optimizer_cfg.seed)
        unit = sampler.random(n=count)
        vectors = qmc.scale(unit, self.lower, self.upper)
        vectors[0, :] = self.geometry_adapter.to_vector(self.bounds_model.midpoint())
        seen_cache_keys = {result.cache_key for result in seeded_results}
        unique_vectors: list[np.ndarray] = []
        for vector in list(vectors):
            geometry = self.geometry_from_vector(vector)
            cache_key = self.evaluator.cache_key(geometry) if hasattr(self.evaluator, "cache_key") else repr(tuple(vector))
            if cache_key in seen_cache_keys:
                continue
            seen_cache_keys.add(cache_key)
            unique_vectors.append(np.asarray(vector, dtype=float))
        random_results = self.evaluate_results(unique_vectors) if unique_vectors else []
        self._warm_results = [*seeded_results, *random_results]
        return self._warm_results

    def best_start_geometry(self) -> TransformerSpec:
        warm_results = self.run_warm_start()
        if warm_results:
            return min(warm_results, key=self.cost_from_result).geometry
        return self.bounds_model.midpoint()

    def write_summary(self, metadata: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "optimizer_name": self.backend_name,
            "optimizer_config": asdict(self.optimizer_cfg),
            "best_result": self.best_result.summary_dict() if self.best_result is not None else None,
            "total_evaluation_count": int(self.total_evaluation_count),
            "unique_evaluation_count": int(len(self.unique_cache_keys)),
            "wall_clock_seconds": float(perf_counter() - self._start_time),
            "cancelled": bool(metadata.get("cancelled", False)),
            "checkpoint_path": str(self._checkpoint_path),
            "resumed_from_checkpoint": bool(self._checkpoint_loaded),
        }
        payload[self.backend_name] = metadata
        out_path = Path(self.evaluator.root_dir) / "optimization_summary.json"
        out_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")

    def _checkpoint_backend_state(self) -> dict[str, object]:
        return {}

    def _checkpoint_validation_payload(self) -> dict[str, object]:
        return {
            "format_version": _CHECKPOINT_FORMAT_VERSION,
            "optimizer_name": self.backend_name,
            "field_order": list(self.geometry_adapter.field_order()),
            "topology_mode": str(self.bounds_model.topology_mode),
        }

    def _load_checkpoint_if_available(self) -> None:
        if not self._checkpoint_path.exists():
            return
        payload = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
        if int(payload.get("format_version", -1)) != _CHECKPOINT_FORMAT_VERSION:
            raise ValueError(f"Unsupported optimization checkpoint format in {self._checkpoint_path}")
        if str(payload.get("optimizer_name")) != self.backend_name:
            raise ValueError(
                f"Checkpoint backend '{payload.get('optimizer_name')}' does not match current backend '{self.backend_name}'"
            )
        if list(payload.get("field_order", ())) != list(self.geometry_adapter.field_order()):
            raise ValueError("Checkpoint search-space fields do not match the active optimizer search space")
        if str(payload.get("topology_mode")) != str(self.bounds_model.topology_mode):
            raise ValueError("Checkpoint topology mode does not match the active optimizer search space")

        result_history_payload = list(payload.get("result_history", ()) or ())
        self._result_history = [
            _result_from_checkpoint_payload(item, bounds_model=self.bounds_model, target=self.evaluator.run_config.target)
            for item in result_history_payload
        ]
        warm_results_payload = payload.get("warm_results")
        if warm_results_payload is not None:
            self._warm_results = [
                _result_from_checkpoint_payload(item, bounds_model=self.bounds_model, target=self.evaluator.run_config.target)
                for item in list(warm_results_payload or ())
            ]
        best_payload = payload.get("best_result")
        if isinstance(best_payload, dict):
            self.best_result = _result_from_checkpoint_payload(
                best_payload,
                bounds_model=self.bounds_model,
                target=self.evaluator.run_config.target,
            )
        elif self._result_history:
            self.best_result = min(self._result_history, key=self.cost_from_result)
        self.total_evaluation_count = int(payload.get("total_evaluation_count", len(self._result_history)))
        self.unique_cache_keys = {str(key) for key in list(payload.get("unique_cache_keys", ()))}
        if not self.unique_cache_keys:
            self.unique_cache_keys = {result.cache_key for result in self._result_history}
        self._resume_backend_state = dict(payload.get("backend_state", {}) or {})
        self._checkpoint_loaded = True
        self._last_checkpoint_evaluation_count = self.total_evaluation_count

    def _maybe_write_checkpoint(self, *, force: bool = False) -> None:
        interval = max(0, int(self.optimizer_cfg.checkpoint_interval_evaluations))
        if not force:
            if interval <= 0:
                return
            if self.total_evaluation_count == self._last_checkpoint_evaluation_count:
                return
            if (self.total_evaluation_count % interval) != 0:
                return
        payload: dict[str, object] = {
            **self._checkpoint_validation_payload(),
            "total_evaluation_count": int(self.total_evaluation_count),
            "unique_cache_keys": sorted(self.unique_cache_keys),
            "best_result": None if self.best_result is None else _result_to_checkpoint_payload(self.best_result),
            "warm_results": None
            if self._warm_results is None
            else [_result_to_checkpoint_payload(result) for result in self._warm_results],
            "result_history": [_result_to_checkpoint_payload(result) for result in self._result_history],
            "backend_state": self._checkpoint_backend_state(),
        }
        self._checkpoint_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        self._last_checkpoint_evaluation_count = self.total_evaluation_count

    def _load_warm_start_source_geometry(self, raw_path: str) -> TransformerSpec:
        source_path = Path(raw_path).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(f"Warm-start source does not exist: {source_path}")
        if source_path.is_dir():
            for candidate in (
                source_path / "optimization_summary.json",
                source_path / "summary.json",
            ):
                if candidate.exists():
                    source_path = candidate
                    break
            else:
                raise FileNotFoundError(
                    f"Warm-start source directory must contain optimization_summary.json or summary.json: {source_path}"
                )

        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if isinstance(payload.get("best_result"), dict):
            geometry_payload = dict(payload["best_result"].get("geometry", {}) or {})
        else:
            geometry_payload = dict(payload.get("geometry", {}) or {})
        if not geometry_payload:
            raise ValueError(f"Warm-start source does not contain geometry data: {source_path}")
        return _geometry_from_flat_dict(self.bounds_model, geometry_payload)

    def _warm_start_geometries(self) -> list[TransformerSpec]:
        if self._imported_warm_start_geometries is not None:
            return list(self._imported_warm_start_geometries)
        geometries: list[TransformerSpec] = []
        seen_cache_keys: set[str] = set()
        for raw_path in self.optimizer_cfg.warm_start_paths:
            geometry = self._load_warm_start_source_geometry(str(raw_path))
            cache_key = self.evaluator.cache_key(geometry) if hasattr(self.evaluator, "cache_key") else repr(geometry.flat_dict())
            if cache_key in seen_cache_keys:
                continue
            seen_cache_keys.add(cache_key)
            geometries.append(geometry)
        self._imported_warm_start_geometries = geometries
        return list(self._imported_warm_start_geometries)

    def _check_cancelled(self) -> None:
        if self._should_stop is not None and bool(self._should_stop()):
            raise _OptimizationCancelled("Optimization cancelled")

    def _wait_if_paused(self) -> None:
        while self._should_pause is not None and bool(self._should_pause()):
            self._check_cancelled()
            time.sleep(0.1)

    def _emit_progress(self, result: TransformerEvalResult, *, is_best: bool) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(
            {
                "backend_name": self.backend_name,
                "evaluation_count": int(self.total_evaluation_count),
                "unique_evaluation_count": int(len(self.unique_cache_keys)),
                "elapsed_seconds": float(perf_counter() - self._start_time),
                "is_best": bool(is_best),
                "cost": float(self.cost_from_result(result)),
                "result": result,
            }
        )


class _CMAESBackend(_OptimizerBackend):
    backend_name = "cma_es"

    def _best_candidate_snapshot(self, es, fallback_mean: np.ndarray | list[float] | tuple[float, ...]) -> tuple[np.ndarray, float]:
        candidate_vector = getattr(getattr(es, "result", None), "xbest", None)
        if candidate_vector is None:
            if self.best_result is not None:
                candidate_vector = self.geometry_adapter.to_vector(self.best_result.geometry)
            else:
                candidate_vector = np.asarray(fallback_mean, dtype=float)
        candidate_cost = getattr(getattr(es, "result", None), "fbest", None)
        if candidate_cost is None or not np.isfinite(float(candidate_cost)):
            candidate_cost = self.cost_from_result(self.best_result)
        return np.asarray(candidate_vector, dtype=float), float(candidate_cost)

    def _checkpoint_backend_state(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if hasattr(self, "_resume_mean"):
            payload["current_mean"] = list(map(float, getattr(self, "_resume_mean")))
        if hasattr(self, "_resume_sigma"):
            payload["current_sigma"] = float(getattr(self, "_resume_sigma"))
        if hasattr(self, "_resume_generation_count"):
            payload["generation_count"] = int(getattr(self, "_resume_generation_count"))
        if hasattr(self, "_resume_best_candidate_vector"):
            payload["best_candidate_vector"] = list(map(float, getattr(self, "_resume_best_candidate_vector")))
        if hasattr(self, "_resume_best_candidate_cost"):
            payload["best_candidate_cost"] = float(getattr(self, "_resume_best_candidate_cost"))
        return payload

    def _optimize_impl(self) -> dict[str, Any]:
        cma = _require_module("cma", "cma")
        warm_results = self.run_warm_start()
        if self.remaining_budget() <= 0:
            return {"termination_reason": "max_evaluations_reached_after_warm_start", "warm_start_count": len(warm_results)}
        if warm_results:
            initial_mean = self.geometry_adapter.to_vector(min(warm_results, key=self.cost_from_result).geometry)
        else:
            initial_mean = self.geometry_adapter.to_vector(self.bounds_model.midpoint())
        resume_state = dict(self._resume_backend_state or {})
        if resume_state.get("current_mean") is not None:
            initial_mean = np.asarray(resume_state["current_mean"], dtype=float)
        sigma0 = self.optimizer_cfg.cma_es.sigma0
        if sigma0 is None:
            sigma0 = 0.25 * float(np.median(self.span))
        if resume_state.get("current_sigma") is not None:
            sigma0 = float(resume_state["current_sigma"])

        options: dict[str, Any] = {
            "bounds": [self.lower.tolist(), self.upper.tolist()],
            "seed": int(self.optimizer_cfg.seed),
            "verbose": int(self.optimizer_cfg.cma_es.verbose),
        }
        if self.optimizer_cfg.cma_es.population_size is not None:
            options["popsize"] = int(self.optimizer_cfg.cma_es.population_size)

        es = cma.CMAEvolutionStrategy(initial_mean.tolist(), float(sigma0), options)
        self._resume_mean = np.asarray(initial_mean, dtype=float)
        self._resume_sigma = float(sigma0)
        self._resume_generation_count = int(resume_state.get("generation_count", 0))
        self._resume_best_candidate_vector = np.asarray(
            resume_state.get("best_candidate_vector", initial_mean),
            dtype=float,
        )
        self._resume_best_candidate_cost = float(resume_state.get("best_candidate_cost", float("inf")))
        termination_reason = "package_converged"
        while not es.stop() and self.remaining_budget() > 0:
            self._check_cancelled()
            self._wait_if_paused()
            min_batch_size = max(4, int(getattr(es.sp, "mu", 1)))
            batch_size = min(int(es.sp.popsize), self.remaining_budget())
            if batch_size < min_batch_size:
                termination_reason = "max_evaluations_reached"
                break
            batch = es.ask(batch_size)
            if not batch:
                termination_reason = "empty_candidate_batch"
                break
            results = self.evaluate_results(batch)
            scores = [self.cost_from_result(result) for result in results]
            es.tell(batch, scores)
            self._resume_generation_count += 1
            self._resume_mean = np.asarray(es.mean, dtype=float)
            self._resume_sigma = float(getattr(es, "sigma", sigma0))
            best_candidate_vector, best_candidate_cost = self._best_candidate_snapshot(es, es.mean)
            self._resume_best_candidate_vector = best_candidate_vector
            self._resume_best_candidate_cost = best_candidate_cost
            self._maybe_write_checkpoint()

        stop_dict = dict(es.stop())
        if self.remaining_budget() <= 0:
            termination_reason = "max_evaluations_reached"
        elif stop_dict:
            termination_reason = ",".join(sorted(stop_dict.keys()))

        best_candidate_vector, best_candidate_cost = self._best_candidate_snapshot(es, es.mean)
        return {
            "evaluation_count": int(self.total_evaluation_count),
            "initial_mean": list(map(float, initial_mean)),
            "final_mean": list(map(float, es.mean)),
            "best_candidate_vector": list(map(float, best_candidate_vector)),
            "best_candidate_cost": float(best_candidate_cost),
            "sigma0": float(sigma0),
            "population_size": int(es.sp.popsize),
            "termination_reason": termination_reason,
            "stop_dict": stop_dict,
        }


class _TuRBOBackend(_OptimizerBackend):
    backend_name = "turbo"

    def _checkpoint_backend_state(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if hasattr(self, "_checkpoint_turbo_iterations"):
            payload["iterations"] = int(getattr(self, "_checkpoint_turbo_iterations"))
        if hasattr(self, "_checkpoint_turbo_x_data"):
            payload["x_data"] = np.asarray(getattr(self, "_checkpoint_turbo_x_data"), dtype=float)
        if hasattr(self, "_checkpoint_turbo_y_data"):
            payload["y_data"] = np.asarray(getattr(self, "_checkpoint_turbo_y_data"), dtype=float)
        if hasattr(self, "_checkpoint_turbo_state"):
            payload["trust_region_state"] = dict(getattr(self, "_checkpoint_turbo_state"))
        return payload

    def _optimize_impl(self) -> dict[str, Any]:
        torch = _require_module("torch", "torch")
        gpytorch = _require_module("gpytorch", "gpytorch")
        _require_module("botorch", "botorch")

        from botorch.acquisition import qExpectedImprovement
        from botorch.models import SingleTaskGP
        from botorch.optim import optimize_acqf
        from gpytorch.constraints import Interval
        from gpytorch.kernels import MaternKernel, ScaleKernel
        from gpytorch.likelihoods import GaussianLikelihood
        from gpytorch.mlls import ExactMarginalLogLikelihood

        try:
            from botorch.fit import fit_gpytorch_mll as _fit_gp
        except ImportError:  # pragma: no cover - depends on installed botorch version
            from botorch.fit import fit_gpytorch_model as _fit_gp

        torch.manual_seed(int(self.optimizer_cfg.seed))
        dtype = torch.double
        device = torch.device("cpu")
        resume_state = dict(self._resume_backend_state or {})
        restored_x = resume_state.get("x_data")
        restored_y = resume_state.get("y_data")
        turbo_cfg = self.optimizer_cfg.turbo
        if restored_x is not None and restored_y is not None:
            x_data = np.asarray(restored_x, dtype=float)
            y_data = np.asarray(restored_y, dtype=float)
            if y_data.ndim == 1:
                y_data = y_data.reshape(-1, 1)
            X = torch.tensor(x_data, dtype=dtype, device=device)
            Y = torch.tensor(y_data, dtype=dtype, device=device)
            state_payload = dict(resume_state.get("trust_region_state", {}) or {})
            state = _TuRBOState(
                dim=X.shape[-1],
                batch_size=1,
                length=float(state_payload.get("length", turbo_cfg.initial_length)),
                length_min=float(state_payload.get("length_min", turbo_cfg.length_min)),
                length_max=float(state_payload.get("length_max", turbo_cfg.length_max)),
                success_tolerance=int(state_payload.get("success_tolerance", turbo_cfg.success_tolerance)),
                best_value=float(state_payload.get("best_value", float(Y.max().item()))),
                failure_counter=int(state_payload.get("failure_counter", 0)),
                failure_tolerance=int(state_payload.get("failure_tolerance", 1)),
                success_counter=int(state_payload.get("success_counter", 0)),
                restart_triggered=bool(state_payload.get("restart_triggered", False)),
            )
            iterations = int(resume_state.get("iterations", 0))
        else:
            warm_results = self.run_warm_start()
            if not warm_results and self.remaining_budget() > 0:
                warm_results = [self.evaluate_result(self.geometry_adapter.to_vector(self.bounds_model.midpoint()))]
            if len(warm_results) < 2 and self.remaining_budget() > 0:
                jitter = self.normalize_vector(self.geometry_adapter.to_vector(self.bounds_model.midpoint())) + 0.1
                warm_results.append(self.evaluate_result(self.denormalize_vector(np.mod(jitter, 1.0))))
            if self.remaining_budget() <= 0:
                return {"termination_reason": "max_evaluations_reached_after_warm_start", "warm_start_count": len(warm_results)}

            x_data = np.stack([self.normalize_vector(self.geometry_adapter.to_vector(result.geometry)) for result in warm_results], axis=0)
            y_data = np.array([-self.cost_from_result(result) for result in warm_results], dtype=float).reshape(-1, 1)
            X = torch.tensor(x_data, dtype=dtype, device=device)
            Y = torch.tensor(y_data, dtype=dtype, device=device)
            state = _TuRBOState(
                dim=X.shape[-1],
                batch_size=1,
                length=float(turbo_cfg.initial_length),
                length_min=float(turbo_cfg.length_min),
                length_max=float(turbo_cfg.length_max),
                success_tolerance=int(turbo_cfg.success_tolerance),
                best_value=float(Y.max().item()),
            )
            iterations = 0
        self._checkpoint_turbo_iterations = int(iterations)
        self._checkpoint_turbo_x_data = X.detach().cpu().numpy()
        self._checkpoint_turbo_y_data = Y.detach().cpu().numpy()
        self._checkpoint_turbo_state = {
            "length": float(state.length),
            "length_min": float(state.length_min),
            "length_max": float(state.length_max),
            "success_counter": int(state.success_counter),
            "failure_counter": int(state.failure_counter),
            "success_tolerance": int(state.success_tolerance),
            "failure_tolerance": int(state.failure_tolerance),
            "best_value": float(state.best_value),
            "restart_triggered": bool(state.restart_triggered),
        }
        termination_reason = "trust_region_collapse"

        while not state.restart_triggered and self.remaining_budget() > 0:
            self._check_cancelled()
            self._wait_if_paused()
            iterations += 1
            y_mean = Y.mean()
            y_std = Y.std()
            if float(y_std.item()) < 1.0e-12:
                train_Y = Y - y_mean
            else:
                train_Y = (Y - y_mean) / y_std

            likelihood = GaussianLikelihood(noise_constraint=Interval(1.0e-8, 1.0e-3))
            covar_module = ScaleKernel(
                MaternKernel(
                    nu=2.5,
                    ard_num_dims=state.dim,
                    lengthscale_constraint=Interval(0.005, 4.0),
                )
            )
            model = SingleTaskGP(X, train_Y, covar_module=covar_module, likelihood=likelihood)
            mll = ExactMarginalLogLikelihood(model.likelihood, model)
            with gpytorch.settings.max_cholesky_size(float(turbo_cfg.max_cholesky_size)):
                _fit_gp(mll)

                center_idx = int(torch.argmax(Y).item())
                x_center = X[center_idx, :].clone()
                weights = model.covar_module.base_kernel.lengthscale.squeeze().detach()
                if weights.ndim == 0:
                    weights = weights.view(1)
                weights = weights / weights.mean()
                weights = weights / torch.prod(weights.pow(1.0 / len(weights)))
                tr_lb = torch.clamp(x_center - weights * state.length / 2.0, 0.0, 1.0)
                tr_ub = torch.clamp(x_center + weights * state.length / 2.0, 0.0, 1.0)

                acquisition = qExpectedImprovement(model, best_f=train_Y.max())
                X_next, _ = optimize_acqf(
                    acquisition,
                    bounds=torch.stack([tr_lb, tr_ub]),
                    q=1,
                    num_restarts=int(turbo_cfg.num_restarts),
                    raw_samples=int(turbo_cfg.raw_samples),
                )

            candidate_unit = X_next.squeeze(0).detach().cpu().numpy()
            candidate_vector = self.denormalize_vector(candidate_unit)
            reward = -self.objective_cost(candidate_vector)
            state.update(float(reward))

            X = torch.cat(
                [
                    X,
                    torch.tensor(candidate_unit, dtype=dtype, device=device).unsqueeze(0),
                ],
                dim=0,
            )
            Y = torch.cat(
                [
                    Y,
                    torch.tensor([[reward]], dtype=dtype, device=device),
                ],
                dim=0,
            )
            self._checkpoint_turbo_iterations = int(iterations)
            self._checkpoint_turbo_x_data = X.detach().cpu().numpy()
            self._checkpoint_turbo_y_data = Y.detach().cpu().numpy()
            self._checkpoint_turbo_state = {
                "length": float(state.length),
                "length_min": float(state.length_min),
                "length_max": float(state.length_max),
                "success_counter": int(state.success_counter),
                "failure_counter": int(state.failure_counter),
                "success_tolerance": int(state.success_tolerance),
                "failure_tolerance": int(state.failure_tolerance),
                "best_value": float(state.best_value),
                "restart_triggered": bool(state.restart_triggered),
            }
            self._maybe_write_checkpoint()

        if self.remaining_budget() <= 0:
            termination_reason = "max_evaluations_reached"

        return {
            "evaluation_count": int(self.total_evaluation_count),
            "iterations": int(iterations),
            "termination_reason": termination_reason,
            "trust_region_state": {
                "length": float(state.length),
                "length_min": float(state.length_min),
                "length_max": float(state.length_max),
                "success_counter": int(state.success_counter),
                "failure_counter": int(state.failure_counter),
                "success_tolerance": int(state.success_tolerance),
                "failure_tolerance": int(state.failure_tolerance),
                "best_value": float(state.best_value),
                "restart_triggered": bool(state.restart_triggered),
            },
        }
