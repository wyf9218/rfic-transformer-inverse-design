import importlib.util
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import gdstk
import numpy as np

from rfic_transformer_inverse_design.api import (
    CMAESOptimizerConfig,
    InductorSpec,
    PowerLine8PortSpec,
    SParameterResult,
    TransformerOptimizationAdapter,
    TransformerEmxEvaluator,
    TransformerEvalResult,
    TransformerGeometrySpec,
    TransformerObjectiveBreakdown,
    TransformerOptimizer,
    TransformerOptimizerConfig,
    TuRBOOptimizerConfig,
    build_lumped_transformer_sparameters,
    default_run_config,
    default_target_spec,
    differential_2port_to_4port_s,
    extract_transformer_metrics,
    extract_transformer_metrics_from_differential,
    extract_transformer_metrics_from_single_ended_pairs,
    load_run_config,
    multiport_single_ended_to_differential_z,
    score_transformer_result,
)
from rfic_transformer_inverse_design.layout import export_transformer_layout, run_transformer_gdstk_checks
from rfic_transformer_inverse_design.layout.builders import (
    BridgeEndpointStack,
    BridgePadStage,
    CenterTappedInductorGeometry,
    InductorLayoutSpec,
    InductorTerminals,
    LayerPolygonGroup,
    _build_center_tapped_inductor,
    _build_winding,
)
from rfic_transformer_inverse_design.layout.checks import _generic_same_layer_spacing_checks
from rfic_transformer_inverse_design.network_analysis import s_to_z, z_to_s


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "rfic_transformer_inverse_design" / "interfaces" / "cli.py"


def _write_touchstone(path: Path, freqs_hz: np.ndarray, s_matrix: np.ndarray) -> None:
    path = Path(path)
    n_ports = int(s_matrix.shape[1])
    with path.open("w", encoding="ascii") as handle:
        handle.write(f"! {n_ports}-port synthetic data\n")
        handle.write("# GHz S RI R 50\n")
        for idx, freq_hz in enumerate(freqs_hz):
            values = [f"{freq_hz / 1e9:.12g}"]
            order = [(0, 0), (1, 0), (0, 1), (1, 1)] if n_ports == 2 else [
                (row, col) for row in range(n_ports) for col in range(n_ports)
            ]
            for row, col in order:
                s = complex(s_matrix[idx, row, col])
                values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
            handle.write(" ".join(values) + "\n")


def _load_script_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEMXSimulation:
    run_count = 0
    target = None

    def __init__(self, emx_binary=None, process_file=None, top_cell=None, extra_args=None):
        self.emx_binary = emx_binary
        self.process_file = process_file
        self.top_cell = top_cell
        self.extra_args = extra_args or []
        self.project_dir = None
        self.freq_start_hz = None
        self.freq_stop_hz = None
        self.num_freq_points = None
        self._layout_manifest = None
        self._last_touchstone_path = None

    def create_project(self, path):
        self.project_dir = Path(path)
        self.project_dir.mkdir(parents=True, exist_ok=True)

    def configure_solver(self, solver_type, freq_start_hz, freq_stop_hz, num_freq_points):
        self.freq_start_hz = float(freq_start_hz)
        self.freq_stop_hz = float(freq_stop_hz)
        self.num_freq_points = int(num_freq_points)

    def _build_emx_command(self, layout_path):
        return ["emx", str(layout_path), self.top_cell, self.process_file]

    def connect(self):
        return None

    def run_solver(self):
        type(self).run_count += 1
        freqs = np.linspace(self.freq_start_hz, self.freq_stop_hz, self.num_freq_points)
        diff = build_lumped_transformer_sparameters(
            freqs_hz=freqs,
            target=type(self).target,
            q_primary=18.0,
            q_secondary=16.0,
        )
        if self._layout_manifest is not None and len(self._layout_manifest.ports) == 2:
            touchstone_path = self.project_dir / "emx.s2p"
            _write_touchstone(touchstone_path, diff.freqs_hz, diff.s_matrix)
        else:
            single = differential_2port_to_4port_s(
                freqs_hz=freqs,
                s_diff=diff.s_matrix,
                diff_z0_ohm=type(self).target.differential_reference_impedance_ohm,
                single_z0_ohm=50.0,
            )
            touchstone_path = self.project_dir / "emx.s4p"
            _write_touchstone(touchstone_path, single.freqs_hz, single.s_matrix)
        self._last_touchstone_path = touchstone_path

    def disconnect(self):
        return None


class FakeOptimizerEvaluator:
    def __init__(self, run_config, root_dir: Path, optimum: TransformerGeometrySpec):
        self.run_config = run_config
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.optimum = optimum
        self.adapter = TransformerOptimizationAdapter(run_config.bounds)
        self.batch_calls = 0

    def cache_key(self, geometry: TransformerGeometrySpec) -> str:
        return "|".join(f"{value:.6f}" for value in self.adapter.to_vector(geometry))

    def evaluate_geometry(self, geometry: TransformerGeometrySpec, run_emx: bool = True) -> TransformerEvalResult:
        delta = self.adapter.to_vector(geometry) - self.adapter.to_vector(self.optimum)
        scale = np.maximum(np.abs(self.adapter.to_vector(self.optimum)), 1.0)
        cost = float(np.sum((delta / scale) ** 2))
        cache_key = self.cache_key(geometry)
        objective = TransformerObjectiveBreakdown(
            lp_rel_error=0.0,
            ls_rel_error=0.0,
            k_rel_error=0.0,
            primary_term=cost,
            q_reward=0.0,
            total_cost=cost,
        )
        return TransformerEvalResult(
            cache_key=cache_key,
            geometry=geometry,
            target=self.run_config.target,
            layout=None,
            metrics=None,
            objective=objective,
            single_ended_sparams=None,
            differential_sparams=None,
            differential_z=None,
            work_dir=self.root_dir,
            touchstone_path=None,
            command=None,
            error=None,
        )

    def evaluate_geometry_batch(
        self,
        geometries: list[TransformerGeometrySpec] | tuple[TransformerGeometrySpec, ...],
        run_emx: bool = True,
    ) -> list[TransformerEvalResult]:
        self.batch_calls += 1
        return [self.evaluate_geometry(geometry, run_emx=run_emx) for geometry in geometries]


class TransformerToolboxTestBase(unittest.TestCase):
    @staticmethod
    def _replace_inductor(inductor: InductorSpec, **updates) -> InductorSpec:
        geometry_fields = {
            "outer_width_um",
            "outer_height_um",
            "trace_width_um",
            "spacing_um",
            "terminal_y_span_um",
            "feed_extension_um",
        }
        fixed_fields = {
            "turns",
            "center_tap",
            "bridge_layer",
            "bridge_via_layer",
            "bridge_lower_layer",
            "bridge_lower_via_layer",
            "bridge_section",
            "vdd_bar",
        }
        geometry_updates = {key: value for key, value in updates.items() if key in geometry_fields}
        fixed_updates = {key: value for key, value in updates.items() if key in fixed_fields}
        unknown = set(updates) - geometry_fields - fixed_fields
        if unknown:
            raise ValueError(f"Unknown inductor update fields: {sorted(unknown)}")
        return replace(
            inductor,
            geometry=replace(inductor.geometry, **geometry_updates),
            fixed=replace(inductor.fixed, **fixed_updates),
        )
    def _make_optimum(self, cfg, delta: np.ndarray | dict[str, float] | None = None):
        midpoint = cfg.bounds.midpoint()
        adapter = TransformerOptimizationAdapter(cfg.bounds)
        if delta is None:
            default_shift_by_name = {
                "primary_outer_width_um": 2.0,
                "primary_outer_height_um": 2.0,
                "secondary_outer_width_um": -1.0,
                "secondary_outer_height_um": 1.5,
                "primary_width_um": -0.5,
                "secondary_width_um": 1.0,
                "primary_spacing_um": 0.0,
                "secondary_spacing_um": 0.0,
                "primary_terminal_y_span_um": 6.0,
                "secondary_terminal_y_span_um": -4.0,
                "offset_um": 1.0,
                "primary_feed_extension_um": 1.0,
                "secondary_feed_extension_um": -1.5,
            }
            shift = np.array([default_shift_by_name[name] for name in cfg.bounds.names()], dtype=float)
        elif isinstance(delta, dict):
            shift = np.array([float(delta.get(name, 0.0)) for name in cfg.bounds.names()], dtype=float)
        else:
            shift = np.asarray(delta, dtype=float)
            if shift.shape != adapter.to_vector(midpoint).shape:
                raise ValueError(
                    f"Expected optimum delta shape {adapter.to_vector(midpoint).shape}, got {shift.shape}"
                )
        return adapter.from_vector(adapter.to_vector(midpoint) + shift)
    def _assert_optimizer_smoke(
        self,
        cfg,
        optimizer_name: str,
        *,
        optimum_delta: np.ndarray | None = None,
    ) -> dict[str, object]:
        optimum = self._make_optimum(cfg, delta=optimum_delta)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            optimizer = TransformerOptimizer(evaluator=evaluator)
            baseline = evaluator.evaluate_geometry(cfg.bounds.midpoint())
            result = optimizer.optimize()

            self.assertIsNotNone(result.objective)
            self.assertIsNotNone(baseline.objective)
            self.assertLess(result.objective.total_cost, baseline.objective.total_cost)

            summary_path = Path(tmpdir) / "optimization_summary.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["optimizer_name"], optimizer_name)
            self.assertIn(optimizer_name, summary)
            self.assertIn("optimizer_config", summary)
            return summary


__all__ = [
    "BridgeEndpointStack",
    "BridgePadStage",
    "CMAESOptimizerConfig",
    "CenterTappedInductorGeometry",
    "FakeEMXSimulation",
    "FakeOptimizerEvaluator",
    "InductorLayoutSpec",
    "InductorSpec",
    "InductorTerminals",
    "LayerPolygonGroup",
    "Path",
    "PowerLine8PortSpec",
    "SParameterResult",
    "TransformerEmxEvaluator",
    "TransformerEvalResult",
    "TransformerGeometrySpec",
    "TransformerObjectiveBreakdown",
    "TransformerOptimizationAdapter",
    "TransformerOptimizer",
    "TransformerOptimizerConfig",
    "TransformerToolboxTestBase",
    "TuRBOOptimizerConfig",
    "_build_center_tapped_inductor",
    "_build_winding",
    "_generic_same_layer_spacing_checks",
    "_load_script_module",
    "_write_touchstone",
    "build_lumped_transformer_sparameters",
    "default_run_config",
    "default_target_spec",
    "differential_2port_to_4port_s",
    "extract_transformer_metrics",
    "extract_transformer_metrics_from_differential",
    "extract_transformer_metrics_from_single_ended_pairs",
    "export_transformer_layout",
    "gdstk",
    "io",
    "json",
    "load_run_config",
    "mock",
    "multiport_single_ended_to_differential_z",
    "np",
    "replace",
    "run_transformer_gdstk_checks",
    "s_to_z",
    "score_transformer_result",
    "tempfile",
    "unittest",
    "z_to_s",
]
