"""Qt-based transformer constraint GUI."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import importlib.util
import json
import math
import os
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import gdstk
import matplotlib.image as mpimg
import matplotlib.patheffects as patheffects
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.widgets import Cursor
import numpy as np
from scipy.spatial import Delaunay
import yaml

try:  # pragma: no cover - import availability depends on local environment
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - fallback path
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise SystemExit("PySide6 or PyQt6 is required for the Qt transformer GUI") from exc

try:  # pragma: no cover - import availability depends on local environment
    import pyvista as pv
except ImportError:  # pragma: no cover - optional GUI path
    pv = None

try:  # pragma: no cover - import availability depends on local environment
    import vtk
except ImportError:  # pragma: no cover - optional GUI path
    vtk = None

try:  # pragma: no cover - import availability depends on local environment
    from pyvistaqt import QtInteractor
except ImportError:  # pragma: no cover - optional GUI path
    QtInteractor = None

if __package__ in {None, ""}:  # pragma: no cover - exercised by direct script execution
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

from rfic_transformer_inverse_design.core.defaults import default_run_config, load_run_config, load_run_config_from_raw
from rfic_transformer_inverse_design.layout import export_transformer_layout, run_transformer_gdstk_checks
from rfic_transformer_inverse_design.core import BridgeSectionConfig, TopologyMode, TransformerGeometrySpec, TransformerOptimizationAdapter
from rfic_transformer_inverse_design.execution import TransformerEmxEvaluator
from rfic_transformer_inverse_design.optimize import TransformerOptimizer
from rfic_transformer_inverse_design.paths import bundled_proc_dir, resolve_local_path, runtime_root
from rfic_transformer_inverse_design.process import infer_bridge_route_layers, parse_proc_file
from rfic_transformer_inverse_design.sim.base import SParameterResult
from rfic_transformer_inverse_design.sim.emx.render import layout_preview_extent

QtSignal = getattr(QtCore, "Signal", None)
if QtSignal is None:  # PyQt exposes pyqtSignal instead of Signal.
    QtSignal = getattr(QtCore, "pyqtSignal")


PARAMETER_LABELS: dict[str, str] = {
    "primary_outer_width_um": "Primary outer width",
    "primary_outer_height_um": "Primary outer height",
    "secondary_outer_width_um": "Secondary outer width",
    "secondary_outer_height_um": "Secondary outer height",
    "primary_width_um": "Primary line width",
    "secondary_width_um": "Secondary line width",
    "primary_spacing_um": "Primary turn spacing",
    "secondary_spacing_um": "Secondary turn spacing",
    "primary_terminal_y_span_um": "Primary terminal y-span",
    "secondary_terminal_y_span_um": "Secondary terminal y-span",
    "offset_um": "Primary-secondary offset",
    "primary_feed_extension_um": "Primary feed extension",
    "secondary_feed_extension_um": "Secondary feed extension",
}

BOUND_SLIDER_FIELDS: dict[str, str] = {
    "primary_outer_width_um": "primary_outer_width_um",
    "primary_outer_height_um": "primary_outer_height_um",
    "secondary_outer_width_um": "secondary_outer_width_um",
    "secondary_outer_height_um": "secondary_outer_height_um",
    "primary_width_um": "primary_width_um",
    "primary_spacing_um": "primary_spacing_um",
    "primary_terminal_y_span_um": "primary_terminal_y_span_um",
    "primary_feed_extension_um": "primary_feed_extension_um",
    "secondary_width_um": "secondary_width_um",
    "secondary_spacing_um": "secondary_spacing_um",
    "secondary_terminal_y_span_um": "secondary_terminal_y_span_um",
    "secondary_feed_extension_um": "secondary_feed_extension_um",
    "offset_um": "offset_um",
}

STACKUP_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("ap_layer", "Primary coil layer"),
    ("primary_bridge_layer", "Primary bridge layer"),
    ("m9_layer", "Secondary coil layer"),
    ("secondary_bridge_layer", "Secondary bridge layer"),
)
STACKUP_FIELD_LABELS_BY_NAME: dict[str, str] = dict(STACKUP_FIELD_LABELS)


def _slider_resolution(lo: float, hi: float) -> float:
    span = hi - lo
    if span <= 20.0:
        return 0.1
    if span <= 80.0:
        return 0.25
    return 0.5


def _frequency_metric_curves(result, differential_reference_impedance_ohm: float) -> dict[str, np.ndarray] | None:
    precomputed = getattr(result, "frequency_metric_curves", None)
    if isinstance(precomputed, dict):
        return precomputed
    differential = getattr(result, "differential_sparams", None)
    if differential is None:
        return None
    freqs_hz = np.asarray(differential.freqs_hz, dtype=float)
    if freqs_hz.size == 0:
        return None
    omega = 2.0 * math.pi * freqs_hz
    valid = np.abs(omega) > 0.0
    z_diff = differential.to_z_parameters(z0=differential_reference_impedance_ohm)
    lp_h = np.full(freqs_hz.shape, np.nan, dtype=float)
    ls_h = np.full(freqs_hz.shape, np.nan, dtype=float)
    mutual_h = np.full(freqs_hz.shape, np.nan, dtype=float)
    q_primary = np.full(freqs_hz.shape, np.nan, dtype=float)
    q_secondary = np.full(freqs_hz.shape, np.nan, dtype=float)
    k = np.full(freqs_hz.shape, np.nan, dtype=float)
    lp_h[valid] = np.imag(z_diff[valid, 0, 0]) / omega[valid]
    ls_h[valid] = np.imag(z_diff[valid, 1, 1]) / omega[valid]
    mutual_h[valid] = np.imag(z_diff[valid, 1, 0]) / omega[valid]
    q_primary = np.imag(z_diff[:, 0, 0]) / np.where(np.abs(np.real(z_diff[:, 0, 0])) > 1.0e-12, np.real(z_diff[:, 0, 0]), np.nan)
    q_secondary = np.imag(z_diff[:, 1, 1]) / np.where(np.abs(np.real(z_diff[:, 1, 1])) > 1.0e-12, np.real(z_diff[:, 1, 1]), np.nan)
    denom = np.sqrt(np.maximum(lp_h * ls_h, 0.0))
    valid_k = np.isfinite(denom) & (denom > 1.0e-30)
    k[valid_k] = mutual_h[valid_k] / denom[valid_k]
    return {
        "freqs_ghz": freqs_hz / 1.0e9,
        "lp_h": lp_h,
        "ls_h": ls_h,
        "mutual_h": mutual_h,
        "q_primary": q_primary,
        "q_secondary": q_secondary,
        "k": k,
    }


def _bridge_section_payload(section: BridgeSectionConfig | None) -> dict[str, object] | None:
    if section is None:
        return None
    return {
        "pad_width_ratio": float(section.pad_width_ratio),
        "pad_height_ratio": float(section.pad_height_ratio),
        "via_size_ratio": float(section.via_size_ratio),
        "via_width_ratio": float(section.via_width_ratio),
        "via_spacing_ratio": float(section.via_spacing_ratio),
    }


def _run_config_to_payload(cfg) -> dict[str, object]:
    return {
        "target": {
            "f0_hz": float(cfg.target.f0_hz),
            "lp_h": float(cfg.target.lp_h),
            "ls_h": float(cfg.target.ls_h),
            "k_target": float(cfg.target.k_target),
            "topology_mode": cfg.target.topology_mode,
            "differential_reference_impedance_ohm": float(cfg.target.differential_reference_impedance_ohm),
            "band_points": int(cfg.target.band_points),
            "fractional_bandwidth": float(cfg.target.fractional_bandwidth),
        },
        "topology": {
            "primary": {
                "turns": int(cfg.bounds.primary.turns),
                "center_tap": bool(cfg.bounds.primary.center_tap),
                "vdd_bar": None if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.as_dict(),
            },
            "secondary": {
                "turns": int(cfg.bounds.secondary.turns),
                "center_tap": bool(cfg.bounds.secondary.center_tap),
                "vdd_bar": None if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.as_dict(),
            },
            "shield": cfg.bounds.shield.as_dict(),
        },
        "bounds": {
            "topology_mode": cfg.bounds.topology_mode,
            "offset_um": tuple(map(float, cfg.bounds.offset_um)),
            "primary": {
                "outer_width_um": tuple(map(float, cfg.bounds.primary.outer_width_um)),
                "outer_height_um": tuple(map(float, cfg.bounds.primary.outer_height_um)),
                "trace_width_um": tuple(map(float, cfg.bounds.primary.trace_width_um)),
                "spacing_um": tuple(map(float, cfg.bounds.primary.spacing_um)),
                "terminal_y_span_um": tuple(map(float, cfg.bounds.primary.terminal_y_span_um)),
                "feed_extension_um": tuple(map(float, cfg.bounds.primary.feed_extension_um)),
                "bridge_layer": cfg.bounds.primary.bridge_layer,
                "bridge_via_layer": cfg.bounds.primary.bridge_via_layer,
                "bridge_lower_layer": cfg.bounds.primary.bridge_lower_layer,
                "bridge_lower_via_layer": cfg.bounds.primary.bridge_lower_via_layer,
                "bridge_section": _bridge_section_payload(cfg.bounds.primary.bridge_section),
            },
            "secondary": {
                "outer_width_um": tuple(map(float, cfg.bounds.secondary.outer_width_um)),
                "outer_height_um": tuple(map(float, cfg.bounds.secondary.outer_height_um)),
                "trace_width_um": tuple(map(float, cfg.bounds.secondary.trace_width_um)),
                "spacing_um": tuple(map(float, cfg.bounds.secondary.spacing_um)),
                "terminal_y_span_um": tuple(map(float, cfg.bounds.secondary.terminal_y_span_um)),
                "feed_extension_um": tuple(map(float, cfg.bounds.secondary.feed_extension_um)),
                "bridge_layer": cfg.bounds.secondary.bridge_layer,
                "bridge_via_layer": cfg.bounds.secondary.bridge_via_layer,
                "bridge_lower_layer": cfg.bounds.secondary.bridge_lower_layer,
                "bridge_lower_via_layer": cfg.bounds.secondary.bridge_lower_via_layer,
                "bridge_section": _bridge_section_payload(cfg.bounds.secondary.bridge_section),
            },
        },
        "emx": {
            "emx_binary": cfg.emx.emx_binary,
            "emx_home": cfg.emx.emx_home,
            "emx_process_file": cfg.emx.emx_process_file,
            "top_cell_prefix": cfg.emx.top_cell_prefix,
            "extra_args": list(cfg.emx.extra_args),
            "use_cadence_license_env": bool(cfg.emx.use_cadence_license_env),
            "license_file": cfg.emx.license_file,
            "cdslmd_license_file": cfg.emx.cdslmd_license_file,
            "skip_os_check": bool(cfg.emx.skip_os_check),
            "port_mode": cfg.emx.port_mode,
            "primary_coil_layer": int(cfg.emx.primary_coil_layer),
            "secondary_coil_layer": int(cfg.emx.secondary_coil_layer),
            "m5_layer": int(cfg.emx.m5_layer),
            "primary_bridge_layer": int(cfg.emx.primary_bridge_layer),
            "primary_bridge_via_layer": int(cfg.emx.primary_bridge_via_layer),
            "primary_bridge_lower_layer": cfg.emx.primary_bridge_lower_layer,
            "primary_bridge_lower_via_layer": cfg.emx.primary_bridge_lower_via_layer,
            "secondary_bridge_layer": int(cfg.emx.secondary_bridge_layer),
            "secondary_bridge_via_layer": int(cfg.emx.secondary_bridge_via_layer),
            "secondary_bridge_lower_layer": cfg.emx.secondary_bridge_lower_layer,
            "secondary_bridge_lower_via_layer": cfg.emx.secondary_bridge_lower_via_layer,
            "shield_layer": cfg.emx.shield_layer,
            "metal_datatype": int(cfg.emx.metal_datatype),
            "label_layer": int(cfg.emx.label_layer),
            "label_datatype": int(cfg.emx.label_datatype),
            "via_layer_rules": asdict(cfg.emx)["via_layer_rules"],
            "via_family_rules": asdict(cfg.emx)["via_family_rules"],
            "enable_large_plate_warnings": bool(cfg.emx.enable_large_plate_warnings),
        },
        "optimizer": asdict(cfg.optimizer),
    }


@dataclass
class SliderBinding:
    slider: QtWidgets.QSlider
    value_label: QtWidgets.QLabel
    range_label: QtWidgets.QLabel
    minimum: float
    maximum: float
    resolution: float


@dataclass
class OptimizationEvalRecord:
    evaluation_count: int
    unique_evaluation_count: int
    backend_name: str
    elapsed_seconds: float
    is_best: bool
    cost: float
    result: object

    @property
    def cache_key(self) -> str:
        return str(getattr(self.result, "cache_key", ""))

    @property
    def error(self) -> str | None:
        error = getattr(self.result, "error", None)
        return None if error is None else str(error)

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class GuiHistoryEntry:
    kind: str
    label: str
    status: str
    topology_mode: str
    path: Path
    config_path: Path
    updated_at: datetime
    detail_path: str | None = None


def _theme_preview_image_array(image: np.ndarray) -> np.ndarray:
    data = np.asarray(image)
    if data.ndim != 3 or data.shape[2] not in (3, 4):
        return data
    themed = np.array(data, copy=True)
    rgb = themed[..., :3].astype(np.float32)
    if rgb.max(initial=0.0) > 1.0:
        rgb /= 255.0
    background = np.array([0xF6, 0xF2, 0xEC], dtype=np.float32) / 255.0
    whiteness = np.min(rgb, axis=2)
    mask = whiteness > 0.9
    if np.any(mask):
        blend = np.clip((whiteness[mask] - 0.9) / 0.1, 0.0, 1.0)[:, None]
        rgb[mask] = (1.0 - blend) * rgb[mask] + blend * background
    if np.issubdtype(themed.dtype, np.integer):
        themed[..., :3] = np.clip(np.round(rgb * 255.0), 0.0, 255.0).astype(themed.dtype)
    else:
        themed[..., :3] = np.clip(rgb, 0.0, 1.0).astype(themed.dtype, copy=False)
    return themed


class PreviewRefreshWorkerThread(QtCore.QThread):
    completed = QtSignal(object)
    failed = QtSignal(object)

    def __init__(self, *, generation: int, run_config, geometry: TransformerGeometrySpec, yaml_text: str, preview_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.generation = int(generation)
        self.run_config = run_config
        self.geometry = geometry
        self.yaml_text = str(yaml_text)
        self.preview_dir = Path(preview_dir)

    def run(self) -> None:  # pragma: no cover - exercised through GUI runtime
        try:
            bounds_errors = list(self.run_config.bounds.validate(self.geometry))
            geometry_errors = [*bounds_errors, *self.geometry.validate()]
            gdstk_check = run_transformer_gdstk_checks(self.geometry, self.run_config)
            layout = export_transformer_layout(
                geometry=self.geometry,
                run_config=self.run_config,
                out_dir=self.preview_dir,
                validate_geometry=False,
            )
            image = _theme_preview_image_array(mpimg.imread(layout.preview_path))
            self.completed.emit(
                {
                    "generation": self.generation,
                    "run_config": self.run_config,
                    "geometry": self.geometry,
                    "yaml_text": self.yaml_text,
                    "bounds_errors": bounds_errors,
                    "geometry_errors": geometry_errors,
                    "gdstk_check": gdstk_check,
                    "layout": layout,
                    "image": image,
                }
            )
        except Exception as exc:
            self.failed.emit({"generation": self.generation, "error": str(exc)})


class OptimizationWorkerThread(QtCore.QThread):
    progress = QtSignal(object)
    completed = QtSignal(object, bool, str)
    failed = QtSignal(str, str)

    def __init__(self, *, run_config, run_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.run_config = run_config
        self.run_dir = Path(run_dir)
        self._stop_requested = threading.Event()
        self._pause_requested = threading.Event()

    def request_stop(self) -> None:
        self._stop_requested.set()
        self._pause_requested.clear()

    def request_pause(self) -> None:
        self._pause_requested.set()

    def request_resume(self) -> None:
        self._pause_requested.clear()

    def is_pause_requested(self) -> bool:
        return self._pause_requested.is_set()

    def run(self) -> None:  # pragma: no cover - exercised through GUI runtime
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            payload = _run_config_to_payload(self.run_config)
            (self.run_dir / "gui_snapshot.yaml").write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
                encoding="utf-8",
            )
            evaluator = TransformerEmxEvaluator(run_config=self.run_config, root_dir=self.run_dir)
            optimizer = TransformerOptimizer(
                evaluator=evaluator,
                progress_callback=lambda payload: self.progress.emit(payload),
                should_stop=self._stop_requested.is_set,
                should_pause=self._pause_requested.is_set,
            )
            result = optimizer.optimize()
            self.completed.emit(result, bool(self._stop_requested.is_set()), str(self.run_dir))
        except Exception as exc:
            self.failed.emit(str(exc), str(self.run_dir))


class TransformerConstraintQtGui(QtWidgets.QMainWindow):
    def __init__(
        self,
        *,
        topology_mode: TopologyMode,
        config_path: str | None = None,
        demo_optimization_view: bool = False,
    ) -> None:
        super().__init__()
        self.topology_mode = topology_mode
        self.demo_optimization_view = bool(demo_optimization_view)
        self.config_path: Path | None = None
        self.run_config = default_run_config(topology_mode=topology_mode)
        if config_path is not None:
            loaded = load_run_config(config_path)
            self.run_config = loaded
            self.config_path = Path(config_path).resolve()
        self.bounds = self.run_config.bounds
        self.preview_dir = Path()
        self.generated_config_path = Path()
        self.recent_config_store_path = Path()
        self.history_root = Path()
        self.history_configs_dir = Path()
        self.history_runs_dir = Path()
        self._apply_topology_mode(self.run_config.bounds.topology_mode)
        self.recent_config_paths: list[Path] = []
        self._load_recent_config_history()
        self.current_image = None
        self.current_preview_display_image: np.ndarray | None = None
        self.current_preview_geometry_mask: np.ndarray | None = None
        self.current_preview_extent: tuple[float, float, float, float] | None = None
        self.current_preview_geometry: TransformerGeometrySpec | None = None
        self.current_preview_layout = None
        self.preview_show_dimensions = True
        self.last_preview_path: Path | None = None
        self.last_debug_preview_path: Path | None = None
        self._suspend_refresh = False
        self.preview_refresh_generation = 0
        self.preview_refresh_thread: PreviewRefreshWorkerThread | None = None
        self.preview_refresh_pending_request: tuple[int, object, TransformerGeometrySpec, str] | None = None
        self.preview_refresh_running_generation: int | None = None
        self.proc_info_cache: dict[tuple[str, int, int], object] = {}

        self.slider_bindings: dict[str, SliderBinding] = {}
        self.line_edits: dict[str, QtWidgets.QLineEdit] = {}
        self.combo_boxes: dict[str, QtWidgets.QComboBox] = {}
        self.check_boxes: dict[str, QtWidgets.QCheckBox] = {}
        self.stackup_values: dict[str, int] = {}
        self.stackup_combos: dict[str, QtWidgets.QComboBox] = {}
        self.layer_choice_values: dict[str, int | None] = {}
        self.layer_choice_combos: dict[str, QtWidgets.QComboBox] = {}
        self.optional_widget_groups: dict[str, list[QtWidgets.QWidget]] = {}
        self.bridge_section_widgets: dict[str, list[QtWidgets.QWidget]] = {}
        self.hidden_emx_values: dict[str, int] = {
            "metal_datatype": int(self.run_config.emx.metal_datatype),
            "label_layer": int(self.run_config.emx.label_layer),
            "label_datatype": int(self.run_config.emx.label_datatype),
        }
        self.optimization_thread: OptimizationWorkerThread | None = None
        self.optimization_run_dir: Path | None = None
        self.optimization_history: list[tuple[int, float, float]] = []
        self.optimization_eval_records: list[OptimizationEvalRecord] = []
        self.optimization_best_result = None
        self.optimization_last_result = None
        self.optimization_log_lines: list[str] = []
        self.optimization_launch_snapshot_yaml: str | None = None
        self.optimization_launch_snapshot_name: str | None = None
        self.optimization_editor_dirty_since_start = False
        self.optimization_blocking_errors: list[str] = []
        self.optimization_preview_mode = "best"
        self.optimization_manual_selected_eval: int | None = None
        self.optimization_selected_result = None
        self.best_metrics_cursors: list[Cursor] = []
        self.best_metrics_motion_connection = None
        self.optimization_backend_value_label: QtWidgets.QLabel | None = None
        self.optimization_run_status_value_label: QtWidgets.QLabel | None = None
        self.optimization_snapshot_value_label: QtWidgets.QLabel | None = None
        self.optimization_editor_state_value_label: QtWidgets.QLabel | None = None
        self.optimization_run_dir_value: QtWidgets.QLineEdit | None = None
        self.optimization_eval_dir_value: QtWidgets.QLineEdit | None = None
        self.optimization_preview_mode_combo: QtWidgets.QComboBox | None = None
        self.optimization_follow_live_button: QtWidgets.QPushButton | None = None
        self.optimization_eval_table: QtWidgets.QTableWidget | None = None
        self.optimization_viewer_tabs: QtWidgets.QTabWidget | None = None
        self.optimization_summary_text: QtWidgets.QPlainTextEdit | None = None
        self.optimization_eval_detail_text: QtWidgets.QPlainTextEdit | None = None
        self.optimization_blocking_text: QtWidgets.QPlainTextEdit | None = None
        self.config_recent_tree: QtWidgets.QTreeWidget | None = None
        self.config_current_path_value: QtWidgets.QLineEdit | None = None
        self.config_current_topology_value: QtWidgets.QLabel | None = None
        self.viewer3d = None
        self.viewer3d_placeholder: QtWidgets.QLabel | None = None
        self.viewer3d_status_label: QtWidgets.QLabel | None = None
        self.viewer3d_show_dielectrics_checkbox: QtWidgets.QCheckBox | None = None
        self.viewer3d_show_dimensions_checkbox: QtWidgets.QCheckBox | None = None
        self.viewer3d_mode_2d_button: QtWidgets.QPushButton | None = None
        self.viewer3d_mode_3d_button: QtWidgets.QPushButton | None = None
        self.viewer3d_save_button: QtWidgets.QPushButton | None = None
        self.viewer3d_last_source: tuple[str, str, str] | None = None
        self.viewer3d_pending_layout = None
        self.viewer3d_actor_defaults: dict[str, dict[str, object]] = {}
        self.viewer3d_selected_actor_id: str | None = None
        self.viewer3d_show_dielectrics = True
        self.viewer3d_show_dimensions = True
        self.viewer3d_view_mode = "3d"
        self._is_closing = False

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._refresh)
        self.viewer3d_refresh_timer = QtCore.QTimer(self)
        self.viewer3d_refresh_timer.setSingleShot(True)
        self.viewer3d_refresh_timer.timeout.connect(self._flush_3d_view_refresh)

        self._build_layout()
        self._apply_gui_theme()
        self._load_run_config_into_editor(self.run_config)
        self._load_geometry(self.bounds.midpoint())
        self._refresh_stackup_controls()
        self._update_optional_control_state()
        self.resize(1500, 1080)
        self._schedule_refresh()
        if self.demo_optimization_view:
            self._seed_demo_optimization_view()

    @contextmanager
    def _signal_guard(self):
        previous = self._suspend_refresh
        self._suspend_refresh = True
        try:
            yield
        finally:
            self._suspend_refresh = previous

    def _apply_gui_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #f6f2ec;
                color: #2f2720;
            }
            QSplitter::handle {
                background-color: #d5cabd;
            }
            QGroupBox {
                background-color: #fbf8f3;
                border: 1px solid #d7cabd;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #3a3026;
            }
            QLabel {
                color: #2f2720;
            }
            QPushButton {
                background-color: #e7d8c7;
                color: #2c241d;
                border: 1px solid #c8b39b;
                border-radius: 4px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #dcc8af;
            }
            QPushButton:pressed {
                background-color: #ceb599;
            }
            QPushButton:disabled {
                background-color: #efe6dc;
                color: #9b8b7b;
                border-color: #d7cabd;
            }
            QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #fffdfb;
                color: #2f2720;
                border: 1px solid #cdb9a7;
                border-radius: 4px;
                padding: 4px 6px;
                selection-background-color: #d7c0a1;
                selection-color: #2c241d;
            }
            QComboBox QAbstractItemView {
                background-color: #fffdfb;
                color: #2f2720;
                selection-background-color: #d7c0a1;
                selection-color: #2c241d;
                border: 1px solid #cdb9a7;
            }
            QCheckBox, QRadioButton {
                color: #2f2720;
                spacing: 6px;
            }
            QCheckBox::indicator, QRadioButton::indicator {
                width: 14px;
                height: 14px;
            }
            QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked {
                background-color: #fffdfb;
                border: 1px solid #c8b39b;
            }
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {
                background-color: #d6b383;
                border: 1px solid #ba9061;
            }
            QTabWidget::pane {
                border: 1px solid #d7cabd;
                background-color: #fbf8f3;
            }
            QTabBar::tab {
                background-color: #eadfd2;
                color: #4b3d30;
                border: 1px solid #d3c4b4;
                padding: 6px 10px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #fffdfb;
                color: #2f2720;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e0d1c0;
            }
            QScrollArea {
                border: none;
                background-color: #f6f2ec;
            }
            QHeaderView::section {
                background-color: #e9ddd0;
                color: #3a3026;
                border: 1px solid #d1c2b4;
                padding: 4px 6px;
            }
            QToolTip {
                background-color: #fffdf9;
                color: #2f2720;
                border: 1px solid #cdb9a7;
            }
            """
        )

    @staticmethod
    def _apply_plot_theme(figure: Figure, axes: list[object] | tuple[object, ...]) -> None:
        figure.patch.set_facecolor("#f6f2ec")
        for axis in axes:
            axis.set_facecolor("#fffdfb")
            for spine in axis.spines.values():
                spine.set_color("#c8b9aa")
            axis.tick_params(colors="#58483a", labelcolor="#58483a")
            axis.xaxis.label.set_color("#3d3127")
            axis.yaxis.label.set_color("#3d3127")
            axis.title.set_color("#7a4f1f")
            axis.xaxis.offsetText.set_color("#58483a")
            axis.yaxis.offsetText.set_color("#58483a")

    @staticmethod
    def _apply_data_view_theme(widget: QtWidgets.QWidget) -> None:
        widget.setStyleSheet(
            """
            QTreeWidget, QTableWidget {
                background-color: #fffdfb;
                alternate-background-color: #f5eee5;
                color: #2f2720;
                gridline-color: #d7cabd;
                selection-background-color: #d7c0a1;
                selection-color: #2c241d;
            }
            QTreeWidget::item:selected, QTableWidget::item:selected {
                background-color: #d7c0a1;
                color: #2c241d;
            }
            QTreeWidget::item:hover, QTableWidget::item:hover {
                background-color: #ece1d5;
            }
            QHeaderView::section {
                background-color: #e9ddd0;
                color: #3a3026;
                border: 1px solid #d1c2b4;
                padding: 4px 6px;
            }
            """
        )

    @staticmethod
    def _wrap_scroll(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _apply_topology_mode(self, topology_mode: TopologyMode) -> None:
        self.topology_mode = topology_mode
        self.preview_dir = runtime_root() / "gui_qt"
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.generated_config_path = self.preview_dir / "gui_current_config.yaml"
        self.recent_config_store_path = self.preview_dir / "recent_configs.yaml"
        self.history_root = self.preview_dir / "history"
        self.history_configs_dir = self.history_root / "configs"
        self.history_runs_dir = self.history_root / "runs"
        self.history_configs_dir.mkdir(parents=True, exist_ok=True)
        self.history_runs_dir.mkdir(parents=True, exist_ok=True)
        self.setWindowTitle(f"RFIC Transformer Toolkit GUI (Qt, {topology_mode})")

    def _build_layout(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QHBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        controls_panel = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        editor_tabs = QtWidgets.QTabWidget()
        controls_layout.addWidget(editor_tabs, 1)

        editor_tabs.addTab(self._build_configs_tab(), "Configs")
        editor_tabs.addTab(self._build_geometry_tab(), "TransformerSpec")
        editor_tabs.addTab(self._build_fixed_geom_tab(), "FixedSpec")
        editor_tabs.addTab(self._build_target_tab(), "TargetSpec")
        editor_tabs.addTab(self._build_bounds_tab(), "SearchSpace")
        editor_tabs.addTab(self._build_emx_tab(), "EmxConfig")
        editor_tabs.addTab(self._build_optimizer_tab(), "OptimizerConfig")
        self.run_optimization_button = QtWidgets.QPushButton("Start Optimization")
        self.run_optimization_button.clicked.connect(self._start_optimization)
        self.pause_optimization_button = QtWidgets.QPushButton("Pause")
        self.pause_optimization_button.setEnabled(False)
        self.pause_optimization_button.clicked.connect(self._pause_optimization)
        self.resume_optimization_button = QtWidgets.QPushButton("Resume")
        self.resume_optimization_button.setEnabled(False)
        self.resume_optimization_button.clicked.connect(self._resume_optimization)
        self.stop_optimization_button = QtWidgets.QPushButton("Stop")
        self.stop_optimization_button.setEnabled(False)
        self.stop_optimization_button.clicked.connect(self._stop_optimization)
        self.export_best_sparams_button = QtWidgets.QPushButton("Export Best S-Params")
        self.export_best_sparams_button.setEnabled(False)
        self.export_best_sparams_button.clicked.connect(self._export_best_sparams)
        editor_tabs.addTab(self._build_optimization_workflow_tab(), "Optimization")

        self.status_label = QtWidgets.QLabel("Ready")
        self.status_label.setWordWrap(True)
        controls_layout.addWidget(self.status_label)
        self.optimization_status_label = QtWidgets.QLabel("Optimization idle.")
        self.optimization_status_label.setWordWrap(True)
        controls_layout.addWidget(self.optimization_status_label)

        preview_panel = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.top_tabs = QtWidgets.QTabWidget()
        self.top_tabs.currentChanged.connect(self._on_top_tab_changed)
        preview_layout.addWidget(self.top_tabs, 3)

        self.preview_group = QtWidgets.QGroupBox("Current Design Preview")
        preview_group_layout = QtWidgets.QVBoxLayout(self.preview_group)
        self.figure = Figure(figsize=(8, 6), dpi=110)
        self.axis = self.figure.add_subplot(111)
        self._apply_plot_theme(self.figure, (self.axis,))
        self.axis.set_axis_off()
        self.canvas = FigureCanvasQTAgg(self.figure)
        preview_group_layout.addWidget(self.canvas)
        preview_controls_row = QtWidgets.QHBoxLayout()
        preview_controls_row.addStretch(1)
        self.preview_dimensions_checkbox = QtWidgets.QCheckBox("Show Dimensions")
        self.preview_dimensions_checkbox.setChecked(self.preview_show_dimensions)
        self.preview_dimensions_checkbox.toggled.connect(self._on_preview_dimensions_toggled)
        preview_controls_row.addWidget(self.preview_dimensions_checkbox)
        preview_group_layout.addLayout(preview_controls_row)
        self.top_tabs.addTab(self.preview_group, "Preview")
        self.viewer3d_group = self._build_3d_tab()
        self.top_tabs.addTab(self.viewer3d_group, "3D")
        self.optimization_viewer_group = self._build_optimization_viewer_tab()
        self.top_tabs.addTab(self.optimization_viewer_group, "Optimization")
        self._refresh_top_tab_visibility()

        lower_tabs = QtWidgets.QTabWidget()
        self.validation_text = QtWidgets.QPlainTextEdit()
        self.validation_text.setReadOnly(True)
        self.proc_text = QtWidgets.QPlainTextEdit()
        self.proc_text.setReadOnly(True)
        self.optimization_text = QtWidgets.QPlainTextEdit()
        self.optimization_text.setReadOnly(True)
        self.optimization_text.hide()
        lower_tabs.addTab(self.validation_text, "Validation")
        lower_tabs.addTab(self.proc_text, "Proc Stackup")
        preview_layout.addWidget(lower_tabs, 2)
        self._refresh_optimization_text()

        splitter.addWidget(controls_panel)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([750, 750])

    def _build_3d_tab(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("3D Stackup Viewer")
        layout = QtWidgets.QVBoxLayout(group)
        controls_row = QtWidgets.QHBoxLayout()
        self.viewer3d_show_dielectrics_checkbox = QtWidgets.QCheckBox("Show Dielectrics")
        self.viewer3d_show_dielectrics_checkbox.setChecked(self.viewer3d_show_dielectrics)
        self.viewer3d_show_dielectrics_checkbox.toggled.connect(self._on_3d_show_dielectrics_toggled)
        controls_row.addWidget(self.viewer3d_show_dielectrics_checkbox)
        self.viewer3d_show_dimensions_checkbox = QtWidgets.QCheckBox("Show Dimensions")
        self.viewer3d_show_dimensions_checkbox.setChecked(self.viewer3d_show_dimensions)
        self.viewer3d_show_dimensions_checkbox.toggled.connect(self._on_3d_show_dimensions_toggled)
        controls_row.addWidget(self.viewer3d_show_dimensions_checkbox)
        self.viewer3d_mode_2d_button = QtWidgets.QPushButton("2D")
        self.viewer3d_mode_2d_button.setCheckable(True)
        self.viewer3d_mode_2d_button.setAutoExclusive(True)
        self.viewer3d_mode_2d_button.setChecked(self.viewer3d_view_mode == "2d")
        self.viewer3d_mode_2d_button.toggled.connect(lambda checked: self._on_3d_view_mode_toggled("2d", checked))
        controls_row.addWidget(self.viewer3d_mode_2d_button)
        self.viewer3d_mode_3d_button = QtWidgets.QPushButton("3D")
        self.viewer3d_mode_3d_button.setCheckable(True)
        self.viewer3d_mode_3d_button.setAutoExclusive(True)
        self.viewer3d_mode_3d_button.setChecked(self.viewer3d_view_mode == "3d")
        self.viewer3d_mode_3d_button.toggled.connect(lambda checked: self._on_3d_view_mode_toggled("3d", checked))
        controls_row.addWidget(self.viewer3d_mode_3d_button)
        self.viewer3d_save_button = QtWidgets.QPushButton("Save View...")
        self.viewer3d_save_button.clicked.connect(self._save_3d_view)
        controls_row.addWidget(self.viewer3d_save_button)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)
        self.viewer3d_status_label = QtWidgets.QLabel()
        self.viewer3d_status_label.setWordWrap(True)
        unavailable_reason = self._pyvista_unavailable_reason()
        if unavailable_reason is None:
            self.viewer3d = QtInteractor(group)
            self._configure_3d_viewer_rendering()
            self.viewer3d.installEventFilter(self)
            if isinstance(getattr(self.viewer3d, "iren", None), QtCore.QObject) and self.viewer3d.iren is not self.viewer3d:
                self.viewer3d.iren.installEventFilter(self)
            layout.addWidget(self.viewer3d, 1)
            self.viewer3d_status_label.setText(
                "Interactive 3D stackup viewer ready. Double-click metal or via geometry to toggle solid opacity."
            )
        else:
            self.viewer3d_placeholder = QtWidgets.QLabel(unavailable_reason)
            self.viewer3d_placeholder.setWordWrap(True)
            self.viewer3d_placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.viewer3d_placeholder, 1)
            self.viewer3d_status_label.setText("3D viewer unavailable in this Qt/runtime configuration.")
        layout.addWidget(self.viewer3d_status_label)
        return group

    def _build_optimization_viewer_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        tabs = QtWidgets.QTabWidget(container)
        self.optimization_viewer_tabs = tabs

        convergence_group = QtWidgets.QGroupBox("Objective Convergence")
        convergence_group_layout = QtWidgets.QVBoxLayout(convergence_group)
        self.convergence_figure = Figure(figsize=(5, 6), dpi=110)
        self.convergence_axis = self.convergence_figure.add_subplot(111)
        self._apply_plot_theme(self.convergence_figure, (self.convergence_axis,))
        self.convergence_canvas = FigureCanvasQTAgg(self.convergence_figure)
        self.convergence_toolbar = NavigationToolbar2QT(self.convergence_canvas, self)
        convergence_group_layout.addWidget(self.convergence_canvas)
        convergence_group_layout.addWidget(self.convergence_toolbar)
        tabs.addTab(convergence_group, "Convergence")

        metrics_group = QtWidgets.QGroupBox("Optimization Metrics")
        metrics_layout = QtWidgets.QVBoxLayout(metrics_group)
        self.best_metrics_figure = Figure(figsize=(5, 6), dpi=110)
        self.best_k_axis = self.best_metrics_figure.add_subplot(311)
        self.best_q_axis = self.best_metrics_figure.add_subplot(312)
        self.best_l_axis = self.best_metrics_figure.add_subplot(313)
        self._apply_plot_theme(self.best_metrics_figure, (self.best_k_axis, self.best_q_axis, self.best_l_axis))
        self.best_metrics_canvas = FigureCanvasQTAgg(self.best_metrics_figure)
        self.best_metrics_toolbar = NavigationToolbar2QT(self.best_metrics_canvas, self)
        self.best_metrics_context_label = QtWidgets.QLabel("Viewing: no optimization result selected")
        self.best_metrics_context_label.setWordWrap(False)
        self.best_metrics_context_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.best_metrics_cursor_label = QtWidgets.QLabel("Cursor: no optimization frequency data")
        self.best_metrics_cursor_label.setWordWrap(False)
        self.best_metrics_cursor_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.best_metrics_cursor_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        metrics_status_row = QtWidgets.QHBoxLayout()
        metrics_status_row.setContentsMargins(0, 0, 0, 0)
        metrics_status_row.setSpacing(10)
        metrics_layout.addWidget(self.best_metrics_canvas)
        metrics_layout.addWidget(self.best_metrics_toolbar)
        metrics_status_row.addWidget(self.best_metrics_context_label, 1)
        metrics_status_row.addWidget(self.best_metrics_cursor_label, 1)
        metrics_layout.addLayout(metrics_status_row)
        tabs.addTab(metrics_group, "Frequency Metrics")
        layout.addWidget(tabs, 1)

        self._draw_convergence_plot()
        self._draw_best_metrics_plot()
        return container

    def _build_configs_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        action_row = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open Config...")
        open_button.clicked.connect(self._load_config)
        save_button = QtWidgets.QPushButton("Save Config As...")
        save_button.clicked.connect(self._save_config)
        action_row.addWidget(open_button)
        action_row.addWidget(save_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        metadata_form = QtWidgets.QFormLayout()
        metadata_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.config_current_topology_value = QtWidgets.QLabel(str(self.topology_mode))
        self.config_current_topology_value.setWordWrap(True)
        metadata_form.addRow("Current topology", self.config_current_topology_value)
        self.config_current_path_value = QtWidgets.QLineEdit()
        self.config_current_path_value.setReadOnly(True)
        metadata_form.addRow("Current config", self.config_current_path_value)
        layout.addLayout(metadata_form)

        recent_group = QtWidgets.QGroupBox("Config and Run History")
        recent_layout = QtWidgets.QVBoxLayout(recent_group)
        self.config_recent_tree = QtWidgets.QTreeWidget()
        self.config_recent_tree.setHeaderLabels(("Name", "Type", "Topology", "Path"))
        self.config_recent_tree.setRootIsDecorated(True)
        self.config_recent_tree.setAlternatingRowColors(True)
        self.config_recent_tree.itemDoubleClicked.connect(self._open_recent_config_item)
        self.config_recent_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.config_recent_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.config_recent_tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.config_recent_tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._apply_data_view_theme(self.config_recent_tree)
        recent_layout.addWidget(self.config_recent_tree)
        layout.addWidget(recent_group, 1)
        self._refresh_config_hub()
        return self._wrap_scroll(container)

    def _refresh_top_tab_visibility(self) -> None:
        self.top_tabs.tabBar().setVisible(self.top_tabs.count() > 1)

    @staticmethod
    def _qt_platform_name() -> str:
        value = os.environ.get("QT_QPA_PLATFORM", "")
        return str(value).split(":", maxsplit=1)[0].strip().lower()

    def _pyvista_unavailable_reason(self) -> str | None:
        if pv is None:
            return "3D viewer requires the optional `pyvista` package."
        if QtInteractor is None:
            return "3D viewer requires the optional `pyvistaqt` package for embedded Qt interaction."
        platform_name = self._qt_platform_name()
        if platform_name in {"offscreen", "minimal"}:
            return (
                f"3D viewer is disabled for QT_QPA_PLATFORM={platform_name}. "
                "Use a normal desktop Qt session with working OpenGL for interactive 3D."
            )
        return None

    def _set_3d_status(self, message: str) -> None:
        if self.viewer3d_status_label is not None:
            self.viewer3d_status_label.setText(str(message))

    def _draw_canvas_idle(self, canvas) -> None:
        if self._is_closing or canvas is None:
            return
        try:
            canvas.draw()
        except RuntimeError:
            return

    @staticmethod
    def _clamp_preview_value(value: float, lower: float, upper: float) -> float:
        return max(float(lower), min(float(upper), float(value)))

    @staticmethod
    def _preview_dimension_text(label: str) -> str:
        if label.startswith("Primary "):
            return "Primary\n" + label[len("Primary "):]
        if label.startswith("Secondary "):
            return "Secondary\n" + label[len("Secondary "):]
        return label.replace(" offset", "\noffset")

    def _on_preview_dimensions_toggled(self, checked: bool) -> None:
        self.preview_show_dimensions = bool(checked)
        if self.current_image is None or self.current_preview_geometry is None:
            return
        self._draw_preview_image(
            image=self.current_image,
            geometry=self.current_preview_geometry,
            layout=self.current_preview_layout,
            title=self.preview_group.title(),
        )

    def _approximate_preview_extent(
        self,
        geometry: TransformerGeometrySpec,
    ) -> tuple[float, float, float, float]:
        primary = geometry.primary
        secondary = geometry.secondary
        left = min(
            -0.5 * float(primary.outer_width_um) - float(primary.feed_extension_um),
            float(geometry.offset_um) - 0.5 * float(secondary.outer_width_um),
        )
        right = max(
            0.5 * float(primary.outer_width_um),
            float(geometry.offset_um) + 0.5 * float(secondary.outer_width_um) + float(secondary.feed_extension_um),
        )
        half_height = 0.5 * max(float(primary.outer_height_um), float(secondary.outer_height_um))
        bottom = -half_height
        top = half_height

        shield = getattr(geometry, "shield", None)
        if shield is not None and bool(getattr(shield, "enabled", False)) and getattr(shield, "width_um", None) is not None:
            shield_width_um = float(shield.width_um)
            vertical_margin_um = float(0.0 if getattr(shield, "margin_um", None) is None else shield.margin_um)
            inner_half_height = half_height + vertical_margin_um
            bottom = min(bottom, -inner_half_height - shield_width_um)
            top = max(top, inner_half_height + shield_width_um)
            left = min(left, left - shield_width_um)
            right = max(right, right + shield_width_um)

        span_x = max(right - left, 1.0)
        span_y = max(top - bottom, 1.0)
        pad_x = max(0.08 * span_x, 1.0)
        pad_y = max(0.08 * span_y, 1.0)
        return (left - pad_x, right + pad_x, bottom - pad_y, top + pad_y)

    def _preview_extent_for_geometry(
        self,
        *,
        geometry: TransformerGeometrySpec,
        layout=None,
    ) -> tuple[float, float, float, float]:
        if layout is not None:
            gds_path = getattr(layout, "gds_path", None)
            manifest_path = getattr(layout, "manifest_path", None)
            if gds_path is not None:
                try:
                    manifest = None if manifest_path is None else Path(manifest_path)
                    return layout_preview_extent(Path(gds_path), manifest_path=manifest)
                except Exception:
                    pass
        return self._approximate_preview_extent(geometry)

    def _draw_preview_dimension(
        self,
        *,
        label: str,
        orientation: str,
        color: str,
        span_start: float,
        span_end: float,
        ref_coord: float,
        line_coord: float,
        normal_span: float,
        label_side: str = "positive",
        max_label_line_delta: float | None = None,
    ):
        guide_style = {
            "color": color,
            "linewidth": 0.8,
            "alpha": 0.72,
            "linestyle": (0, (1.4, 1.4)),
            "zorder": 6,
        }
        arrow_style = {
            "arrowstyle": "<->",
            "color": color,
            "linewidth": 1.05,
            "shrinkA": 0.0,
            "shrinkB": 0.0,
            "mutation_scale": 8.5,
            "alpha": 0.96,
        }
        text = self._preview_dimension_text(label)
        text_style = {
            "ha": "center",
            "va": "center",
            "fontsize": 6.6,
            "color": color,
            "clip_on": False,
            "zorder": 8,
        }
        label_offset = max(0.018 * float(normal_span), 5.0)
        text_effects = [
            patheffects.withStroke(linewidth=3.4, foreground="#fffaf5", alpha=0.98),
        ]

        if orientation == "horizontal":
            x0 = float(span_start)
            x1 = float(span_end)
            if x1 < x0:
                x0, x1 = x1, x0
            if abs(x1 - x0) < 1.0e-9:
                x0 -= 0.5 * label_offset
                x1 += 0.5 * label_offset
            y_ref = float(ref_coord)
            y_line = float(line_coord)
            self.axis.plot([x0, x0], [y_ref, y_line], **guide_style)
            self.axis.plot([x1, x1], [y_ref, y_line], **guide_style)
            self.axis.annotate("", xy=(x1, y_line), xytext=(x0, y_line), arrowprops=arrow_style, zorder=7)
            text_artist = self.axis.text(
                0.5 * (x0 + x1),
                y_line,
                text,
                **text_style,
            )
            text_artist.set_path_effects(text_effects)
            return {
                "artist": text_artist,
                "move_axis": "y",
                "preferred_sign": 1 if label_side == "positive" else (-1 if label_side == "negative" else 0),
                "orientation": "horizontal",
                "line_coord": y_line,
                "max_line_delta": None if max_label_line_delta is None else float(max_label_line_delta),
            }

        if orientation == "vertical":
            y0 = float(span_start)
            y1 = float(span_end)
            if y1 < y0:
                y0, y1 = y1, y0
            if abs(y1 - y0) < 1.0e-9:
                y0 -= 0.5 * label_offset
                y1 += 0.5 * label_offset
            x_ref = float(ref_coord)
            x_line = float(line_coord)
            self.axis.plot([x_ref, x_line], [y0, y0], **guide_style)
            self.axis.plot([x_ref, x_line], [y1, y1], **guide_style)
            self.axis.annotate("", xy=(x_line, y1), xytext=(x_line, y0), arrowprops=arrow_style, zorder=7)
            text_artist = self.axis.text(
                x_line,
                0.5 * (y0 + y1),
                text,
                **text_style,
            )
            text_artist.set_path_effects(text_effects)
            return {
                "artist": text_artist,
                "move_axis": "x",
                "preferred_sign": 1 if label_side == "positive" else (-1 if label_side == "negative" else 0),
                "orientation": "vertical",
                "line_coord": x_line,
                "max_line_delta": None if max_label_line_delta is None else float(max_label_line_delta),
            }

        raise ValueError(f"Unsupported preview dimension orientation: {orientation}")

    def _preview_text_bbox_data(self, text_artist, renderer) -> tuple[float, float, float, float]:
        bbox = text_artist.get_window_extent(renderer=renderer).expanded(1.04, 1.08)
        points = self.axis.transData.inverted().transform(bbox.get_points())
        x0, y0 = points[0]
        x1, y1 = points[1]
        return (
            min(float(x0), float(x1)),
            min(float(y0), float(y1)),
            max(float(x0), float(x1)),
            max(float(y0), float(y1)),
        )

    def _preview_bbox_overlaps_geometry(self, bbox: tuple[float, float, float, float]) -> bool:
        if self.current_preview_geometry_mask is None or self.current_preview_extent is None:
            return False
        mask = self.current_preview_geometry_mask
        left, right, bottom, top = map(float, self.current_preview_extent)
        if right <= left or top <= bottom:
            return False
        x0, y0, x1, y1 = map(float, bbox)
        if x1 <= left or x0 >= right or y1 <= bottom or y0 >= top:
            return False

        height_px, width_px = mask.shape[:2]
        ix0 = max(0, min(width_px - 1, int(math.floor((x0 - left) / (right - left) * width_px))))
        ix1 = max(0, min(width_px, int(math.ceil((x1 - left) / (right - left) * width_px))))
        iy0 = max(0, min(height_px - 1, int(math.floor((y0 - bottom) / (top - bottom) * height_px))))
        iy1 = max(0, min(height_px, int(math.ceil((y1 - bottom) / (top - bottom) * height_px))))
        if ix1 <= ix0 or iy1 <= iy0:
            return False

        crop = mask[iy0:iy1, ix0:ix1]
        if crop.size == 0:
            return False
        return bool(np.any(crop))

    @staticmethod
    def _preview_geometry_mask(image: np.ndarray) -> np.ndarray | None:
        if image.ndim != 3 or image.shape[0] == 0 or image.shape[1] == 0:
            return None
        rgb = image[..., :3].astype(np.float32)
        if rgb.max(initial=0.0) > 1.0:
            rgb /= 255.0
        background = np.array([0xF6, 0xF2, 0xEC], dtype=np.float32) / 255.0
        alpha = np.ones(rgb.shape[:2], dtype=np.float32)
        if image.shape[2] >= 4:
            alpha = image[..., 3].astype(np.float32)
            if alpha.max(initial=0.0) > 1.0:
                alpha /= 255.0
        difference = np.max(np.abs(rgb - background), axis=2)
        mask = (difference > 0.065) & (alpha > 0.05)
        coverage = float(np.mean(mask))
        if coverage <= 0.0 or coverage >= 0.7:
            return None
        return mask

    @staticmethod
    def _shift_preview_label(text_artist, *, axis_name: str, delta: float) -> None:
        x_pos, y_pos = map(float, text_artist.get_position())
        if axis_name == "x":
            text_artist.set_position((x_pos + float(delta), y_pos))
            return
        if axis_name == "y":
            text_artist.set_position((x_pos, y_pos + float(delta)))
            return
        raise ValueError(f"Unsupported preview label axis: {axis_name}")

    def _resolve_preview_dimension_label_overlaps(
        self,
        labels: list[dict[str, object]],
        *,
        extent: tuple[float, float, float, float],
    ) -> None:
        if not labels or self.canvas is None:
            return
        left, right, bottom, top = map(float, extent)
        span_x = max(right - left, 1.0)
        span_y = max(top - bottom, 1.0)
        margin_x = max(0.02 * span_x, 6.0)
        margin_y = max(0.02 * span_y, 6.0)
        step_x = max(0.025 * span_x, 8.0)
        step_y = max(0.025 * span_y, 8.0)

        def _shifted_bbox(bbox: tuple[float, float, float, float], *, axis_name: str, delta: float) -> tuple[float, float, float, float]:
            if axis_name == "x":
                return (bbox[0] + delta, bbox[1], bbox[2] + delta, bbox[3])
            return (bbox[0], bbox[1] + delta, bbox[2], bbox[3] + delta)

        def _bbox_out_of_bounds(bbox: tuple[float, float, float, float]) -> bool:
            return bool(
                bbox[0] < left + margin_x
                or bbox[2] > right - margin_x
                or bbox[1] < bottom + margin_y
                or bbox[3] > top - margin_y
            )

        def _line_delta_allowed(
            meta: dict[str, object],
            *,
            axis_name: str,
            delta: float,
        ) -> bool:
            max_line_delta = meta.get("max_line_delta")
            if max_line_delta is None:
                return True
            line_coord = meta.get("line_coord")
            if line_coord is None:
                return True
            artist = meta["artist"]
            x_pos, y_pos = map(float, artist.get_position())
            next_coord = (x_pos + float(delta)) if axis_name == "x" else (y_pos + float(delta))
            return abs(next_coord - float(line_coord)) <= float(max_line_delta) + 1.0e-9

        def _candidate_delta(
            *,
            meta: dict[str, object],
            bbox: tuple[float, float, float, float],
            axis_name: str,
            base_delta: float,
            preferred_sign: int,
        ) -> float:
            candidate_signs = [preferred_sign] if preferred_sign != 0 else [1, -1]
            if preferred_sign != 0:
                candidate_signs.append(-preferred_sign)
            if axis_name == "x":
                candidate_signs.extend([1, -1])
            else:
                candidate_signs.extend([1, -1])

            seen: set[int] = set()
            ordered_signs: list[int] = []
            for sign in candidate_signs:
                if sign == 0 or sign in seen:
                    continue
                seen.add(sign)
                ordered_signs.append(sign)

            for sign in ordered_signs:
                trial_delta = sign * base_delta
                if not _line_delta_allowed(meta, axis_name=axis_name, delta=trial_delta):
                    continue
                trial_bbox = _shifted_bbox(bbox, axis_name=axis_name, delta=trial_delta)
                if _bbox_out_of_bounds(trial_bbox):
                    continue
                if self._preview_bbox_overlaps_geometry(trial_bbox):
                    continue
                return trial_delta
            for sign in ordered_signs:
                trial_delta = sign * min(base_delta, float(meta.get("max_line_delta") or base_delta))
                if _line_delta_allowed(meta, axis_name=axis_name, delta=trial_delta):
                    return trial_delta
            return 0.0

        def _clamp_artist(text_artist, bbox: tuple[float, float, float, float]) -> bool:
            x_pos, y_pos = map(float, text_artist.get_position())
            moved = False
            if bbox[0] < left + margin_x:
                x_pos += (left + margin_x) - bbox[0]
                moved = True
            if bbox[2] > right - margin_x:
                x_pos -= bbox[2] - (right - margin_x)
                moved = True
            if bbox[1] < bottom + margin_y:
                y_pos += (bottom + margin_y) - bbox[1]
                moved = True
            if bbox[3] > top - margin_y:
                y_pos -= bbox[3] - (top - margin_y)
                moved = True
            if moved:
                text_artist.set_position((x_pos, y_pos))
            return moved

        for _ in range(18):
            self.canvas.draw()
            renderer = self.figure.canvas.get_renderer()
            bboxes = [self._preview_text_bbox_data(meta["artist"], renderer) for meta in labels]
            moved = False

            for meta, bbox in zip(labels, bboxes):
                moved |= _clamp_artist(meta["artist"], bbox)

            if moved:
                continue

            for meta, bbox in zip(labels, bboxes):
                if not self._preview_bbox_overlaps_geometry(bbox):
                    continue
                axis_name = str(meta.get("move_axis"))
                preferred_sign = int(meta.get("preferred_sign", 0)) or 1
                base_delta = step_x if axis_name == "x" else step_y
                delta = _candidate_delta(
                    meta=meta,
                    bbox=bbox,
                    axis_name=axis_name,
                    base_delta=base_delta,
                    preferred_sign=preferred_sign,
                )
                if abs(delta) <= 1.0e-12:
                    continue
                self._shift_preview_label(meta["artist"], axis_name=axis_name, delta=delta)
                moved = True
            if moved:
                continue

            for idx, meta in enumerate(labels):
                bbox = bboxes[idx]
                artist = meta["artist"]
                for other_idx in range(idx):
                    other_bbox = bboxes[other_idx]
                    overlap_x = min(bbox[2], other_bbox[2]) - max(bbox[0], other_bbox[0])
                    overlap_y = min(bbox[3], other_bbox[3]) - max(bbox[1], other_bbox[1])
                    if overlap_x <= 0.0 or overlap_y <= 0.0:
                        continue

                    preferred_sign = int(meta.get("preferred_sign", 0))
                    axis_name = str(meta.get("move_axis"))
                    if axis_name == "x":
                        current_pos = float(artist.get_position()[0])
                        other_pos = float(labels[other_idx]["artist"].get_position()[0])
                        base_delta = overlap_x + step_x
                        sign = 1 if current_pos >= other_pos else -1
                        if preferred_sign != 0 and sign != preferred_sign and abs(current_pos - other_pos) < 0.75 * step_x:
                            sign = preferred_sign
                    else:
                        current_pos = float(artist.get_position()[1])
                        other_pos = float(labels[other_idx]["artist"].get_position()[1])
                        base_delta = overlap_y + step_y
                        sign = 1 if current_pos >= other_pos else -1
                        if preferred_sign != 0 and sign != preferred_sign and abs(current_pos - other_pos) < 0.75 * step_y:
                            sign = preferred_sign
                    delta = _candidate_delta(
                        meta=meta,
                        bbox=bbox,
                        axis_name=axis_name,
                        base_delta=base_delta,
                        preferred_sign=sign,
                    )
                    if abs(delta) <= 1.0e-12:
                        continue
                    self._shift_preview_label(artist, axis_name=axis_name, delta=delta)
                    moved = True
                    break
                if moved:
                    break

            if not moved:
                break

    def _annotate_preview_tunable_parameters(
        self,
        *,
        geometry: TransformerGeometrySpec,
        extent: tuple[float, float, float, float],
    ) -> None:
        label_artists: list[dict[str, object]] = []
        left, right, bottom, top = extent
        span_x = max(right - left, 1.0)
        span_y = max(top - bottom, 1.0)
        primary = geometry.primary
        secondary = geometry.secondary
        primary_center_x = 0.0
        secondary_center_x = float(geometry.offset_um)
        primary_color = "#9a5522"
        secondary_color = "#0b5cab"
        neutral_color = "#5b4636"

        half_primary_w = 0.5 * float(primary.outer_width_um)
        half_secondary_w = 0.5 * float(secondary.outer_width_um)
        half_primary_h = 0.5 * float(primary.outer_height_um)
        half_secondary_h = 0.5 * float(secondary.outer_height_um)
        primary_left = primary_center_x - half_primary_w
        primary_right = primary_center_x + half_primary_w
        secondary_left = secondary_center_x - half_secondary_w
        secondary_right = secondary_center_x + half_secondary_w
        primary_feed_end = primary_left - float(primary.feed_extension_um)
        secondary_feed_end = secondary_right + float(secondary.feed_extension_um)
        primary_terminal_half = 0.5 * float(primary.terminal_y_span_um)
        secondary_terminal_half = 0.5 * float(secondary.terminal_y_span_um)

        primary_width_lane_y = self._clamp_preview_value(top - 0.20 * span_y, bottom + 0.12 * span_y, top - 0.10 * span_y)
        secondary_width_lane_y = self._clamp_preview_value(bottom + 0.18 * span_y, bottom + 0.10 * span_y, top - 0.12 * span_y)
        offset_lane_y = self._clamp_preview_value(top - 0.10 * span_y, bottom + 0.18 * span_y, top - 0.07 * span_y)
        primary_feed_lane_y = self._clamp_preview_value(bottom + 0.32 * span_y, bottom + 0.10 * span_y, top - 0.12 * span_y)
        secondary_feed_lane_y = self._clamp_preview_value(top - 0.32 * span_y, bottom + 0.12 * span_y, top - 0.10 * span_y)
        primary_height_lane_x = self._clamp_preview_value(left + 0.20 * span_x, left + 0.12 * span_x, right - 0.24 * span_x)
        secondary_height_lane_x = self._clamp_preview_value(right - 0.20 * span_x, left + 0.24 * span_x, right - 0.12 * span_x)
        primary_terminal_lane_x = self._clamp_preview_value(left + 0.08 * span_x, left + 0.05 * span_x, right - 0.24 * span_x)
        secondary_terminal_lane_x = self._clamp_preview_value(right - 0.08 * span_x, left + 0.24 * span_x, right - 0.05 * span_x)

        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["primary_outer_width_um"],
            orientation="horizontal",
            color=primary_color,
            span_start=primary_left,
            span_end=primary_right,
            ref_coord=half_primary_h,
            line_coord=primary_width_lane_y,
            normal_span=span_y,
            label_side="positive",
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["secondary_outer_width_um"],
            orientation="horizontal",
            color=secondary_color,
            span_start=secondary_left,
            span_end=secondary_right,
            ref_coord=-half_secondary_h,
            line_coord=secondary_width_lane_y,
            normal_span=span_y,
            label_side="negative",
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["primary_outer_height_um"],
            orientation="vertical",
            color=primary_color,
            span_start=-half_primary_h,
            span_end=half_primary_h,
            ref_coord=primary_left,
            line_coord=primary_height_lane_x,
            normal_span=span_x,
            label_side="positive",
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["secondary_outer_height_um"],
            orientation="vertical",
            color=secondary_color,
            span_start=-half_secondary_h,
            span_end=half_secondary_h,
            ref_coord=secondary_right,
            line_coord=secondary_height_lane_x,
            normal_span=span_x,
            label_side="negative",
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["primary_terminal_y_span_um"],
            orientation="vertical",
            color=primary_color,
            span_start=-primary_terminal_half,
            span_end=primary_terminal_half,
            ref_coord=primary_feed_end,
            line_coord=primary_terminal_lane_x,
            normal_span=span_x,
            label_side="negative",
            max_label_line_delta=max(0.035 * span_x, 12.0),
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["secondary_terminal_y_span_um"],
            orientation="vertical",
            color=secondary_color,
            span_start=-secondary_terminal_half,
            span_end=secondary_terminal_half,
            ref_coord=secondary_feed_end,
            line_coord=secondary_terminal_lane_x,
            normal_span=span_x,
            label_side="positive",
            max_label_line_delta=max(0.035 * span_x, 12.0),
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["primary_feed_extension_um"],
            orientation="horizontal",
            color=primary_color,
            span_start=primary_feed_end,
            span_end=primary_left,
            ref_coord=-primary_terminal_half,
            line_coord=primary_feed_lane_y,
            normal_span=span_y,
            label_side="negative",
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["secondary_feed_extension_um"],
            orientation="horizontal",
            color=secondary_color,
            span_start=secondary_right,
            span_end=secondary_feed_end,
            ref_coord=secondary_terminal_half,
            line_coord=secondary_feed_lane_y,
            normal_span=span_y,
            label_side="positive",
        ))
        label_artists.append(self._draw_preview_dimension(
            label=PARAMETER_LABELS["offset_um"],
            orientation="horizontal",
            color=neutral_color,
            span_start=primary_center_x,
            span_end=secondary_center_x,
            ref_coord=0.0,
            line_coord=offset_lane_y,
            normal_span=span_y,
            label_side="positive",
        ))

        def _draw_trace_dimensions(inductor, *, center_x: float, side: str, color: str, width_key: str, spacing_key: str) -> None:
            outer_half_width = 0.5 * float(inductor.outer_width_um)
            outer_half_height = 0.5 * float(inductor.outer_height_um)
            chamfer = min(outer_half_width, outer_half_height) * (math.sqrt(2.0) - 1.0)
            flat_half_span = outer_half_width - chamfer
            probe_shift = max(0.42 * flat_half_span, 12.0)
            probe_x = center_x + (-probe_shift if side == "left" else probe_shift)
            line_shift = max(0.04 * span_x, 10.0)
            width_line_x = probe_x + (-0.75 * line_shift if side == "left" else 0.75 * line_shift)
            width_label_side = "negative" if side == "left" else "positive"

            outer_top = outer_half_height
            outer_top_inner = outer_top - float(inductor.trace_width_um)
            label_artists.append(self._draw_preview_dimension(
                label=PARAMETER_LABELS[width_key],
                orientation="vertical",
                color=color,
                span_start=outer_top_inner,
                span_end=outer_top,
                ref_coord=probe_x,
                line_coord=width_line_x,
                normal_span=span_x,
                label_side=width_label_side,
            ))
            if int(inductor.turns) <= 1:
                return
            spacing_line_x = probe_x + (-2.8 * line_shift if side == "left" else 2.8 * line_shift)
            next_outer_top = outer_top - float(inductor.trace_width_um) - float(inductor.spacing_um)
            spacing_label_side = "negative" if side == "left" else "positive"
            label_artists.append(self._draw_preview_dimension(
                label=PARAMETER_LABELS[spacing_key],
                orientation="vertical",
                color=color,
                span_start=next_outer_top,
                span_end=outer_top_inner,
                ref_coord=probe_x,
                line_coord=spacing_line_x,
                normal_span=span_x,
                label_side=spacing_label_side,
            ))

        _draw_trace_dimensions(
            primary,
            center_x=primary_center_x,
            side="left",
            color=primary_color,
            width_key="primary_width_um",
            spacing_key="primary_spacing_um",
        )
        _draw_trace_dimensions(
            secondary,
            center_x=secondary_center_x,
            side="right",
            color=secondary_color,
            width_key="secondary_width_um",
            spacing_key="secondary_spacing_um",
        )
        self._resolve_preview_dimension_label_overlaps(label_artists, extent=extent)

    def _draw_preview_image(
        self,
        *,
        image: np.ndarray,
        geometry: TransformerGeometrySpec,
        layout,
        title: str,
    ) -> None:
        self.preview_group.setTitle(title)
        self.axis.clear()
        self.axis.set_facecolor("#f6f2ec")
        extent = self._preview_extent_for_geometry(geometry=geometry, layout=layout)
        display_image = np.flipud(image)
        self.current_preview_geometry = geometry
        self.current_preview_layout = layout
        self.current_preview_display_image = display_image
        self.current_preview_geometry_mask = self._preview_geometry_mask(display_image)
        self.current_preview_extent = extent
        self.axis.imshow(display_image, extent=extent, origin="lower")
        self.axis.set_xlim(extent[0], extent[1])
        self.axis.set_ylim(extent[2], extent[3])
        if self.preview_show_dimensions:
            self._annotate_preview_tunable_parameters(geometry=geometry, extent=extent)
        self.axis.set_axis_off()
        self.figure.tight_layout(pad=0.1)
        self._draw_canvas_idle(self.canvas)

    def _make_demo_optimization_result(
        self,
        *,
        cache_key: str,
        work_dir: Path,
        center_freq_ghz: float,
        lp_nh: float,
        ls_nh: float,
        q_primary_peak: float,
        q_secondary_peak: float,
        k_peak: float,
        total_cost: float,
        frequency_metric_curves: dict[str, np.ndarray] | None = None,
        target_impedance_ohm: float = 100.0,
    ):
        from rfic_transformer_inverse_design.network_analysis import z_to_s

        if frequency_metric_curves is None:
            freqs_ghz = np.linspace(10.0, 20.0, 17, dtype=float)
            normalized_offset = (freqs_ghz - float(center_freq_ghz)) / 3.0
            profile = np.exp(-0.5 * normalized_offset * normalized_offset)
            lp_h_curve = 1.0e-9 * (float(lp_nh) + 0.06 * profile)
            ls_h_curve = 1.0e-9 * (float(ls_nh) + 0.05 * profile)
            k_curve = np.clip(float(k_peak) - 0.08 * normalized_offset * normalized_offset, 0.2, 0.95)
            q_primary_curve = np.maximum(float(q_primary_peak) - 1.6 * normalized_offset * normalized_offset, 2.0)
            q_secondary_curve = np.maximum(float(q_secondary_peak) - 1.4 * normalized_offset * normalized_offset, 2.0)
            frequency_metric_curves = {
                "freqs_ghz": freqs_ghz,
                "lp_h": lp_h_curve,
                "ls_h": ls_h_curve,
                "q_primary": q_primary_curve,
                "q_secondary": q_secondary_curve,
                "k": k_curve,
            }
        freqs_ghz = np.asarray(frequency_metric_curves["freqs_ghz"], dtype=float)
        lp_h_curve = np.asarray(frequency_metric_curves["lp_h"], dtype=float)
        ls_h_curve = np.asarray(frequency_metric_curves["ls_h"], dtype=float)
        q_primary_curve = np.asarray(frequency_metric_curves["q_primary"], dtype=float)
        q_secondary_curve = np.asarray(frequency_metric_curves["q_secondary"], dtype=float)
        k_curve = np.asarray(frequency_metric_curves["k"], dtype=float)
        freqs_hz = freqs_ghz * 1.0e9
        omega = 2.0 * math.pi * freqs_hz
        mutual_h_curve = k_curve * np.sqrt(np.maximum(lp_h_curve * ls_h_curve, 0.0))
        r11_curve = omega * lp_h_curve / q_primary_curve
        r22_curve = omega * ls_h_curve / q_secondary_curve

        z_diff = np.zeros((len(freqs_hz), 2, 2), dtype=np.complex128)
        z_diff[:, 0, 0] = r11_curve + 1j * omega * lp_h_curve
        z_diff[:, 1, 1] = r22_curve + 1j * omega * ls_h_curve
        coupling_resistance = 0.18 * np.sqrt(np.maximum(r11_curve * r22_curve, 0.0))
        z_diff[:, 0, 1] = coupling_resistance + 1j * omega * mutual_h_curve
        z_diff[:, 1, 0] = z_diff[:, 0, 1]
        differential_sparams = SParameterResult(freqs_hz=freqs_hz, s_matrix=z_to_s(z_diff, z0=100.0))

        peak_index = int(np.argmin(np.abs(freqs_ghz - float(center_freq_ghz))))
        metrics = SimpleNamespace(
            lp_h=float(lp_h_curve[peak_index]),
            ls_h=float(ls_h_curve[peak_index]),
            k=float(k_curve[peak_index]),
            q_primary=float(q_primary_curve[peak_index]),
            q_secondary=float(q_secondary_curve[peak_index]),
        )
        objective = SimpleNamespace(total_cost=float(total_cost))
        return SimpleNamespace(
            cache_key=cache_key,
            work_dir=work_dir,
            touchstone_path=work_dir / "emx.s4p",
            geometry=self.bounds.midpoint(),
            layout=None,
            metrics=metrics,
            objective=objective,
            target=SimpleNamespace(differential_reference_impedance_ohm=float(target_impedance_ohm)),
            single_ended_sparams=None,
            differential_sparams=differential_sparams,
            differential_z=z_diff,
            frequency_metric_curves={
                "freqs_ghz": freqs_ghz,
                "lp_h": lp_h_curve,
                "ls_h": ls_h_curve,
                "q_primary": q_primary_curve,
                "q_secondary": q_secondary_curve,
                "k": k_curve,
            },
            command=["emx", "<demo>"],
            geometry_check={"ok": True},
            error=None,
        )

    def _load_real_demo_wideband_result(self):
        summary_path = Path("tmp/manual_sweeps/best_4b072e1594ddbdbb_1to70ghz_20260420/source_summary.json")
        curves_path = summary_path.with_name("klq_vs_f.npz")
        touchstone_path = summary_path.with_name("emx_reduced.s4p")
        if not summary_path.exists() or not curves_path.exists():
            return None
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            curve_payload = np.load(curves_path)
            frequency_metric_curves = {
                "freqs_ghz": np.asarray(curve_payload["freqs_ghz"], dtype=float),
                "k": np.asarray(curve_payload["k"], dtype=float),
                "lp_h": np.asarray(curve_payload["lp_h"], dtype=float),
                "ls_h": np.asarray(curve_payload["ls_h"], dtype=float),
                "q_primary": np.asarray(curve_payload["q_primary"], dtype=float),
                "q_secondary": np.asarray(curve_payload["q_secondary"], dtype=float),
            }
            metrics_payload = dict(summary.get("metrics", {}))
            objective_payload = dict(summary.get("objective", {}))
            target_payload = dict(summary.get("target", {}))
            work_dir = summary_path.parent
            result = self._make_demo_optimization_result(
                cache_key=str(summary.get("cache_key", "wideband-demo")),
                work_dir=work_dir,
                center_freq_ghz=float(target_payload.get("f0_hz", 15.0e9)) / 1.0e9,
                lp_nh=1.0e9 * float(metrics_payload.get("lp_h", 0.8e-9)),
                ls_nh=1.0e9 * float(metrics_payload.get("ls_h", 0.6e-9)),
                q_primary_peak=float(metrics_payload.get("q_primary", 12.0)),
                q_secondary_peak=float(metrics_payload.get("q_secondary", 16.0)),
                k_peak=float(metrics_payload.get("k", 0.64)),
                total_cost=float(objective_payload.get("total_cost", 0.1)),
                frequency_metric_curves=frequency_metric_curves,
                target_impedance_ohm=float(target_payload.get("differential_reference_impedance_ohm", 100.0)),
            )
            result.touchstone_path = touchstone_path if touchstone_path.exists() else None
            result.command = ["emx", str(touchstone_path)] if touchstone_path.exists() else ["emx", "<wideband-demo>"]
            result.demo_label = "Real wideband EMX sweep"
            return result
        except Exception:
            return None

    def _seed_demo_optimization_view(self) -> None:
        demo_root = self.preview_dir / "demo_optimization_view"
        demo_root.mkdir(parents=True, exist_ok=True)
        real_wideband_result = self._load_real_demo_wideband_result()
        demo_specs = (
            (1, 0.184, True,  15.4, 1.11, 1.28, 16.8, 18.2, 0.69),
            (2, 0.149, False, 15.2, 1.14, 1.31, 17.5, 19.0, 0.71),
            (3, 0.118, True,  15.1, 1.16, 1.35, 18.9, 20.6, 0.76),
            (4, 0.132, False, 15.0, 1.15, 1.33, 18.1, 19.7, 0.74),
            (5, 0.101, True,  14.9, 1.17, 1.39, 20.4, 22.0, 0.79),
            (6, 0.096, True,  15.0, 1.18, 1.41, 21.1, 22.7, 0.81),
            (7, 0.109, False, 15.1, 1.17, 1.38, 20.2, 21.8, 0.78),
            (8, 0.089, True,  15.0, 1.19, 1.43, 22.0, 23.6, 0.83),
        )
        self.optimization_run_dir = demo_root
        self.optimization_launch_snapshot_name = "cma_es"
        self.optimization_launch_snapshot_yaml = "demo optimization snapshot"
        self.optimization_log_lines = [
            "Demo optimization view seeded from synthetic results.",
            "Use this only for screenshots.",
        ]
        if real_wideband_result is not None:
            self.optimization_log_lines.insert(0, f"Loaded real wideband EMX sweep: {real_wideband_result.cache_key}")
        self.optimization_blocking_errors = []
        self.optimization_history = []
        self.optimization_eval_records = []
        self.optimization_manual_selected_eval = None
        self.optimization_selected_result = None
        self.optimization_preview_mode = "best"
        best_cost = math.inf
        best_result = None
        last_result = None
        for eval_count, cost, is_best_hint, center_freq_ghz, lp_nh, ls_nh, qp, qs, k_peak in demo_specs:
            result = self._make_demo_optimization_result(
                cache_key=f"demo-{eval_count:02d}",
                work_dir=demo_root / f"eval_{eval_count:02d}",
                center_freq_ghz=center_freq_ghz,
                lp_nh=lp_nh,
                ls_nh=ls_nh,
                q_primary_peak=qp,
                q_secondary_peak=qs,
                k_peak=k_peak,
                total_cost=cost,
            )
            best_cost = min(best_cost, float(cost))
            is_best = bool(is_best_hint) or float(cost) <= best_cost
            self.optimization_history.append((int(eval_count), float(cost), float(best_cost)))
            self.optimization_eval_records.append(
                OptimizationEvalRecord(
                    evaluation_count=int(eval_count),
                    unique_evaluation_count=int(eval_count),
                    backend_name="cma_es",
                    elapsed_seconds=14.0 * float(eval_count),
                    is_best=is_best,
                    cost=float(cost),
                    result=result,
                )
            )
            if is_best:
                best_result = result
            last_result = result
        if real_wideband_result is not None:
            real_eval = len(self.optimization_history) + 1
            real_cost = float(getattr(getattr(real_wideband_result, "objective", None), "total_cost", 0.082))
            best_cost = min(best_cost, real_cost)
            self.optimization_history.append((int(real_eval), real_cost, float(best_cost)))
            self.optimization_eval_records.append(
                OptimizationEvalRecord(
                    evaluation_count=int(real_eval),
                    unique_evaluation_count=int(real_eval),
                    backend_name="cma_es",
                    elapsed_seconds=14.0 * float(real_eval),
                    is_best=True,
                    cost=real_cost,
                    result=real_wideband_result,
                )
            )
            best_result = real_wideband_result
            last_result = real_wideband_result
        self.optimization_best_result = best_result
        self.optimization_last_result = last_result
        self._set_optimization_status("Demo optimization results loaded for screenshot preview.")
        self._set_optimization_controls_running(False)
        self._draw_convergence_plot()
        self._draw_best_metrics_plot()
        self._refresh_optimization_text()
        self._refresh_optimization_eval_table()
        self._refresh_selected_optimization_detail()
        if self.optimization_preview_mode_combo is not None:
            self.optimization_preview_mode_combo.setCurrentIndex(0)
        if self.top_tabs is not None and self.optimization_viewer_group is not None:
            self.top_tabs.setCurrentWidget(self.optimization_viewer_group)

    def _on_3d_show_dielectrics_toggled(self, checked: bool) -> None:
        self.viewer3d_show_dielectrics = bool(checked)
        self._apply_dielectric_visibility()
        if self.viewer3d is not None:
            self.viewer3d.render()

    def _refresh_3d_view_for_overlay_toggle(self) -> None:
        layout = self.current_preview_layout
        if layout is None:
            return
        self.viewer3d_last_source = None
        if self._is_3d_tab_active():
            self._set_3d_view_from_layout(layout)
            return
        self._schedule_3d_view_refresh(layout)

    def _on_3d_show_dimensions_toggled(self, checked: bool) -> None:
        self.viewer3d_show_dimensions = bool(checked)
        self._refresh_3d_view_for_overlay_toggle()

    def _on_3d_view_mode_toggled(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self.viewer3d_view_mode = str(mode).lower()
        self._apply_3d_viewer_camera_mode(reset_camera=True, render=True)

    def _apply_3d_viewer_camera_mode(self, *, reset_camera: bool, render: bool) -> None:
        if self.viewer3d is None:
            return
        mode = str(self.viewer3d_view_mode).lower()
        if mode == "2d":
            if hasattr(self.viewer3d, "view_xy"):
                self.viewer3d.view_xy()
            if hasattr(self.viewer3d, "enable_parallel_projection"):
                self.viewer3d.enable_parallel_projection()
            if hasattr(self.viewer3d, "enable_2d_style"):
                self.viewer3d.enable_2d_style()
        else:
            if hasattr(self.viewer3d, "view_isometric"):
                self.viewer3d.view_isometric()
            if hasattr(self.viewer3d, "disable_parallel_projection"):
                self.viewer3d.disable_parallel_projection()
            if hasattr(self.viewer3d, "enable_trackball_style"):
                self.viewer3d.enable_trackball_style()
        if hasattr(self.viewer3d, "set_scale"):
            self.viewer3d.set_scale(xscale=1.0, yscale=1.0, zscale=0.72, reset_camera=False)
        if reset_camera and hasattr(self.viewer3d, "reset_camera"):
            self.viewer3d.reset_camera()
        if render and hasattr(self.viewer3d, "render"):
            self.viewer3d.render()

    def _configure_3d_viewer_rendering(self) -> None:
        if self.viewer3d is None:
            return
        self.viewer3d.set_background("#f6f2ec")
        if hasattr(self.viewer3d, "enable_depth_peeling"):
            self.viewer3d.enable_depth_peeling(number_of_peels=8, occlusion_ratio=0.0)
        if hasattr(self.viewer3d, "enable_lightkit"):
            self.viewer3d.enable_lightkit(only_active=False)
        if hasattr(self.viewer3d, "disable_shadows"):
            self.viewer3d.disable_shadows()
        renderer = getattr(self.viewer3d, "renderer", None)
        if renderer is not None:
            if hasattr(renderer, "UseShadowsOff"):
                renderer.UseShadowsOff()
            elif hasattr(renderer, "SetUseShadows"):
                renderer.SetUseShadows(False)
            if hasattr(renderer, "Modified"):
                renderer.Modified()
        self._apply_3d_viewer_camera_mode(reset_camera=False, render=False)

    def _build_3d_dimension_specs(
        self,
        *,
        geometry: TransformerGeometrySpec,
        bounds_xy: tuple[float, float, float, float],
    ) -> list[dict[str, object]]:
        left, right, bottom, top = map(float, bounds_xy)
        span_x = max(right - left, 1.0)
        span_y = max(top - bottom, 1.0)
        primary = geometry.primary
        secondary = geometry.secondary
        primary_center_x = 0.0
        secondary_center_x = float(geometry.offset_um)
        primary_color = "#9a5522"
        secondary_color = "#0b5cab"
        neutral_color = "#5b4636"

        half_primary_w = 0.5 * float(primary.outer_width_um)
        half_secondary_w = 0.5 * float(secondary.outer_width_um)
        half_primary_h = 0.5 * float(primary.outer_height_um)
        half_secondary_h = 0.5 * float(secondary.outer_height_um)
        primary_left = primary_center_x - half_primary_w
        primary_right = primary_center_x + half_primary_w
        secondary_left = secondary_center_x - half_secondary_w
        secondary_right = secondary_center_x + half_secondary_w
        primary_feed_end = primary_left - float(primary.feed_extension_um)
        secondary_feed_end = secondary_right + float(secondary.feed_extension_um)
        primary_terminal_half = 0.5 * float(primary.terminal_y_span_um)
        secondary_terminal_half = 0.5 * float(secondary.terminal_y_span_um)

        primary_width_lane_y = self._clamp_preview_value(top - 0.20 * span_y, bottom + 0.12 * span_y, top - 0.10 * span_y)
        secondary_width_lane_y = self._clamp_preview_value(bottom + 0.18 * span_y, bottom + 0.10 * span_y, top - 0.12 * span_y)
        offset_lane_y = self._clamp_preview_value(top - 0.10 * span_y, bottom + 0.18 * span_y, top - 0.07 * span_y)
        primary_feed_lane_y = self._clamp_preview_value(bottom + 0.32 * span_y, bottom + 0.10 * span_y, top - 0.12 * span_y)
        secondary_feed_lane_y = self._clamp_preview_value(top - 0.32 * span_y, bottom + 0.12 * span_y, top - 0.10 * span_y)
        primary_height_lane_x = self._clamp_preview_value(left + 0.20 * span_x, left + 0.12 * span_x, right - 0.24 * span_x)
        secondary_height_lane_x = self._clamp_preview_value(right - 0.20 * span_x, left + 0.24 * span_x, right - 0.12 * span_x)
        primary_terminal_lane_x = self._clamp_preview_value(left + 0.08 * span_x, left + 0.05 * span_x, right - 0.24 * span_x)
        secondary_terminal_lane_x = self._clamp_preview_value(right - 0.08 * span_x, left + 0.24 * span_x, right - 0.05 * span_x)

        specs: list[dict[str, object]] = [
            {
                "label": PARAMETER_LABELS["primary_outer_width_um"],
                "orientation": "horizontal",
                "color": primary_color,
                "span_start": primary_left,
                "span_end": primary_right,
                "ref_coord": half_primary_h,
                "line_coord": primary_width_lane_y,
            },
            {
                "label": PARAMETER_LABELS["secondary_outer_width_um"],
                "orientation": "horizontal",
                "color": secondary_color,
                "span_start": secondary_left,
                "span_end": secondary_right,
                "ref_coord": -half_secondary_h,
                "line_coord": secondary_width_lane_y,
            },
            {
                "label": PARAMETER_LABELS["primary_outer_height_um"],
                "orientation": "vertical",
                "color": primary_color,
                "span_start": -half_primary_h,
                "span_end": half_primary_h,
                "ref_coord": primary_left,
                "line_coord": primary_height_lane_x,
            },
            {
                "label": PARAMETER_LABELS["secondary_outer_height_um"],
                "orientation": "vertical",
                "color": secondary_color,
                "span_start": -half_secondary_h,
                "span_end": half_secondary_h,
                "ref_coord": secondary_right,
                "line_coord": secondary_height_lane_x,
            },
            {
                "label": PARAMETER_LABELS["primary_terminal_y_span_um"],
                "orientation": "vertical",
                "color": primary_color,
                "span_start": -primary_terminal_half,
                "span_end": primary_terminal_half,
                "ref_coord": primary_feed_end,
                "line_coord": primary_terminal_lane_x,
            },
            {
                "label": PARAMETER_LABELS["secondary_terminal_y_span_um"],
                "orientation": "vertical",
                "color": secondary_color,
                "span_start": -secondary_terminal_half,
                "span_end": secondary_terminal_half,
                "ref_coord": secondary_feed_end,
                "line_coord": secondary_terminal_lane_x,
            },
            {
                "label": PARAMETER_LABELS["primary_feed_extension_um"],
                "orientation": "horizontal",
                "color": primary_color,
                "span_start": primary_feed_end,
                "span_end": primary_left,
                "ref_coord": -primary_terminal_half,
                "line_coord": primary_feed_lane_y,
            },
            {
                "label": PARAMETER_LABELS["secondary_feed_extension_um"],
                "orientation": "horizontal",
                "color": secondary_color,
                "span_start": secondary_right,
                "span_end": secondary_feed_end,
                "ref_coord": secondary_terminal_half,
                "line_coord": secondary_feed_lane_y,
            },
            {
                "label": PARAMETER_LABELS["offset_um"],
                "orientation": "horizontal",
                "color": neutral_color,
                "span_start": primary_center_x,
                "span_end": secondary_center_x,
                "ref_coord": 0.0,
                "line_coord": offset_lane_y,
            },
        ]

        def _append_trace_specs(inductor, *, center_x: float, side: str, color: str, width_key: str, spacing_key: str) -> None:
            outer_half_width = 0.5 * float(inductor.outer_width_um)
            outer_half_height = 0.5 * float(inductor.outer_height_um)
            chamfer = min(outer_half_width, outer_half_height) * (math.sqrt(2.0) - 1.0)
            flat_half_span = outer_half_width - chamfer
            probe_shift = max(0.42 * flat_half_span, 12.0)
            probe_x = center_x + (-probe_shift if side == "left" else probe_shift)
            line_shift = max(0.04 * span_x, 10.0)
            width_line_x = probe_x + (-0.75 * line_shift if side == "left" else 0.75 * line_shift)

            outer_top = outer_half_height
            outer_top_inner = outer_top - float(inductor.trace_width_um)
            specs.append(
                {
                    "label": PARAMETER_LABELS[width_key],
                    "orientation": "vertical",
                    "color": color,
                    "span_start": outer_top_inner,
                    "span_end": outer_top,
                    "ref_coord": probe_x,
                    "line_coord": width_line_x,
                }
            )
            if int(inductor.turns) <= 1:
                return
            spacing_line_x = probe_x + (-1.45 * line_shift if side == "left" else 1.45 * line_shift)
            next_outer_top = outer_top - float(inductor.trace_width_um) - float(inductor.spacing_um)
            specs.append(
                {
                    "label": PARAMETER_LABELS[spacing_key],
                    "orientation": "vertical",
                    "color": color,
                    "span_start": next_outer_top,
                    "span_end": outer_top_inner,
                    "ref_coord": probe_x,
                    "line_coord": spacing_line_x,
                }
            )

        _append_trace_specs(
            primary,
            center_x=primary_center_x,
            side="left",
            color=primary_color,
            width_key="primary_width_um",
            spacing_key="primary_spacing_um",
        )
        _append_trace_specs(
            secondary,
            center_x=secondary_center_x,
            side="right",
            color=secondary_color,
            width_key="secondary_width_um",
            spacing_key="secondary_spacing_um",
        )
        return specs

    @staticmethod
    def _build_3d_dimension_segments(
        *,
        orientation: str,
        span_start: float,
        span_end: float,
        ref_coord: float,
        line_coord: float,
        anchor_z: float,
        line_z: float,
        tick_span: float,
    ) -> tuple[list[tuple[tuple[float, float, float], tuple[float, float, float]]], tuple[float, float, float]]:
        if orientation == "horizontal":
            x0 = float(span_start)
            x1 = float(span_end)
            if x1 < x0:
                x0, x1 = x1, x0
            if abs(x1 - x0) < 1.0e-9:
                x0 -= 0.5 * tick_span
                x1 += 0.5 * tick_span
            y_ref = float(ref_coord)
            y_line = float(line_coord)
            z0 = float(anchor_z)
            z1 = float(line_z)
            segments = [
                ((x0, y_ref, z0), (x0, y_ref, z1)),
                ((x0, y_ref, z1), (x0, y_line, z1)),
                ((x1, y_ref, z0), (x1, y_ref, z1)),
                ((x1, y_ref, z1), (x1, y_line, z1)),
                ((x0, y_line, z1), (x1, y_line, z1)),
                ((x0, y_line - 0.5 * tick_span, z1), (x0, y_line + 0.5 * tick_span, z1)),
                ((x1, y_line - 0.5 * tick_span, z1), (x1, y_line + 0.5 * tick_span, z1)),
            ]
            return segments, (0.5 * (x0 + x1), y_line, z1)

        if orientation == "vertical":
            y0 = float(span_start)
            y1 = float(span_end)
            if y1 < y0:
                y0, y1 = y1, y0
            if abs(y1 - y0) < 1.0e-9:
                y0 -= 0.5 * tick_span
                y1 += 0.5 * tick_span
            x_ref = float(ref_coord)
            x_line = float(line_coord)
            z0 = float(anchor_z)
            z1 = float(line_z)
            segments = [
                ((x_ref, y0, z0), (x_ref, y0, z1)),
                ((x_ref, y0, z1), (x_line, y0, z1)),
                ((x_ref, y1, z0), (x_ref, y1, z1)),
                ((x_ref, y1, z1), (x_line, y1, z1)),
                ((x_line, y0, z1), (x_line, y1, z1)),
                ((x_line - 0.5 * tick_span, y0, z1), (x_line + 0.5 * tick_span, y0, z1)),
                ((x_line - 0.5 * tick_span, y1, z1), (x_line + 0.5 * tick_span, y1, z1)),
            ]
            return segments, (x_line, 0.5 * (y0 + y1), z1)

        raise ValueError(f"Unsupported 3D dimension orientation: {orientation}")

    @staticmethod
    def _merged_3d_line_mesh(
        segments: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
    ):
        if pv is None or not segments:
            return None
        meshes = [pv.Line(np.asarray(start, dtype=float), np.asarray(end, dtype=float)) for start, end in segments]
        return pv.merge(meshes) if len(meshes) > 1 else meshes[0]

    def _add_3d_dimension_point_label(self, *, point: tuple[float, float, float], text: str, color: str, name: str) -> None:
        if self.viewer3d is None:
            return
        add_point_labels = getattr(self.viewer3d, "add_point_labels", None)
        if not callable(add_point_labels):
            return
        points = np.asarray([point], dtype=float)
        labels = [text]
        attempts = (
            {
                "text_color": color,
                "font_size": 11,
                "show_points": False,
                "always_visible": True,
                "shape_opacity": 0.0,
                "margin": 0,
                "pickable": False,
                "name": name,
            },
            {
                "text_color": color,
                "font_size": 11,
                "show_points": False,
                "always_visible": True,
                "shape_opacity": 0.0,
                "margin": 0,
                "name": name,
            },
            {
                "text_color": color,
                "font_size": 11,
                "show_points": False,
                "always_visible": True,
            },
        )
        for kwargs in attempts:
            try:
                add_point_labels(points, labels, **kwargs)
                return
            except TypeError:
                continue

    def _render_3d_dimension_overlays(
        self,
        *,
        geometry: TransformerGeometrySpec,
        bounds_xy: tuple[float, float, float, float],
        anchor_z: float,
        visual_height: float,
    ) -> None:
        if self.viewer3d is None:
            return
        span_x = max(float(bounds_xy[1]) - float(bounds_xy[0]), 1.0)
        span_y = max(float(bounds_xy[3]) - float(bounds_xy[2]), 1.0)
        lateral_span = max(span_x, span_y, 1.0)
        line_z = float(anchor_z) + max(2.0, min(0.035 * lateral_span, 24.0), 0.08 * max(float(visual_height), 1.0))
        tick_span = max(0.018 * min(span_x, span_y), 4.0)

        for index, spec in enumerate(self._build_3d_dimension_specs(geometry=geometry, bounds_xy=bounds_xy)):
            segments, label_point = self._build_3d_dimension_segments(
                orientation=str(spec["orientation"]),
                span_start=float(spec["span_start"]),
                span_end=float(spec["span_end"]),
                ref_coord=float(spec["ref_coord"]),
                line_coord=float(spec["line_coord"]),
                anchor_z=float(anchor_z),
                line_z=line_z,
                tick_span=tick_span,
            )
            line_mesh = self._merged_3d_line_mesh(segments)
            if line_mesh is None:
                continue
            add_mesh_attempts = (
                {
                    "color": str(spec["color"]),
                    "opacity": 0.98,
                    "line_width": 2.2,
                    "lighting": False,
                    "render_lines_as_tubes": False,
                    "pickable": False,
                    "name": f"dimension_line_{index}",
                },
                {
                    "color": str(spec["color"]),
                    "opacity": 0.98,
                    "line_width": 2.2,
                    "lighting": False,
                    "render_lines_as_tubes": False,
                    "name": f"dimension_line_{index}",
                },
                {
                    "color": str(spec["color"]),
                    "opacity": 0.98,
                    "line_width": 2.2,
                    "name": f"dimension_line_{index}",
                },
            )
            actor = None
            for kwargs in add_mesh_attempts:
                try:
                    actor = self.viewer3d.add_mesh(line_mesh, **kwargs)
                    break
                except TypeError:
                    continue
            if actor is None:
                continue
            if hasattr(actor, "PickableOff"):
                actor.PickableOff()
            self._add_3d_dimension_point_label(
                point=label_point,
                text=self._preview_dimension_text(str(spec["label"])),
                color=str(spec["color"]),
                name=f"dimension_label_{index}",
            )

    def _save_3d_view(self) -> None:
        initial_path = self.preview_dir / "transformer_stackup_view.png"
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save 3D viewer image",
            str(initial_path),
            "PNG files (*.png);;JPEG files (*.jpg *.jpeg);;BMP files (*.bmp);;All files (*.*)",
        )
        if not selected:
            return
        target_path = Path(selected).expanduser().resolve()
        try:
            screenshot = getattr(self.viewer3d, "screenshot", None) if self.viewer3d is not None else None
            if callable(screenshot):
                screenshot(str(target_path))
            else:
                source_widget = self.viewer3d
                if source_widget is None:
                    source_widget = self.viewer3d_placeholder or getattr(self, "viewer3d_group", None)
                if source_widget is None or not hasattr(source_widget, "grab"):
                    raise RuntimeError("3D viewer image capture is unavailable in this Qt/runtime configuration")
                captured = source_widget.grab()
                if captured.isNull():
                    raise RuntimeError("3D viewer image capture returned an empty frame")
                if not captured.save(str(target_path)):
                    raise RuntimeError("Qt could not encode the selected image format")
        except Exception as exc:
            self._set_3d_status(f"Cannot save 3D viewer image: {exc}")
            return
        self._set_3d_status(f"Saved 3D viewer image to {target_path}")

    def _apply_dielectric_visibility(self) -> None:
        show_dielectrics = bool(self.viewer3d_show_dielectrics)
        for metadata in self.viewer3d_actor_defaults.values():
            if metadata.get("kind") != "Dielectric":
                continue
            actor = metadata.get("actor")
            if actor is None:
                continue
            if hasattr(actor, "SetVisibility"):
                actor.SetVisibility(1 if show_dielectrics else 0)

    @staticmethod
    def _actor_id(actor) -> str | None:
        if actor is None or not hasattr(actor, "GetAddressAsString"):
            return None
        return str(actor.GetAddressAsString(""))

    def _register_3d_actor(
        self,
        actor,
        *,
        opacity: float,
        label: str,
        kind: str,
        selectable: bool = True,
        z_min: float | None = None,
        z_max: float | None = None,
    ) -> None:
        actor_id = self._actor_id(actor)
        if actor_id is None:
            return
        self.viewer3d_actor_defaults[actor_id] = {
            "actor": actor,
            "opacity": float(opacity),
            "label": str(label),
            "kind": str(kind),
            "selectable": bool(selectable),
            "z_min": None if z_min is None else float(z_min),
            "z_max": None if z_max is None else float(z_max),
        }

    def _restore_3d_actor(self, actor_id: str) -> None:
        metadata = self.viewer3d_actor_defaults.get(str(actor_id))
        if metadata is None:
            return
        actor = metadata.get("actor")
        if actor is None:
            return
        actor.GetProperty().SetOpacity(float(metadata["opacity"]))

    def _set_dielectric_focus_opacity(self, *, focused_actor_id: str | None) -> None:
        focus_mid_z = None
        if focused_actor_id is not None:
            focused = self.viewer3d_actor_defaults.get(str(focused_actor_id))
            if focused is not None and focused.get("z_min") is not None and focused.get("z_max") is not None:
                focus_mid_z = 0.5 * (float(focused["z_min"]) + float(focused["z_max"]))
        for metadata in self.viewer3d_actor_defaults.values():
            if metadata.get("kind") != "Dielectric":
                continue
            actor = metadata.get("actor")
            if actor is None:
                continue
            base_opacity = float(metadata["opacity"])
            if focus_mid_z is None:
                actor.GetProperty().SetOpacity(base_opacity)
                continue
            dielectric_mid_z = None
            if metadata.get("z_min") is not None and metadata.get("z_max") is not None:
                dielectric_mid_z = 0.5 * (float(metadata["z_min"]) + float(metadata["z_max"]))
            if dielectric_mid_z is not None and dielectric_mid_z > focus_mid_z:
                actor.GetProperty().SetOpacity(min(base_opacity * 0.18, 0.05))
            else:
                actor.GetProperty().SetOpacity(base_opacity)

    def _clear_3d_actor_registry(self) -> None:
        self.viewer3d_actor_defaults.clear()
        self.viewer3d_selected_actor_id = None

    def _toggle_3d_actor_selection(self, actor) -> None:
        actor_id = self._actor_id(actor)
        if actor_id is None or actor_id not in self.viewer3d_actor_defaults:
            return
        metadata = self.viewer3d_actor_defaults[actor_id]
        if not bool(metadata.get("selectable", True)):
            return
        if self.viewer3d_selected_actor_id == actor_id:
            self._restore_3d_actor(actor_id)
            self._set_dielectric_focus_opacity(focused_actor_id=None)
            self.viewer3d_selected_actor_id = None
            self._set_3d_status(f"{metadata['kind']} {metadata['label']} restored to default transparency.")
        else:
            if self.viewer3d_selected_actor_id is not None:
                self._restore_3d_actor(self.viewer3d_selected_actor_id)
            actor.GetProperty().SetOpacity(1.0)
            self._set_dielectric_focus_opacity(focused_actor_id=actor_id)
            self.viewer3d_selected_actor_id = actor_id
            self._set_3d_status(f"{metadata['kind']} {metadata['label']} set to solid opacity.")
        if self.viewer3d is not None:
            self.viewer3d.render()

    def _pick_3d_actor_from_event(self, source, event):
        if self.viewer3d is None or vtk is None:
            return None
        if hasattr(event, "position"):
            position = event.position()
            x_pos = float(position.x())
            y_pos = float(position.y())
        else:
            position = event.pos()
            x_pos = float(position.x())
            y_pos = float(position.y())
        picker = vtk.vtkPropPicker()
        picker.Pick(float(x_pos), float(max(source.height() - y_pos - 1.0, 0.0)), 0.0, self.viewer3d.renderer)
        return picker.GetActor()

    def _clear_3d_view(self, *, reason: str | None = None) -> None:
        self.viewer3d_last_source = None
        self.viewer3d_pending_layout = None
        self._clear_3d_actor_registry()
        if self.viewer3d is not None:
            self.viewer3d.clear()
            self.viewer3d.add_axes()
        if reason:
            self._set_3d_status(reason)

    def _is_3d_tab_active(self) -> bool:
        viewer3d_group = getattr(self, "viewer3d_group", None)
        return viewer3d_group is not None and self.top_tabs.currentWidget() is viewer3d_group

    def _on_top_tab_changed(self, _index: int) -> None:
        if not self._is_3d_tab_active():
            self.viewer3d_refresh_timer.stop()
            return
        if self.viewer3d_pending_layout is not None:
            self._set_3d_status("3D preview update pending...")
            self.viewer3d_refresh_timer.start(5000)

    def _schedule_3d_view_refresh(self, layout) -> None:
        self.viewer3d_pending_layout = layout
        if not self._is_3d_tab_active():
            self.viewer3d_refresh_timer.stop()
            self._set_3d_status("3D preview queued. Open the 3D tab to render it.")
            return
        self._set_3d_status("3D preview update pending...")
        self.viewer3d_refresh_timer.start(5000)

    def _flush_3d_view_refresh(self) -> None:
        if not self._is_3d_tab_active():
            return
        layout = self.viewer3d_pending_layout
        self.viewer3d_pending_layout = None
        if layout is None:
            return
        self._set_3d_view_from_layout(layout)

    def eventFilter(self, source, event):  # pragma: no cover - driven by Qt runtime
        if self.viewer3d is not None and source in {self.viewer3d, getattr(self.viewer3d, "iren", None)}:
            if event.type() == QtCore.QEvent.Type.MouseButtonDblClick and event.button() == QtCore.Qt.MouseButton.LeftButton:
                actor = self._pick_3d_actor_from_event(source, event)
                if actor is not None:
                    self._toggle_3d_actor_selection(actor)
                elif self.viewer3d_selected_actor_id is not None:
                    self._restore_3d_actor(self.viewer3d_selected_actor_id)
                    self._set_dielectric_focus_opacity(focused_actor_id=None)
                    self.viewer3d_selected_actor_id = None
                    self._set_3d_status("3D solid selection cleared.")
                    self.viewer3d.render()
                return True
        return super().eventFilter(source, event)

    def closeEvent(self, event) -> None:  # pragma: no cover - driven by Qt runtime
        self._is_closing = True
        self.refresh_timer.stop()
        self.viewer3d_refresh_timer.stop()
        self.preview_refresh_generation += 1
        self.preview_refresh_pending_request = None

        if self.preview_refresh_thread is not None:
            for signal, slot in (
                (self.preview_refresh_thread.completed, self._handle_preview_refresh_completed),
                (self.preview_refresh_thread.failed, self._handle_preview_refresh_failed),
                (self.preview_refresh_thread.finished, self._cleanup_preview_refresh_thread),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self.preview_refresh_thread.wait(1000)

        if self.optimization_thread is not None:
            if self.optimization_thread.isRunning():
                self.optimization_thread.request_stop()
                self.optimization_thread.wait(2000)
            for signal, slot in (
                (self.optimization_thread.progress, self._handle_optimization_progress),
                (self.optimization_thread.completed, self._finish_optimization),
                (self.optimization_thread.failed, self._handle_optimization_failure),
                (self.optimization_thread.finished, self._cleanup_optimization_thread),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass

        super().closeEvent(event)

    @staticmethod
    def _layer_preview_color(layer: int) -> str:
        palette = (
            "#b76300",
            "#c46f05",
            "#d17c10",
            "#de891b",
            "#ea9728",
            "#f2a83c",
            "#cf7416",
            "#dd8a36",
        )
        return palette[int(layer) % len(palette)]

    @staticmethod
    def _dielectric_preview_color(index: int, *, is_bottom_slab: bool) -> str:
        palette = (
            "#6f9fc8",
            "#7aabd4",
            "#85b6de",
            "#91c0e6",
            "#9dc9eb",
            "#a9d1ef",
            "#b4d8f2",
            "#c0e0f5",
        )
        if is_bottom_slab:
            return "#658fb5"
        return palette[int(index) % len(palette)]

    @staticmethod
    def _polydata_from_points(points_xy: np.ndarray | object, *, z_bottom_um: float, thickness_um: float):
        if pv is None or vtk is None:
            return None
        polygon = points_xy if hasattr(points_xy, "contain") and hasattr(points_xy, "points") else None
        points = np.asarray(points_xy.points if polygon is not None else points_xy, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2:
            return None
        if len(points) >= 2 and np.allclose(points[0], points[-1]):
            points = points[:-1]
        if len(points) < 3:
            return None

        # Prefer VTK's ordered-polygon triangulation for simple conductors.
        # Some gdstk booleans encode holes with repeated vertices, so keep the
        # Delaunay+point-in-polygon path as a fallback when the VTK result is empty
        # or loses measurable area.
        target_area = TransformerConstraintQtGui._polygon_area(points)
        area_tolerance = max(1.0e-3, target_area * 1.0e-6)
        surface = TransformerConstraintQtGui._vtk_surface_from_points(points)
        if surface is None or surface.n_cells == 0 or abs(float(surface.area) - target_area) > area_tolerance:
            fallback_surface = TransformerConstraintQtGui._delaunay_surface_from_points(points, polygon=polygon)
            if fallback_surface is None or fallback_surface.n_cells == 0:
                if surface is None or surface.n_cells == 0:
                    return None
            elif surface is None or surface.n_cells == 0:
                surface = fallback_surface
            else:
                surface_error = abs(float(surface.area) - target_area)
                fallback_error = abs(float(fallback_surface.area) - target_area)
                surface = fallback_surface if fallback_error < surface_error else surface
        if surface is None or surface.n_cells == 0:
            return None
        surface = surface.clean()
        if surface.n_cells == 0:
            return None
        height = max(float(thickness_um), 1.0e-3)
        return surface.extrude((0.0, 0.0, height), capping=True).triangulate().clean().translate(
            (0.0, 0.0, float(z_bottom_um)),
            inplace=False,
        )

    @staticmethod
    def _polygon_area(points: np.ndarray) -> float:
        shifted = np.roll(points, -1, axis=0)
        return 0.5 * abs(
            float(np.dot(points[:, 0], shifted[:, 1]) - np.dot(points[:, 1], shifted[:, 0]))
        )

    @staticmethod
    def _vtk_surface_from_points(points: np.ndarray):
        if pv is None or vtk is None:
            return None
        vtk_points = vtk.vtkPoints()
        polygon = vtk.vtkPolygon()
        polygon.GetPointIds().SetNumberOfIds(len(points))
        for index, point in enumerate(points):
            vtk_points.InsertNextPoint(float(point[0]), float(point[1]), 0.0)
            polygon.GetPointIds().SetId(index, index)

        polygons = vtk.vtkCellArray()
        polygons.InsertNextCell(polygon)
        polydata = vtk.vtkPolyData()
        polydata.SetPoints(vtk_points)
        polydata.SetPolys(polygons)

        triangulator = vtk.vtkTriangleFilter()
        triangulator.SetInputData(polydata)
        triangulator.Update()
        return pv.wrap(triangulator.GetOutput())

    @staticmethod
    def _delaunay_surface_from_points(points: np.ndarray, *, polygon=None):
        unique_points: list[np.ndarray] = []
        point_index_by_key: dict[tuple[float, float], int] = {}
        for point in points:
            key = (float(point[0]), float(point[1]))
            if key not in point_index_by_key:
                point_index_by_key[key] = len(unique_points)
                unique_points.append(np.array([key[0], key[1]], dtype=float))
        if len(unique_points) < 3:
            return None

        points2d = np.vstack(unique_points)
        try:
            delaunay = Delaunay(points2d)
        except Exception:
            return None

        triangle_cells: list[list[int]] = []
        for simplex in delaunay.simplices:
            simplex_points = points2d[np.asarray(simplex, dtype=int)]
            centroid = simplex_points.mean(axis=0)
            if polygon is not None:
                if not bool(polygon.contain((float(centroid[0]), float(centroid[1])))):
                    continue
            else:
                if not bool(gdstk.inside([(float(centroid[0]), float(centroid[1]))], [gdstk.Polygon(points)])[0]):
                    continue
            triangle_cells.append([3, int(simplex[0]), int(simplex[1]), int(simplex[2])])
        if not triangle_cells:
            return None

        points3d = np.column_stack(
            [
                points2d[:, 0],
                points2d[:, 1],
                np.zeros(points2d.shape[0], dtype=float),
            ]
        )
        return pv.PolyData(points3d, np.asarray(triangle_cells, dtype=np.int64).ravel())

    @staticmethod
    def _flattened_gds_cell(layout):
        library = gdstk.read_gds(str(layout.gds_path))
        source = next((cell for cell in library.cells if cell.name == layout.top_cell), None)
        if source is None:
            top_level = library.top_level()
            if not top_level:
                raise ValueError(f"no top cell found in {layout.gds_path}")
            source = top_level[0]
        flattened = source.copy(f"{source.name}_3d_preview").flatten()
        return flattened

    @staticmethod
    def _polygon_bounds(flattened) -> tuple[float, float, float, float] | None:
        polygons = flattened.get_polygons(apply_repetitions=True, include_paths=True)
        if not polygons:
            return None
        min_x = math.inf
        max_x = -math.inf
        min_y = math.inf
        max_y = -math.inf
        for polygon in polygons:
            points = np.asarray(polygon.points, dtype=float)
            if points.size == 0:
                continue
            min_x = min(min_x, float(np.min(points[:, 0])))
            max_x = max(max_x, float(np.max(points[:, 0])))
            min_y = min(min_y, float(np.min(points[:, 1])))
            max_y = max(max_y, float(np.max(points[:, 1])))
        if not math.isfinite(min_x):
            return None
        return min_x, max_x, min_y, max_y

    @staticmethod
    def _expanded_bounds(bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        min_x, max_x, min_y, max_y = bounds
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        margin_x = span_x * 0.03
        margin_y = span_y * 0.03
        return min_x - margin_x, max_x + margin_x, min_y - margin_y, max_y + margin_y

    def _visual_stack_spans(
        self,
        proc_info,
        *,
        lateral_span_um: float,
    ) -> tuple[dict[object, tuple[float, float]], dict[object, tuple[float, float]], float]:
        conductor_spans: dict[object, tuple[float, float]] = {}
        dielectric_spans: dict[object, tuple[float, float]] = {}
        ordered_segments = sorted(
            [("dielectric", item) for item in proc_info.dielectrics] + [("conductor", item) for item in proc_info.conductors],
            key=lambda entry: int(entry[1].line_no),
        )
        if not ordered_segments:
            return conductor_spans, dielectric_spans, 0.0

        target_total_visual = max(float(lateral_span_um) * 0.22, 45.0)
        segment_specs: list[tuple[str, object, float, float, float]] = []
        total_weight = 0.0
        for kind, item in ordered_segments:
            physical_thickness = max(float(item.z_top_um) - float(item.z_bottom_um), 0.0)
            if kind == "conductor":
                min_visual = max(float(lateral_span_um) * 0.0045, 1.5)
                max_visual = max(float(lateral_span_um) * 0.013, min_visual)
                weight = 2.2 + math.log1p(max(physical_thickness, 0.05) / 0.08)
            else:
                is_substrate = abs(float(item.z_bottom_um)) < 1.0e-6 and physical_thickness > 50.0
                min_visual = max(float(lateral_span_um) * 0.0012, 0.35)
                max_visual = max(float(lateral_span_um) * (0.055 if is_substrate else 0.012), min_visual)
                weight = 0.55 + math.log1p(max(physical_thickness, 0.02) / 0.12)
                if is_substrate:
                    weight *= 0.35
            segment_specs.append((kind, item, weight, min_visual, max_visual))
            total_weight += weight

        visual_cursor = 0.0
        for kind, item, weight, min_visual, max_visual in segment_specs:
            allocated = target_total_visual * (weight / total_weight) if total_weight > 0.0 else min_visual
            visual_thickness = min(max(allocated, min_visual), max_visual)
            visual_bottom = visual_cursor
            visual_top = visual_bottom + visual_thickness
            if kind == "conductor":
                conductor_spans[item] = (visual_bottom, visual_top)
            else:
                dielectric_spans[item] = (visual_bottom, visual_top)
            visual_cursor = visual_top

        max_visual_height = max(float(lateral_span_um) * 0.24, 55.0)
        if visual_cursor > max_visual_height and visual_cursor > 0.0:
            scale = max_visual_height / visual_cursor
            for item, (bottom, top) in list(conductor_spans.items()):
                conductor_spans[item] = (bottom * scale, top * scale)
            for item, (bottom, top) in list(dielectric_spans.items()):
                dielectric_spans[item] = (bottom * scale, top * scale)
            visual_cursor *= scale

        return conductor_spans, dielectric_spans, visual_cursor

    @staticmethod
    def _preferred_conductor_for_layer(proc_info, layer: int):
        conductor = next(iter(proc_info.conductors_for_gds_layer(layer)), None)
        if conductor is None:
            return None
        preferred = proc_info.preferred_draw_pair_for_layer(layer)
        if preferred is None:
            return conductor
        if int(preferred.layer) != int(layer):
            return None
        return conductor

    @staticmethod
    def _via_span_for_layer(proc_info, layer: int, conductor_spans: dict[object, tuple[float, float]]) -> tuple[float, float] | None:
        via_number = None
        for definition in proc_info.layer_definitions_for_gds_layer(layer):
            name = str(definition.name).strip().lower()
            if name.startswith("via") and name[3:].isdigit():
                via_number = int(name[3:])
                break
        if via_number is None:
            return None
        lower = proc_info.conductor_named(f"metal{via_number}")
        upper = proc_info.conductor_named(f"metal{via_number + 1}")
        if lower is None or upper is None:
            return None
        lower_span = conductor_spans.get(lower)
        upper_span = conductor_spans.get(upper)
        if lower_span is None or upper_span is None:
            return None
        z_bottom = float(lower_span[1])
        z_top = float(upper_span[0])
        if z_top <= z_bottom:
            z_top = z_bottom + 1.0
        return z_bottom, z_top

    @staticmethod
    def _slab_mesh(
        *,
        bounds_xy: tuple[float, float, float, float],
        z_bottom_um: float,
        z_top_um: float,
    ):
        if pv is None:
            return None
        min_x, max_x, min_y, max_y = bounds_xy
        z0 = float(z_bottom_um)
        z1 = max(float(z_top_um), z0 + 1.0e-3)
        return pv.Box(bounds=(min_x, max_x, min_y, max_y, z0, z1))

    def _set_3d_view_from_layout(self, layout) -> None:
        if layout is None:
            self._clear_3d_view(reason="3D viewer idle.")
            return
        unavailable_reason = self._pyvista_unavailable_reason()
        if unavailable_reason is not None:
            self._set_3d_status(unavailable_reason)
            if self.viewer3d_placeholder is not None:
                self.viewer3d_placeholder.setText(unavailable_reason)
            return
        if self.viewer3d is None or pv is None:
            self._set_3d_status("3D viewer backend is not available.")
            return

        try:
            gds_path = Path(layout.gds_path).resolve()
            gds_stat = gds_path.stat()
            proc_path = self._local_process_file_path(self.run_config.emx.emx_process_file).resolve()
            proc_stat = proc_path.stat()
            source_key = (
                str(gds_path),
                f"{gds_stat.st_mtime_ns}:{gds_stat.st_size}",
                f"{proc_path}:{proc_stat.st_mtime_ns}:{proc_stat.st_size}:{layout.top_cell}",
            )
            if self.viewer3d_last_source == source_key:
                return

            proc_info = self._cached_proc_info_from_path(proc_path)
            flattened = self._flattened_gds_cell(layout)
            bounds = self._polygon_bounds(flattened)
            if bounds is None:
                self._clear_3d_view(reason="3D viewer found no polygons in the exported GDS.")
                return
            bounds = self._expanded_bounds(bounds)
            lateral_span = max(bounds[1] - bounds[0], bounds[3] - bounds[2], 1.0)
            conductor_spans, dielectric_spans, visual_height = self._visual_stack_spans(
                proc_info,
                lateral_span_um=lateral_span,
            )
            metal_meshes_by_layer: dict[int, list[object]] = {}
            via_meshes_by_layer: dict[int, list[object]] = {}
            metal_polygon_count = 0
            via_polygon_count = 0
            rendered_metal_labels: list[str] = []
            for polygon in flattened.get_polygons(apply_repetitions=True, include_paths=True):
                layer = int(getattr(polygon, "layer", -1))
                conductor = self._preferred_conductor_for_layer(proc_info, layer)
                if conductor is not None:
                    visual_span = conductor_spans.get(conductor)
                    if visual_span is None:
                        continue
                    mesh = self._polydata_from_points(
                        polygon,
                        z_bottom_um=visual_span[0],
                        thickness_um=visual_span[1] - visual_span[0],
                    )
                    if mesh is None or mesh.n_cells == 0:
                        continue
                    metal_meshes_by_layer.setdefault(layer, []).append(mesh)
                    metal_polygon_count += 1
                    continue

                via_span = self._via_span_for_layer(proc_info, layer, conductor_spans)
                if via_span is None:
                    continue
                mesh = self._polydata_from_points(
                    polygon,
                    z_bottom_um=via_span[0],
                    thickness_um=via_span[1] - via_span[0],
                )
                if mesh is None or mesh.n_cells == 0:
                    continue
                via_meshes_by_layer.setdefault(layer, []).append(mesh)
                via_polygon_count += 1
            if not metal_meshes_by_layer and not via_meshes_by_layer:
                self._clear_3d_view(reason="3D viewer found no metal or via polygons in the exported GDS.")
                return

            self.viewer3d.clear()
            self._clear_3d_actor_registry()
            self._configure_3d_viewer_rendering()

            for dielectric_index, dielectric in enumerate(proc_info.dielectrics):
                visual_span = dielectric_spans.get(dielectric)
                if visual_span is None:
                    continue
                slab = self._slab_mesh(bounds_xy=bounds, z_bottom_um=visual_span[0], z_top_um=visual_span[1])
                if slab is None:
                    continue
                is_bottom_slab = abs(float(dielectric.z_bottom_um)) < 1.0e-6
                color = self._dielectric_preview_color(dielectric_index, is_bottom_slab=is_bottom_slab)
                opacity = 0.22 if not is_bottom_slab else 0.32
                actor = self.viewer3d.add_mesh(
                    slab,
                    color=color,
                    opacity=opacity,
                    name=f"dielectric_{dielectric.line_no}",
                    smooth_shading=False,
                    show_edges=True,
                    edge_color="#8fa9bf",
                    line_width=0.45,
                    ambient=0.58,
                    diffuse=0.42,
                    specular=0.0,
                )
                self._register_3d_actor(
                    actor,
                    opacity=opacity,
                    label=dielectric.name,
                    kind="Dielectric",
                    selectable=False,
                    z_min=visual_span[0],
                    z_max=visual_span[1],
                )
                if hasattr(actor, "PickableOff"):
                    actor.PickableOff()

            for layer in sorted(metal_meshes_by_layer):
                merged_mesh = pv.merge(metal_meshes_by_layer[layer]) if len(metal_meshes_by_layer[layer]) > 1 else metal_meshes_by_layer[layer][0]
                label = proc_info.display_label_for_gds_layer(layer)
                rendered_metal_labels.append(label)
                actor = self.viewer3d.add_mesh(
                    merged_mesh,
                    color=self._layer_preview_color(layer),
                    opacity=0.97,
                    name=f"metal_{layer}",
                    smooth_shading=False,
                    show_edges=True,
                    edge_color="#b58a45",
                    line_width=0.55,
                    ambient=0.5,
                    diffuse=0.5,
                    specular=0.0,
                )
                conductor = self._preferred_conductor_for_layer(proc_info, layer)
                visual_span = None if conductor is None else conductor_spans.get(conductor)
                self._register_3d_actor(
                    actor,
                    opacity=0.97,
                    label=label,
                    kind="Metal",
                    z_min=None if visual_span is None else visual_span[0],
                    z_max=None if visual_span is None else visual_span[1],
                )

            for layer in sorted(via_meshes_by_layer):
                merged_mesh = pv.merge(via_meshes_by_layer[layer]) if len(via_meshes_by_layer[layer]) > 1 else via_meshes_by_layer[layer][0]
                actor = self.viewer3d.add_mesh(
                    merged_mesh,
                    color="#b87418",
                    opacity=0.94,
                    name=f"via_{layer}",
                    smooth_shading=False,
                    show_edges=True,
                    edge_color="#a67836",
                    line_width=0.5,
                    ambient=0.48,
                    diffuse=0.5,
                    specular=0.0,
                )
                self._register_3d_actor(
                    actor,
                    opacity=0.94,
                    label=proc_info.display_label_for_gds_layer(layer),
                    kind="Via",
                    z_min=None if (via_span := self._via_span_for_layer(proc_info, layer, conductor_spans)) is None else via_span[0],
                    z_max=None if via_span is None else via_span[1],
                )
            if self.viewer3d_show_dimensions and self.current_preview_geometry is not None:
                self._render_3d_dimension_overlays(
                    geometry=self.current_preview_geometry,
                    bounds_xy=bounds,
                    anchor_z=visual_height,
                    visual_height=visual_height,
                )
            self._apply_dielectric_visibility()
            self.viewer3d.add_axes()
            self._apply_3d_viewer_camera_mode(reset_camera=True, render=False)
            self.viewer3d_last_source = source_key
            rendered_labels = ", ".join(rendered_metal_labels[:4])
            if len(rendered_metal_labels) > 4:
                rendered_labels += ", ..."
            self._set_3d_status(
                f"Rendered stackup view: {len(proc_info.dielectrics)} dielectric slabs, "
                f"{metal_polygon_count} metal polygons, {via_polygon_count} via polygons, "
                f"visual height {visual_height:.1f} um. Layers: {rendered_labels}"
            )
        except Exception as exc:  # pragma: no cover - runtime dependent on PyVista/VTK stack
            self._clear_3d_view(reason=f"3D render failed: {exc}")

    def _build_geometry_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        global_tab = QtWidgets.QWidget()
        primary_tab = QtWidgets.QWidget()
        secondary_tab = QtWidgets.QWidget()
        tabs.addTab(global_tab, "Global")
        tabs.addTab(primary_tab, "Primary")
        tabs.addTab(secondary_tab, "Secondary")

        global_layout = QtWidgets.QVBoxLayout(global_tab)
        primary_layout = QtWidgets.QVBoxLayout(primary_tab)
        secondary_layout = QtWidgets.QVBoxLayout(secondary_tab)

        self._add_parameter_group(global_layout, ("offset_um",))
        self._add_parameter_group(
            primary_layout,
            (
                "primary_outer_width_um",
                "primary_outer_height_um",
                "primary_width_um",
                "primary_spacing_um",
                "primary_terminal_y_span_um",
                "primary_feed_extension_um",
            ),
        )
        self._add_parameter_group(
            secondary_layout,
            (
                "secondary_outer_width_um",
                "secondary_outer_height_um",
                "secondary_width_um",
                "secondary_spacing_um",
                "secondary_terminal_y_span_um",
                "secondary_feed_extension_um",
            ),
        )
        global_layout.addStretch(1)
        primary_layout.addStretch(1)
        secondary_layout.addStretch(1)
        return self._wrap_scroll(container)

    def _build_fixed_geom_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        tabs = QtWidgets.QTabWidget()
        primary_tab = QtWidgets.QWidget()
        secondary_tab = QtWidgets.QWidget()
        tabs.addTab(primary_tab, "Primary")
        tabs.addTab(secondary_tab, "Secondary")
        layout.addWidget(tabs)

        primary_layout = QtWidgets.QVBoxLayout(primary_tab)
        secondary_layout = QtWidgets.QVBoxLayout(secondary_tab)
        self._add_fixed_primary_geometry_controls(primary_layout)
        self._add_fixed_secondary_geometry_controls(secondary_layout)
        primary_layout.addStretch(1)
        secondary_layout.addStretch(1)

        self._add_topology_shield_controls(layout)
        layout.addStretch(1)
        return self._wrap_scroll(container)

    def _build_target_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(container)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for label, key in (
            ("Center freq (Hz)", "target.f0_hz"),
            ("Lp (H)", "target.lp_h"),
            ("Ls (H)", "target.ls_h"),
            ("k target", "target.k_target"),
            ("Q target mode", "target.q_target_mode"),
            ("Qp target", "target.q_primary_target"),
            ("Qs target", "target.q_secondary_target"),
            ("Diff ref Z (ohm)", "target.differential_reference_impedance_ohm"),
            ("Band points", "target.band_points"),
            ("Fractional BW", "target.fractional_bandwidth"),
        ):
            if key == "target.q_target_mode":
                self._add_combo_row(form, label, key, ("max", "target"), on_change=self._on_q_target_mode_changed)
            else:
                self._add_line_edit_row(form, label, key)
        self.optional_widget_groups["q_target_fields"] = [
            self.line_edits["target.q_primary_target"],
            self.line_edits["target.q_secondary_target"],
        ]
        return self._wrap_scroll(container)

    def _build_bounds_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        for tab_name, rows in (
            (
                "Global",
                (("Offset bounds (lo, hi)", "bounds.offset_um"),),
            ),
            (
                "Primary",
                (
                    ("Outer width bounds", "bounds.primary_outer_width_um"),
                    ("Outer height bounds", "bounds.primary_outer_height_um"),
                    ("Trace width bounds", "bounds.primary_width_um"),
                    ("Spacing bounds", "bounds.primary_spacing_um"),
                    ("Terminal span bounds", "bounds.primary_terminal_y_span_um"),
                    ("Feed extension bounds", "bounds.primary_feed_extension_um"),
                ),
            ),
            (
                "Secondary",
                (
                    ("Outer width bounds", "bounds.secondary_outer_width_um"),
                    ("Outer height bounds", "bounds.secondary_outer_height_um"),
                    ("Trace width bounds", "bounds.secondary_width_um"),
                    ("Spacing bounds", "bounds.secondary_spacing_um"),
                    ("Terminal span bounds", "bounds.secondary_terminal_y_span_um"),
                    ("Feed extension bounds", "bounds.secondary_feed_extension_um"),
                ),
            ),
        ):
            tab = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(tab)
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            for label, key in rows:
                self._add_line_edit_row(
                    form,
                    label,
                    key,
                    on_change=lambda _text, bound_key=key.split(".", 1)[1]: self._on_bounds_entry_change(bound_key),
                )
            tabs.addTab(tab, tab_name)

        layout.addStretch(1)
        return self._wrap_scroll(container)

    def _build_emx_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(container)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._add_combo_row(
            form,
            "Execution mode",
            "emx.execution_mode",
            ("local", "remote_ssh"),
        )
        self._add_combo_row(
            form,
            "Port mode",
            "emx.port_mode",
            ("single_ended_floating", "single_ended_shield_grounded", "differential_pairs"),
        )
        for label, key in (
            ("EMX binary", "emx.emx_binary"),
            ("EMX home", "emx.emx_home"),
            ("Top cell prefix", "emx.top_cell_prefix"),
            ("Parallel jobs", "emx.parallel"),
            ("Extra args (csv)", "emx.extra_args"),
            ("License file", "emx.license_file"),
            ("CDSLMD license file", "emx.cdslmd_license_file"),
        ):
            self._add_line_edit_row(form, label, key)
        for label, key in (
            ("Remote SSH host", "emx.remote_ssh_host"),
            ("Remote SSH command", "emx.remote_ssh_command"),
            ("Remote SCP command", "emx.remote_scp_command"),
            ("Remote repo root", "emx.remote_repo_root"),
            ("Remote work root", "emx.remote_work_root"),
            ("Remote python", "emx.remote_python"),
            ("Remote activate script", "emx.remote_venv_activate"),
            ("Remote process file", "emx.remote_emx_process_file"),
        ):
            self._add_line_edit_row(form, label, key)
        self._add_process_file_control(form)
        self._add_check_row(form, "Use cadence license env", "emx.use_cadence_license_env")
        self._add_check_row(form, "Skip OS check", "emx.skip_os_check")
        return self._wrap_scroll(container)

    def _build_optimizer_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        general_tab = QtWidgets.QWidget()
        general_form = QtWidgets.QFormLayout(general_tab)
        general_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._add_combo_row(
            general_form,
            "Backend",
            "optimizer.name",
            ("cma_es", "turbo"),
        )
        for label, key in (
            ("Max evaluations", "optimizer.max_evaluations"),
            ("Warm start samples", "optimizer.warm_start_samples"),
            ("Warm start paths (csv)", "optimizer.warm_start_paths"),
            ("Seed", "optimizer.seed"),
            ("Checkpoint interval (evals)", "optimizer.checkpoint_interval_evaluations"),
        ):
            self._add_line_edit_row(general_form, label, key)
        self._add_check_row(general_form, "Resume from checkpoint", "optimizer.resume_from_checkpoint")
        tabs.addTab(general_tab, "General")

        cma_tab = QtWidgets.QWidget()
        cma_form = QtWidgets.QFormLayout(cma_tab)
        cma_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for label, key in (
            ("Population size", "optimizer.cma_es.population_size"),
            ("Sigma0", "optimizer.cma_es.sigma0"),
            ("Verbose", "optimizer.cma_es.verbose"),
        ):
            self._add_line_edit_row(cma_form, label, key)
        tabs.addTab(cma_tab, "CMA-ES")

        turbo_tab = QtWidgets.QWidget()
        turbo_form = QtWidgets.QFormLayout(turbo_tab)
        turbo_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for label, key in (
            ("Initial length", "optimizer.turbo.initial_length"),
            ("Length min", "optimizer.turbo.length_min"),
            ("Length max", "optimizer.turbo.length_max"),
            ("Success tolerance", "optimizer.turbo.success_tolerance"),
            ("Num restarts", "optimizer.turbo.num_restarts"),
            ("Raw samples", "optimizer.turbo.raw_samples"),
            ("N candidates", "optimizer.turbo.n_candidates"),
            ("Max cholesky size", "optimizer.turbo.max_cholesky_size"),
        ):
            self._add_line_edit_row(turbo_form, label, key)
        self._add_combo_row(
            turbo_form,
            "Acquisition fn",
            "optimizer.turbo.acquisition_function",
            ("ei",),
        )
        tabs.addTab(turbo_tab, "TuRBO")

        layout.addStretch(1)
        return self._wrap_scroll(container)

    def _build_optimization_workflow_tab(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        controls_group = QtWidgets.QGroupBox("Run Controls")
        controls_layout = QtWidgets.QVBoxLayout(controls_group)
        control_row = QtWidgets.QHBoxLayout()
        control_row.addWidget(self.run_optimization_button)
        control_row.addWidget(self.pause_optimization_button)
        control_row.addWidget(self.resume_optimization_button)
        control_row.addWidget(self.stop_optimization_button)
        control_row.addWidget(self.export_best_sparams_button)
        control_row.addStretch(1)
        controls_layout.addLayout(control_row)

        preview_row = QtWidgets.QHBoxLayout()
        preview_row.addWidget(QtWidgets.QLabel("Live preview"))
        self.optimization_preview_mode_combo = QtWidgets.QComboBox()
        self.optimization_preview_mode_combo.addItem("Best so far", "best")
        self.optimization_preview_mode_combo.addItem("Latest evaluated", "latest")
        self.optimization_preview_mode_combo.currentIndexChanged.connect(
            lambda *_args: self._on_optimization_preview_mode_changed(*_args)
        )
        preview_row.addWidget(self.optimization_preview_mode_combo)
        self.optimization_follow_live_button = QtWidgets.QPushButton("Follow Live")
        self.optimization_follow_live_button.clicked.connect(lambda: self._follow_live_optimization_preview())
        preview_row.addWidget(self.optimization_follow_live_button)
        preview_row.addStretch(1)
        controls_layout.addLayout(preview_row)

        metadata_form = QtWidgets.QFormLayout()
        metadata_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.optimization_backend_value_label = QtWidgets.QLabel("Not started")
        self.optimization_backend_value_label.setWordWrap(True)
        metadata_form.addRow("Backend", self.optimization_backend_value_label)
        self.optimization_run_status_value_label = QtWidgets.QLabel("Optimization idle.")
        self.optimization_run_status_value_label.setWordWrap(True)
        metadata_form.addRow("Run status", self.optimization_run_status_value_label)
        self.optimization_snapshot_value_label = QtWidgets.QLabel("No active snapshot.")
        self.optimization_snapshot_value_label.setWordWrap(True)
        metadata_form.addRow("Active run config", self.optimization_snapshot_value_label)
        self.optimization_editor_state_value_label = QtWidgets.QLabel("Editor matches the active config.")
        self.optimization_editor_state_value_label.setWordWrap(True)
        metadata_form.addRow("Editor state", self.optimization_editor_state_value_label)
        self.optimization_run_dir_value = QtWidgets.QLineEdit()
        self.optimization_run_dir_value.setReadOnly(True)
        metadata_form.addRow("Run directory", self.optimization_run_dir_value)
        self.optimization_eval_dir_value = QtWidgets.QLineEdit()
        self.optimization_eval_dir_value.setReadOnly(True)
        metadata_form.addRow("Selected eval dir", self.optimization_eval_dir_value)
        controls_layout.addLayout(metadata_form)
        layout.addWidget(controls_group)

        self.optimization_blocking_text = QtWidgets.QPlainTextEdit()
        self.optimization_blocking_text.setReadOnly(True)
        self.optimization_blocking_text.setPlaceholderText("Blocking issues and launch validation messages appear here.")
        self.optimization_blocking_text.setMaximumHeight(110)
        layout.addWidget(self.optimization_blocking_text)

        history_group = QtWidgets.QGroupBox("Evaluation History")
        history_layout = QtWidgets.QVBoxLayout(history_group)
        self.optimization_eval_table = QtWidgets.QTableWidget(0, 7)
        self.optimization_eval_table.setHorizontalHeaderLabels(
            ("Eval", "State", "Cost", "Best", "Elapsed (s)", "Backend", "Cache")
        )
        self.optimization_eval_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.optimization_eval_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.optimization_eval_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.optimization_eval_table.setAlternatingRowColors(True)
        self.optimization_eval_table.verticalHeader().setVisible(False)
        self.optimization_eval_table.itemSelectionChanged.connect(lambda: self._on_optimization_selection_changed())
        header = self.optimization_eval_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._apply_data_view_theme(self.optimization_eval_table)
        history_layout.addWidget(self.optimization_eval_table)
        layout.addWidget(history_group, 2)

        detail_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.optimization_summary_text = QtWidgets.QPlainTextEdit()
        self.optimization_summary_text.setReadOnly(True)
        self.optimization_eval_detail_text = QtWidgets.QPlainTextEdit()
        self.optimization_eval_detail_text.setReadOnly(True)
        detail_splitter.addWidget(self.optimization_summary_text)
        detail_splitter.addWidget(self.optimization_eval_detail_text)
        detail_splitter.setStretchFactor(0, 1)
        detail_splitter.setStretchFactor(1, 1)
        layout.addWidget(detail_splitter, 2)

        layout.addStretch(1)
        self._set_optimization_controls_running(False)
        self._update_optimization_run_metadata()
        self._update_optimization_blocking_issues()
        self._refresh_optimization_text()
        self._refresh_optimization_eval_table()
        self._refresh_selected_optimization_detail()
        return self._wrap_scroll(container)

    def _add_parameter_group(self, layout: QtWidgets.QVBoxLayout, names: tuple[str, ...]) -> None:
        for name in names:
            self._add_parameter_control(layout, name)

    def _add_parameter_control(self, layout: QtWidgets.QVBoxLayout, name: str) -> None:
        lo, hi = getattr(self.bounds, name)
        resolution = _slider_resolution(lo, hi)

        frame = QtWidgets.QFrame()
        frame_layout = QtWidgets.QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 6)

        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(PARAMETER_LABELS.get(name, name))
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        value_label = QtWidgets.QLabel("0.00 um")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(value_label)
        frame_layout.addLayout(title_row)

        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.valueChanged.connect(lambda _value, field=name: self._on_slider_changed(field))
        frame_layout.addWidget(slider)

        range_label = QtWidgets.QLabel(f"{lo:.2f} to {hi:.2f} um")
        palette = range_label.palette()
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#666666"))
        range_label.setPalette(palette)
        frame_layout.addWidget(range_label)

        binding = SliderBinding(
            slider=slider,
            value_label=value_label,
            range_label=range_label,
            minimum=float(lo),
            maximum=float(hi),
            resolution=resolution,
        )
        self.slider_bindings[name] = binding
        self._set_slider_bounds(name, float(lo), float(hi))
        self._set_slider_value(name, float(lo))
        layout.addWidget(frame)

    def _add_inductor_layer_controls(
        self,
        layout: QtWidgets.QVBoxLayout,
        *,
        title: str,
        field_names: tuple[str, ...],
    ) -> None:
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for field_name in field_names:
            label = STACKUP_FIELD_LABELS_BY_NAME[field_name]
            combo = QtWidgets.QComboBox()
            combo.currentIndexChanged.connect(lambda _index, key=field_name: self._on_stackup_selection(key))
            self.stackup_combos[field_name] = combo
            form.addRow(label, combo)
        layout.addWidget(group)

    def _add_fixed_primary_geometry_controls(self, layout: QtWidgets.QVBoxLayout) -> None:
        self._add_inductor_layer_controls(
            layout,
            title="Inductor Layers",
            field_names=("ap_layer", "primary_bridge_layer"),
        )
        self._add_topology_subset_controls(
            layout,
            title="Primary Discrete Geometry",
            turns_key="topology.primary_turns",
            center_tap_key="topology.primary_center_tap",
            turns_label="Primary turns",
            center_tap_label="Primary center tap",
        )
        self._add_vdd_bar_controls(
            layout,
            title="Primary VDD Bar",
            enabled_key="topology.primary_vdd_bar_enabled",
            layer_key="primary_vdd_bar_layer",
            width_key="topology.primary_vdd_bar_width_um",
            offset_key="topology.primary_vdd_bar_offset_um",
        )
        self._add_bridge_section_controls(
            layout,
            title="Primary Vias",
            pad_length_key="bounds.primary_bridge_section_pad_height_ratio",
            via_size_key="bounds.primary_bridge_section_via_size_ratio",
            via_width_key="bounds.primary_bridge_section_via_width_ratio",
            via_spacing_key="bounds.primary_bridge_section_via_spacing_ratio",
            widget_key="primary",
        )

    def _add_fixed_secondary_geometry_controls(self, layout: QtWidgets.QVBoxLayout) -> None:
        self._add_inductor_layer_controls(
            layout,
            title="Inductor Layers",
            field_names=("m9_layer", "secondary_bridge_layer"),
        )
        self._add_topology_subset_controls(
            layout,
            title="Secondary Discrete Geometry",
            turns_key="topology.secondary_turns",
            center_tap_key="topology.secondary_center_tap",
            turns_label="Secondary turns",
            center_tap_label="Secondary center tap",
        )
        self._add_vdd_bar_controls(
            layout,
            title="Secondary VDD Bar",
            enabled_key="topology.secondary_vdd_bar_enabled",
            layer_key="secondary_vdd_bar_layer",
            width_key="topology.secondary_vdd_bar_width_um",
            offset_key="topology.secondary_vdd_bar_offset_um",
        )
        self._add_bridge_section_controls(
            layout,
            title="Secondary Vias",
            pad_length_key="bounds.secondary_bridge_section_pad_height_ratio",
            via_size_key="bounds.secondary_bridge_section_via_size_ratio",
            via_width_key="bounds.secondary_bridge_section_via_width_ratio",
            via_spacing_key="bounds.secondary_bridge_section_via_spacing_ratio",
            widget_key="secondary",
        )

    def _add_topology_subset_controls(
        self,
        layout: QtWidgets.QVBoxLayout,
        *,
        title: str,
        turns_key: str,
        center_tap_key: str,
        turns_label: str,
        center_tap_label: str,
    ) -> None:
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._add_combo_row(form, turns_label, turns_key, ("1", "2"), on_change=self._on_turns_changed)
        self._add_check_row(form, center_tap_label, center_tap_key)
        layout.addWidget(group)

    def _add_vdd_bar_controls(
        self,
        layout: QtWidgets.QVBoxLayout,
        *,
        title: str,
        enabled_key: str,
        layer_key: str,
        width_key: str,
        offset_key: str,
    ) -> None:
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        checkbox = self._add_check_row(form, "Enable VDD bar", enabled_key)
        combo = self._add_layer_choice_control(form, "VDD bar layer", layer_key, allow_none=True)
        width = self._add_line_edit_row(form, "VDD bar width (um)", width_key)
        offset = self._add_line_edit_row(form, "VDD bar offset (um)", offset_key)
        self.optional_widget_groups[layer_key] = [combo, width, offset]
        checkbox.stateChanged.connect(lambda _state, key=layer_key: self._update_optional_control_state())
        layout.addWidget(group)

    def _add_bridge_section_controls(
        self,
        layout: QtWidgets.QVBoxLayout,
        *,
        title: str,
        pad_length_key: str,
        via_size_key: str,
        via_width_key: str,
        via_spacing_key: str,
        widget_key: str,
    ) -> None:
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        pad_length = self._add_line_edit_row(form, "Pad length ratio", pad_length_key)
        via_size = self._add_line_edit_row(form, "Via footprint ratio", via_size_key)
        via_width = self._add_line_edit_row(form, "Via width ratio", via_width_key)
        via_spacing = self._add_line_edit_row(form, "Via spacing ratio", via_spacing_key)
        self.bridge_section_widgets[widget_key] = [pad_length, via_size, via_width, via_spacing]
        layout.addWidget(group)

    def _add_topology_shield_controls(self, layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Shielding")
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        checkbox = self._add_check_row(form, "Shield enabled", "transformer.shield.enabled")
        combo = self._add_layer_choice_control(form, "Shield draw layer", "shield_layer", allow_none=True)
        margin = self._add_line_edit_row(form, "Shield margin (um)", "transformer.shield.margin_um")
        width = self._add_line_edit_row(form, "Shield width (um)", "transformer.shield.width_um")
        self.optional_widget_groups["shield_layer"] = [combo, margin, width]
        checkbox.stateChanged.connect(lambda _state: self._update_optional_control_state())
        layout.addWidget(group)

    def _add_line_edit_row(
        self,
        form: QtWidgets.QFormLayout,
        label: str,
        key: str,
        *,
        on_change=None,
    ) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        handler = self._schedule_refresh if on_change is None else on_change
        edit.textChanged.connect(handler)
        self.line_edits[key] = edit
        form.addRow(label, edit)
        return edit

    def _add_combo_row(
        self,
        form: QtWidgets.QFormLayout,
        label: str,
        key: str,
        values: tuple[str, ...],
        *,
        on_change=None,
    ) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        for value in values:
            combo.addItem(value, value)
        combo.currentIndexChanged.connect(self._schedule_refresh if on_change is None else on_change)
        self.combo_boxes[key] = combo
        form.addRow(label, combo)
        return combo

    def _add_check_row(self, form: QtWidgets.QFormLayout, label: str, key: str) -> QtWidgets.QCheckBox:
        checkbox = QtWidgets.QCheckBox(label)
        checkbox.stateChanged.connect(self._schedule_refresh)
        self.check_boxes[key] = checkbox
        form.addRow(checkbox)
        return checkbox

    def _add_layer_choice_control(
        self,
        form: QtWidgets.QFormLayout,
        label: str,
        choice_key: str,
        *,
        allow_none: bool,
    ) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setProperty("allow_none", allow_none)
        combo.currentIndexChanged.connect(lambda _index, key=choice_key: self._on_layer_choice_selection(key))
        self.layer_choice_combos[choice_key] = combo
        form.addRow(label, combo)
        return combo

    def _add_process_file_control(self, form: QtWidgets.QFormLayout) -> None:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit()
        edit.textChanged.connect(lambda _text: self._on_process_file_changed())
        button = QtWidgets.QPushButton("Browse")
        button.clicked.connect(self._browse_process_file)
        self.line_edits["emx.emx_process_file"] = edit
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        form.addRow("Process file", row)

    @staticmethod
    def _format_range(value: tuple[float, float]) -> str:
        return f"{float(value[0]):.6g}, {float(value[1]):.6g}"

    def _load_run_config_into_editor(self, cfg) -> None:
        with self._signal_guard():
            self.run_config = cfg
            self.bounds = cfg.bounds
            self.hidden_emx_values["metal_datatype"] = int(cfg.emx.metal_datatype)
            self.hidden_emx_values["label_layer"] = int(cfg.emx.label_layer)
            self.hidden_emx_values["label_datatype"] = int(cfg.emx.label_datatype)

            self._set_line_text("target.f0_hz", cfg.target.f0_hz)
            self._set_line_text("target.lp_h", cfg.target.lp_h)
            self._set_line_text("target.ls_h", cfg.target.ls_h)
            self._set_line_text("target.k_target", cfg.target.k_target)
            self._set_combo_text("target.q_target_mode", cfg.target.q_target_mode)
            self._set_line_text("target.q_primary_target", cfg.target.q_primary_target)
            self._set_line_text("target.q_secondary_target", cfg.target.q_secondary_target)
            self._set_line_text("target.differential_reference_impedance_ohm", cfg.target.differential_reference_impedance_ohm)
            self._set_line_text("target.band_points", cfg.target.band_points)
            self._set_line_text("target.fractional_bandwidth", cfg.target.fractional_bandwidth)

            self._set_line_text("bounds.primary_outer_width_um", self._format_range(cfg.bounds.primary.outer_width_um))
            self._set_line_text("bounds.primary_outer_height_um", self._format_range(cfg.bounds.primary.outer_height_um))
            self._set_line_text("bounds.secondary_outer_width_um", self._format_range(cfg.bounds.secondary.outer_width_um))
            self._set_line_text("bounds.secondary_outer_height_um", self._format_range(cfg.bounds.secondary.outer_height_um))
            self._set_line_text("bounds.primary_width_um", self._format_range(cfg.bounds.primary.trace_width_um))
            self._set_line_text("bounds.primary_spacing_um", self._format_range(cfg.bounds.primary.spacing_um))
            self._set_line_text("bounds.primary_terminal_y_span_um", self._format_range(cfg.bounds.primary.terminal_y_span_um))
            self._set_line_text("bounds.primary_feed_extension_um", self._format_range(cfg.bounds.primary.feed_extension_um))
            self._set_line_text("bounds.secondary_width_um", self._format_range(cfg.bounds.secondary.trace_width_um))
            self._set_line_text("bounds.secondary_spacing_um", self._format_range(cfg.bounds.secondary.spacing_um))
            self._set_line_text("bounds.secondary_terminal_y_span_um", self._format_range(cfg.bounds.secondary.terminal_y_span_um))
            self._set_line_text("bounds.secondary_feed_extension_um", self._format_range(cfg.bounds.secondary.feed_extension_um))
            self._set_line_text("bounds.offset_um", self._format_range(cfg.bounds.offset_um))
            self._set_line_text(
                "bounds.primary_bridge_section_pad_height_ratio",
                "" if cfg.bounds.primary.bridge_section is None else f"{float(cfg.bounds.primary.bridge_section.pad_height_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.primary_bridge_section_via_size_ratio",
                "" if cfg.bounds.primary.bridge_section is None else f"{float(cfg.bounds.primary.bridge_section.via_size_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.primary_bridge_section_via_width_ratio",
                "" if cfg.bounds.primary.bridge_section is None else f"{float(cfg.bounds.primary.bridge_section.via_width_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.primary_bridge_section_via_spacing_ratio",
                "" if cfg.bounds.primary.bridge_section is None else f"{float(cfg.bounds.primary.bridge_section.via_spacing_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.secondary_bridge_section_pad_height_ratio",
                "" if cfg.bounds.secondary.bridge_section is None else f"{float(cfg.bounds.secondary.bridge_section.pad_height_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.secondary_bridge_section_via_size_ratio",
                "" if cfg.bounds.secondary.bridge_section is None else f"{float(cfg.bounds.secondary.bridge_section.via_size_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.secondary_bridge_section_via_width_ratio",
                "" if cfg.bounds.secondary.bridge_section is None else f"{float(cfg.bounds.secondary.bridge_section.via_width_ratio):.6g}",
            )
            self._set_line_text(
                "bounds.secondary_bridge_section_via_spacing_ratio",
                "" if cfg.bounds.secondary.bridge_section is None else f"{float(cfg.bounds.secondary.bridge_section.via_spacing_ratio):.6g}",
            )

            self._set_line_text("emx.emx_binary", cfg.emx.emx_binary)
            self._set_line_text("emx.emx_home", "" if cfg.emx.emx_home is None else cfg.emx.emx_home)
            self._set_line_text("emx.emx_process_file", cfg.emx.emx_process_file)
            self._set_line_text("emx.top_cell_prefix", cfg.emx.top_cell_prefix)
            parallel_jobs = self._extract_parallel_extra_arg(cfg.emx.extra_args)
            filtered_extra_args = [
                arg.strip()
                for arg in cfg.emx.extra_args
                if str(arg).strip() and not str(arg).strip().startswith("--parallel=")
            ]
            self._set_line_text("emx.parallel", "" if parallel_jobs is None else str(parallel_jobs))
            self._set_line_text("emx.extra_args", ", ".join(filtered_extra_args))
            self._set_check("emx.use_cadence_license_env", cfg.emx.use_cadence_license_env)
            self._set_line_text("emx.license_file", "" if cfg.emx.license_file is None else cfg.emx.license_file)
            self._set_line_text(
                "emx.cdslmd_license_file",
                "" if cfg.emx.cdslmd_license_file is None else cfg.emx.cdslmd_license_file,
            )
            self._set_check("emx.skip_os_check", cfg.emx.skip_os_check)
            self._set_combo_text("emx.execution_mode", cfg.emx.execution_mode)
            self._set_line_text("emx.remote_ssh_host", "" if cfg.emx.remote_ssh_host is None else cfg.emx.remote_ssh_host)
            self._set_line_text("emx.remote_ssh_command", cfg.emx.remote_ssh_command)
            self._set_line_text("emx.remote_scp_command", cfg.emx.remote_scp_command)
            self._set_line_text("emx.remote_repo_root", "" if cfg.emx.remote_repo_root is None else cfg.emx.remote_repo_root)
            self._set_line_text("emx.remote_work_root", cfg.emx.remote_work_root)
            self._set_line_text("emx.remote_python", cfg.emx.remote_python)
            self._set_line_text(
                "emx.remote_venv_activate",
                "" if cfg.emx.remote_venv_activate is None else cfg.emx.remote_venv_activate,
            )
            self._set_line_text(
                "emx.remote_emx_process_file",
                "" if cfg.emx.remote_emx_process_file is None else cfg.emx.remote_emx_process_file,
            )
            self._set_combo_text("emx.port_mode", cfg.emx.port_mode)

            self._set_check("transformer.shield.enabled", cfg.bounds.shield.enabled)
            self._set_line_text(
                "transformer.shield.margin_um",
                0.0 if cfg.bounds.shield.margin_um is None else cfg.bounds.shield.margin_um,
            )
            self._set_line_text(
                "transformer.shield.width_um",
                0.0 if cfg.bounds.shield.width_um is None else cfg.bounds.shield.width_um,
            )

            self._set_combo_text("topology.primary_turns", str(cfg.bounds.primary_turns))
            self._set_combo_text("topology.secondary_turns", str(cfg.bounds.secondary_turns))
            self._set_check("topology.primary_center_tap", cfg.bounds.primary_center_tap)
            self._set_check("topology.secondary_center_tap", cfg.bounds.secondary_center_tap)
            self._set_check(
                "topology.primary_vdd_bar_enabled",
                False if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.enabled,
            )
            self._set_check(
                "topology.secondary_vdd_bar_enabled",
                False if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.enabled,
            )
            self._set_line_text(
                "topology.primary_vdd_bar_width_um",
                "" if cfg.bounds.primary.vdd_bar is None or cfg.bounds.primary.vdd_bar.width_um is None else cfg.bounds.primary.vdd_bar.width_um,
            )
            self._set_line_text(
                "topology.primary_vdd_bar_offset_um",
                0.0 if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.offset_um,
            )
            self._set_line_text(
                "topology.secondary_vdd_bar_width_um",
                "" if cfg.bounds.secondary.vdd_bar is None or cfg.bounds.secondary.vdd_bar.width_um is None else cfg.bounds.secondary.vdd_bar.width_um,
            )
            self._set_line_text(
                "topology.secondary_vdd_bar_offset_um",
                0.0 if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.offset_um,
            )

            self.stackup_values = {
                "ap_layer": int(cfg.emx.ap_layer),
                "primary_bridge_layer": int(
                    self._display_bridge_target_layer(cfg.emx.primary_bridge_layer, cfg.emx.primary_bridge_lower_layer)
                ),
                "m9_layer": int(cfg.emx.m9_layer),
                "secondary_bridge_layer": int(
                    self._display_bridge_target_layer(cfg.emx.secondary_bridge_layer, cfg.emx.secondary_bridge_lower_layer)
                ),
                "m5_layer": int(cfg.emx.m5_layer),
            }
            self.layer_choice_values = {
                "shield_layer": None if cfg.emx.shield_layer is None else int(cfg.emx.shield_layer),
                "primary_vdd_bar_layer": (
                    int(cfg.emx.primary_coil_layer)
                    if cfg.bounds.primary.vdd_bar is None or cfg.bounds.primary.vdd_bar.bar_layer is None
                    else int(cfg.bounds.primary.vdd_bar.bar_layer)
                ),
                "secondary_vdd_bar_layer": (
                    int(cfg.emx.secondary_coil_layer)
                    if cfg.bounds.secondary.vdd_bar is None or cfg.bounds.secondary.vdd_bar.bar_layer is None
                    else int(cfg.bounds.secondary.vdd_bar.bar_layer)
                ),
            }

            self._set_combo_text("optimizer.name", cfg.optimizer.name)
            self._set_line_text("optimizer.max_evaluations", cfg.optimizer.max_evaluations)
            self._set_line_text("optimizer.warm_start_samples", cfg.optimizer.warm_start_samples)
            self._set_line_text("optimizer.warm_start_paths", ",".join(cfg.optimizer.warm_start_paths))
            self._set_line_text("optimizer.seed", cfg.optimizer.seed)
            self._set_check("optimizer.resume_from_checkpoint", cfg.optimizer.resume_from_checkpoint)
            self._set_line_text(
                "optimizer.checkpoint_interval_evaluations",
                cfg.optimizer.checkpoint_interval_evaluations,
            )
            self._set_line_text(
                "optimizer.cma_es.population_size",
                "" if cfg.optimizer.cma_es.population_size is None else cfg.optimizer.cma_es.population_size,
            )
            self._set_line_text("optimizer.cma_es.sigma0", "" if cfg.optimizer.cma_es.sigma0 is None else cfg.optimizer.cma_es.sigma0)
            self._set_line_text("optimizer.cma_es.verbose", cfg.optimizer.cma_es.verbose)
            self._set_line_text("optimizer.turbo.initial_length", cfg.optimizer.turbo.initial_length)
            self._set_line_text("optimizer.turbo.length_min", cfg.optimizer.turbo.length_min)
            self._set_line_text("optimizer.turbo.length_max", cfg.optimizer.turbo.length_max)
            self._set_line_text("optimizer.turbo.success_tolerance", cfg.optimizer.turbo.success_tolerance)
            self._set_line_text("optimizer.turbo.num_restarts", cfg.optimizer.turbo.num_restarts)
            self._set_line_text("optimizer.turbo.raw_samples", cfg.optimizer.turbo.raw_samples)
            self._set_line_text(
                "optimizer.turbo.n_candidates",
                "" if cfg.optimizer.turbo.n_candidates is None else cfg.optimizer.turbo.n_candidates,
            )
            self._set_line_text("optimizer.turbo.max_cholesky_size", cfg.optimizer.turbo.max_cholesky_size)
            self._set_combo_text("optimizer.turbo.acquisition_function", cfg.optimizer.turbo.acquisition_function)

            self._reload_parameter_bounds()
            self._refresh_stackup_controls()
            self._update_bridge_section_enabled_state()
            self._update_optional_control_state()
            self._update_optimization_run_metadata()

    def _set_line_text(self, key: str, value) -> None:
        self.line_edits[key].setText("" if value is None else str(value))

    def _set_combo_text(self, key: str, value: str) -> None:
        combo = self.combo_boxes[key]
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_check(self, key: str, checked: bool) -> None:
        self.check_boxes[key].setChecked(bool(checked))

    def _text(self, key: str) -> str:
        return self.line_edits[key].text().strip()

    def _checked(self, key: str) -> bool:
        return bool(self.check_boxes[key].isChecked())

    def _combo_text(self, key: str) -> str:
        return self.combo_boxes[key].currentText().strip()

    def _slider_steps(self, lo: float, hi: float, resolution: float) -> int:
        return max(1, int(round((hi - lo) / resolution)))

    def _set_slider_bounds(self, name: str, lo: float, hi: float) -> None:
        binding = self.slider_bindings[name]
        binding.minimum = float(lo)
        binding.maximum = float(hi)
        binding.resolution = _slider_resolution(lo, hi)
        steps = self._slider_steps(lo, hi, binding.resolution)
        with QtCore.QSignalBlocker(binding.slider):
            binding.slider.setMinimum(0)
            binding.slider.setMaximum(steps)
        binding.range_label.setText(f"{lo:.2f} to {hi:.2f} um")

    def _set_slider_value(self, name: str, value: float) -> None:
        binding = self.slider_bindings[name]
        clipped = min(max(float(value), binding.minimum), binding.maximum)
        steps = self._slider_steps(binding.minimum, binding.maximum, binding.resolution)
        raw = int(round((clipped - binding.minimum) / binding.resolution))
        raw = min(max(raw, 0), steps)
        with QtCore.QSignalBlocker(binding.slider):
            binding.slider.setValue(raw)
        actual = min(binding.minimum + raw * binding.resolution, binding.maximum)
        binding.value_label.setText(f"{actual:.2f} um")

    def _slider_value(self, name: str) -> float:
        binding = self.slider_bindings[name]
        value = binding.minimum + binding.slider.value() * binding.resolution
        return min(max(value, binding.minimum), binding.maximum)

    def _on_slider_changed(self, name: str) -> None:
        self.slider_bindings[name].value_label.setText(f"{self._slider_value(name):.2f} um")
        self._schedule_refresh()

    def _set_combo_options_with_data(
        self,
        combo: QtWidgets.QComboBox,
        options: list[tuple[str, object]],
        *,
        current_value,
        enabled: bool = True,
    ) -> None:
        with QtCore.QSignalBlocker(combo):
            combo.clear()
            selected_index = -1
            for index, (label, value) in enumerate(options):
                combo.addItem(label, value)
                if value == current_value and selected_index < 0:
                    selected_index = index
            if selected_index >= 0:
                combo.setCurrentIndex(selected_index)
            elif combo.count() > 0:
                combo.setCurrentIndex(0)
            combo.setEnabled(enabled)

    def _refresh_stackup_controls(self) -> None:
        try:
            proc_info = self._cached_proc_info(interactive=False)
        except Exception:
            proc_info = None

        for field_name, _label in STACKUP_FIELD_LABELS:
            current_value = int(self.stackup_values.get(field_name, 0) or 0)
            if proc_info is None:
                options = [(f"raw [{current_value}]", current_value)]
            else:
                options = [(display, int(layer)) for display, layer in proc_info.selectable_metal_options(extra_layers=(current_value,))]
            combo = self.stackup_combos[field_name]
            self._set_combo_options_with_data(
                combo,
                options,
                current_value=current_value,
                enabled=self._stackup_field_enabled(field_name),
            )
        self._refresh_layer_choice_controls(proc_info)

    def _refresh_layer_choice_controls(self, proc_info) -> None:
        for choice_key in ("shield_layer", "primary_vdd_bar_layer", "secondary_vdd_bar_layer"):
            combo = self.layer_choice_combos.get(choice_key)
            if combo is None:
                continue
            allow_none = bool(combo.property("allow_none"))
            current_value = self.layer_choice_values.get(choice_key)
            options = self._selectable_metal_options(proc_info, current_value=current_value, allow_none=allow_none)
            self._set_combo_options_with_data(combo, options, current_value=current_value)

    @staticmethod
    def _selectable_metal_options(proc_info, *, current_value: int | None, allow_none: bool) -> list[tuple[str, int | None]]:
        options: list[tuple[str, int | None]] = []
        if allow_none:
            options.append(("none", None))
        if proc_info is None:
            if current_value is not None:
                options.append((f"raw [{current_value}]", int(current_value)))
            return options
        extra_layers = tuple() if current_value is None else (int(current_value),)
        options.extend((display, int(layer)) for display, layer in proc_info.selectable_metal_options(extra_layers=extra_layers))
        return options

    def _on_layer_choice_selection(self, choice_key: str) -> None:
        combo = self.layer_choice_combos[choice_key]
        self.layer_choice_values[choice_key] = combo.currentData()
        self._schedule_refresh()

    def _on_stackup_selection(self, field_name: str) -> None:
        if not self._stackup_field_enabled(field_name):
            return
        combo = self.stackup_combos[field_name]
        value = combo.currentData()
        if value is None:
            return
        self.stackup_values[field_name] = int(value)
        self._schedule_refresh()

    def _stackup_field_enabled(self, field_name: str) -> bool:
        if field_name == "primary_bridge_layer":
            return int(self._combo_text("topology.primary_turns") or "1") > 1
        if field_name == "secondary_bridge_layer":
            return int(self._combo_text("topology.secondary_turns") or "1") > 1
        return True

    def _update_bridge_section_enabled_state(self) -> None:
        primary_enabled = int(self._combo_text("topology.primary_turns") or "1") > 1
        secondary_enabled = int(self._combo_text("topology.secondary_turns") or "1") > 1
        for widget in self.bridge_section_widgets.get("primary", []):
            widget.setEnabled(primary_enabled)
        for widget in self.bridge_section_widgets.get("secondary", []):
            widget.setEnabled(secondary_enabled)

    def _update_optional_control_state(self) -> None:
        q_target_enabled = self._combo_text("target.q_target_mode") == "target"
        for widget in self.optional_widget_groups.get("q_target_fields", []):
            widget.setEnabled(q_target_enabled)
        shield_enabled = self._checked("transformer.shield.enabled")
        for widget in self.optional_widget_groups.get("shield_layer", []):
            widget.setEnabled(shield_enabled)
        primary_vdd_enabled = self._checked("topology.primary_vdd_bar_enabled")
        for widget in self.optional_widget_groups.get("primary_vdd_bar_layer", []):
            widget.setEnabled(primary_vdd_enabled)
        secondary_vdd_enabled = self._checked("topology.secondary_vdd_bar_enabled")
        for widget in self.optional_widget_groups.get("secondary_vdd_bar_layer", []):
            widget.setEnabled(secondary_vdd_enabled)

    def _on_q_target_mode_changed(self, *_args) -> None:
        self._update_optional_control_state()
        self._schedule_refresh()

    def _on_turns_changed(self) -> None:
        self._update_bridge_section_enabled_state()
        self._refresh_stackup_controls()
        self._schedule_refresh()

    def _on_process_file_changed(self) -> None:
        self._refresh_stackup_controls()
        self._schedule_refresh()

    def _schedule_refresh(self, *_args) -> None:
        if self._suspend_refresh:
            return
        if self._is_optimization_running():
            self.optimization_editor_dirty_since_start = True
        self._update_optimization_run_metadata()
        self.refresh_timer.start(180)

    def _refresh_now(self) -> None:
        self.refresh_timer.stop()
        if self._suspend_refresh:
            return
        self._refresh()

    def _browse_process_file(self) -> None:
        chosen = self._browse_for_process_file()
        if chosen is None:
            return
        self.line_edits["emx.emx_process_file"].setText(str(chosen))
        self._refresh_stackup_controls()
        self._schedule_refresh()

    def _browse_for_process_file(self, missing_path: Path | None = None) -> Path | None:
        proc_dir = bundled_proc_dir().resolve()
        current_raw = self._text("emx.emx_process_file")
        current_candidate: Path | None = None
        if current_raw:
            try:
                current_candidate = self._local_process_file_path(current_raw, interactive=False)
            except Exception:
                current_candidate = None
        initial_dir = (
            current_candidate.parent
            if current_candidate is not None and current_candidate.exists()
            else (proc_dir if proc_dir.exists() else Path.cwd().resolve())
        )
        caption = "Select process file"
        if missing_path is not None:
            caption = f"Select process file for missing path:\n{missing_path}"
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            caption,
            str(initial_dir),
            "Cadence proc files (*.proc);;All files (*.*)",
        )
        if not selected:
            return None
        chosen = Path(selected).expanduser().resolve()
        self._set_status(f"Using local process file: {chosen}")
        return chosen

    def _save_config(self) -> None:
        self._save_config_via_dialog()

    def _load_recent_config_history(self) -> None:
        self.recent_config_paths = []
        if self.recent_config_store_path == Path() or not self.recent_config_store_path.exists():
            return
        try:
            raw = yaml.safe_load(self.recent_config_store_path.read_text(encoding="utf-8"))
        except Exception:
            return
        entries = raw if isinstance(raw, list) else []
        for entry in entries:
            if entry is None:
                continue
            path = Path(str(entry)).expanduser()
            try:
                resolved = path.resolve()
            except Exception:
                continue
            if resolved.exists():
                self.recent_config_paths.append(resolved)
        deduped: list[Path] = []
        seen: set[Path] = set()
        for path in self.recent_config_paths:
            if path in seen:
                continue
            deduped.append(path)
            seen.add(path)
        self.recent_config_paths = deduped[:20]

    def _save_recent_config_history(self) -> None:
        if self.recent_config_store_path == Path():
            return
        self.recent_config_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.recent_config_store_path.write_text(
            yaml.safe_dump([str(path) for path in self.recent_config_paths[:20]], sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )

    def _remember_recent_config(self, path: Path) -> None:
        resolved = Path(path).expanduser().resolve()
        self.recent_config_paths = [candidate for candidate in self.recent_config_paths if candidate != resolved]
        self.recent_config_paths.insert(0, resolved)
        self.recent_config_paths = self.recent_config_paths[:20]
        self._save_recent_config_history()
        self._refresh_config_hub()

    @staticmethod
    def _recent_group_label(modified_at: datetime, *, now: datetime) -> str:
        delta_days = max((now.date() - modified_at.date()).days, 0)
        if delta_days == 0:
            return "Today"
        if delta_days == 1:
            return "Yesterday"
        if delta_days <= 7:
            return "Last Week"
        return "Older"

    @staticmethod
    def _history_entry_slug(label: str) -> str:
        filtered = "".join(ch if ch.isalnum() else "_" for ch in str(label).strip().lower())
        compact = "_".join(part for part in filtered.split("_") if part)
        return compact[:48] or "entry"

    def _snapshot_dirname(self, label: str) -> str:
        return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self._history_entry_slug(label)}"

    def _write_history_entry(self, entry_dir: Path, payload: dict[str, object]) -> Path:
        entry_path = entry_dir / "entry.yaml"
        entry_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
        return entry_path

    @staticmethod
    def _copy_optional_artifact(source: Path | None, target: Path) -> Path | None:
        if source is None:
            return None
        source_path = Path(source)
        if not source_path.exists() or not source_path.is_file():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return target

    def _create_config_history_bundle(self, *, external_path: Path | None = None) -> Path | None:
        if self.history_configs_dir == Path():
            return None
        try:
            payload = self._current_config_payload()
            yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
        except Exception:
            return None
        label = external_path.stem if external_path is not None else f"session_{self.topology_mode}"
        entry_dir = self.history_configs_dir / self._snapshot_dirname(label)
        entry_dir.mkdir(parents=True, exist_ok=True)
        config_copy_path = entry_dir / "config.yaml"
        config_copy_path.write_text(yaml_text, encoding="utf-8")
        preview_copy = self._copy_optional_artifact(self.last_preview_path, entry_dir / "artifacts" / "preview.png")
        debug_preview_copy = self._copy_optional_artifact(self.last_debug_preview_path, entry_dir / "artifacts" / "debug_preview.png")
        self._write_history_entry(
            entry_dir,
            {
                "kind": "config_bundle",
                "label": label,
                "status": "saved",
                "topology_mode": str(self.topology_mode),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "config_path": str(config_copy_path),
                "external_path": None if external_path is None else str(external_path),
                "preview_path": None if preview_copy is None else str(preview_copy),
                "debug_preview_path": None if debug_preview_copy is None else str(debug_preview_copy),
            },
        )
        return entry_dir

    def _read_history_entries(self, root_dir: Path, *, expected_kind: str) -> list[GuiHistoryEntry]:
        entries: list[GuiHistoryEntry] = []
        if root_dir == Path() or not root_dir.exists():
            return entries
        for entry_path in sorted(root_dir.glob("*/entry.yaml"), reverse=True):
            try:
                raw = yaml.safe_load(entry_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if str(raw.get("kind", "")).strip() != expected_kind:
                continue
            config_path_raw = raw.get("config_path")
            if not config_path_raw:
                continue
            config_path = Path(str(config_path_raw))
            if not config_path.exists():
                continue
            try:
                updated_at = datetime.fromisoformat(str(raw.get("updated_at") or raw.get("created_at")))
            except Exception:
                updated_at = datetime.fromtimestamp(entry_path.stat().st_mtime)
            entries.append(
                GuiHistoryEntry(
                    kind=expected_kind,
                    label=str(raw.get("label", entry_path.parent.name)),
                    status=str(raw.get("status", "unknown")),
                    topology_mode=str(raw.get("topology_mode", "?")),
                    path=entry_path.parent,
                    config_path=config_path,
                    updated_at=updated_at,
                    detail_path=(
                        str(raw.get("best_touchstone_path"))
                        if raw.get("best_touchstone_path")
                        else str(raw.get("summary_path"))
                        if raw.get("summary_path")
                        else None
                    ),
                )
            )
        return entries

    def _add_history_group_section(
        self,
        *,
        tree,
        title: str,
        entries: list[GuiHistoryEntry],
        kind_label: str,
        now: datetime,
    ) -> None:
        section_item = QtWidgets.QTreeWidgetItem((title, "", "", ""))
        section_item.setFlags(section_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
        tree.addTopLevelItem(section_item)
        if not entries:
            placeholder = QtWidgets.QTreeWidgetItem(("No entries", "", "", ""))
            placeholder.setFlags(placeholder.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
            section_item.addChild(placeholder)
            section_item.setExpanded(True)
            return
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for entry in sorted(entries, key=lambda item: item.updated_at, reverse=True):
            group_label = self._recent_group_label(entry.updated_at, now=now)
            group_item = groups.get(group_label)
            if group_item is None:
                group_item = QtWidgets.QTreeWidgetItem((group_label, "", "", ""))
                group_item.setFlags(group_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
                groups[group_label] = group_item
                section_item.addChild(group_item)
            path_text = str(entry.path)
            if entry.detail_path:
                path_text = f"{path_text} | {entry.detail_path}"
            child = QtWidgets.QTreeWidgetItem((entry.label, kind_label if not entry.status else f"{kind_label} · {entry.status}", entry.topology_mode, path_text))
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, str(entry.config_path))
            group_item.addChild(child)
            group_item.setExpanded(True)
        section_item.setExpanded(True)

    def _refresh_config_hub(self) -> None:
        if self.config_current_topology_value is not None:
            self.config_current_topology_value.setText(str(self.topology_mode))
        if self.config_current_path_value is not None:
            if self.config_path is None:
                self.config_current_path_value.setText(f"Unsaved session config ({self.generated_config_path})")
            else:
                self.config_current_path_value.setText(str(self.config_path))
        if self.config_recent_tree is None:
            return
        tree = self.config_recent_tree
        tree.clear()
        now = datetime.now()
        recent_entries: list[GuiHistoryEntry] = []
        for path in list(self.recent_config_paths):
            if not path.exists():
                continue
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                modified_at = now
            topology_text = "?"
            try:
                loaded = load_run_config(path)
                topology_text = str(loaded.bounds.topology_mode)
            except Exception:
                topology_text = "?"
            recent_entries.append(
                GuiHistoryEntry(
                    kind="recent_config",
                    label=path.name,
                    status="linked",
                    topology_mode=topology_text,
                    path=path,
                    config_path=path,
                    updated_at=modified_at,
                )
            )
        config_bundles = self._read_history_entries(self.history_configs_dir, expected_kind="config_bundle")
        run_entries = self._read_history_entries(self.history_runs_dir, expected_kind="optimization_run")
        self._add_history_group_section(tree=tree, title="Recent Config Files", entries=recent_entries, kind_label="Recent config", now=now)
        self._add_history_group_section(tree=tree, title="Saved Design Bundles", entries=config_bundles, kind_label="Saved bundle", now=now)
        self._add_history_group_section(tree=tree, title="Optimization Runs", entries=run_entries, kind_label="Run", now=now)

    def _load_config_from_path(self, target_path: Path) -> None:
        loaded = load_run_config(target_path)
        self._apply_topology_mode(loaded.bounds.topology_mode)
        self.config_path = target_path
        self._load_run_config_into_editor(loaded)
        self._load_geometry(loaded.bounds.midpoint())
        yaml_text = yaml.safe_dump(_run_config_to_payload(loaded), sort_keys=False, allow_unicode=False)
        self.generated_config_path.write_text(yaml_text, encoding="utf-8")
        self._remember_recent_config(target_path)
        self._set_status(f"Loaded config from {target_path}")
        self._schedule_refresh()

    def _save_config_to_path(self, target_path: Path) -> None:
        try:
            payload = self._current_config_payload()
            yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
        except Exception as exc:
            self._set_status(f"Cannot save config: {exc}")
            return
        target_path.write_text(yaml_text, encoding="utf-8")
        self.generated_config_path.write_text(yaml_text, encoding="utf-8")
        self.config_path = target_path
        self._remember_recent_config(target_path)
        self._create_config_history_bundle(external_path=target_path)
        self._refresh_config_hub()
        self._set_status(f"Saved config to {target_path}")

    def _create_optimization_run_dir(self, *, backend_name: str) -> Path:
        label = f"{backend_name}_{self.topology_mode}"
        run_dir = self.history_runs_dir / self._snapshot_dirname(label)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _write_run_history_entry(
        self,
        run_dir: Path,
        *,
        status: str,
        snapshot_yaml: str | None = None,
        error: str | None = None,
    ) -> None:
        config_copy_path = run_dir / "config.yaml"
        if snapshot_yaml is not None:
            config_copy_path.write_text(snapshot_yaml, encoding="utf-8")
        summary_path = run_dir / "optimization_summary.json"
        entry_path = run_dir / "entry.yaml"
        created_at = datetime.now().isoformat(timespec="seconds")
        if entry_path.exists():
            try:
                existing = yaml.safe_load(entry_path.read_text(encoding="utf-8")) or {}
                created_at = str(existing.get("created_at", created_at))
            except Exception:
                created_at = datetime.now().isoformat(timespec="seconds")
        best_touchstone_path = None
        if self.optimization_best_result is not None and getattr(self.optimization_best_result, "touchstone_path", None) is not None:
            best_touchstone_path = str(self.optimization_best_result.touchstone_path)
        self._write_history_entry(
            run_dir,
            {
                "kind": "optimization_run",
                "label": run_dir.name,
                "status": status,
                "topology_mode": str(self.topology_mode),
                "created_at": created_at,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "config_path": str(config_copy_path),
                "summary_path": str(summary_path) if summary_path.exists() else None,
                "best_touchstone_path": best_touchstone_path,
                "run_dir": str(run_dir),
                "error": error,
            },
        )
        self._refresh_config_hub()

    def _save_config_via_dialog(self) -> None:
        initial_path = self.config_path if self.config_path is not None else self.generated_config_path
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save current transformer config",
            str(initial_path),
            "YAML files (*.yaml *.yml);;All files (*.*)",
        )
        if not selected:
            return
        target_path = Path(selected).expanduser().resolve()
        self._save_config_to_path(target_path)

    def _load_config(self) -> None:
        self._load_config_via_dialog()

    def _load_config_via_dialog(self) -> None:
        initial_path = self.config_path if self.config_path is not None else self.generated_config_path
        initial_dir = (
            initial_path.parent
            if initial_path is not None and initial_path.parent.exists()
            else Path.cwd().resolve()
        )
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load transformer config",
            str(initial_dir),
            "YAML files (*.yaml *.yml);;All files (*.*)",
        )
        if not selected:
            return
        target_path = Path(selected).expanduser().resolve()
        try:
            self._load_config_from_path(target_path)
        except Exception as exc:
            self._set_status(f"Cannot load config: {exc}")

    def _open_recent_config_item(self, item, _column: int) -> None:
        path_value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not path_value:
            return
        try:
            self._load_config_from_path(Path(str(path_value)).expanduser().resolve())
        except Exception as exc:
            self._set_status(f"Cannot load config: {exc}")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _set_optimization_status(self, message: str) -> None:
        self.optimization_status_label.setText(message)
        if self.optimization_run_status_value_label is not None:
            self.optimization_run_status_value_label.setText(message)

    def _update_optimization_run_metadata(self) -> None:
        backend_text = "Not started"
        if self.optimization_launch_snapshot_name is not None:
            backend_text = self.optimization_launch_snapshot_name
        elif self.line_edits:
            backend_text = self._combo_text("optimizer.name")
        if self.optimization_backend_value_label is not None:
            self.optimization_backend_value_label.setText(backend_text)
        if self.optimization_snapshot_value_label is not None:
            if self.optimization_launch_snapshot_yaml is None:
                self.optimization_snapshot_value_label.setText("No active snapshot.")
            else:
                self.optimization_snapshot_value_label.setText(
                    "Snapshot captured at optimization start. The active run will not use later editor changes."
                )
        if self.optimization_editor_state_value_label is not None:
            if self._is_optimization_running():
                if self.optimization_editor_dirty_since_start:
                    self.optimization_editor_state_value_label.setText("Editor modified since run start. Changes apply to the next run.")
                else:
                    self.optimization_editor_state_value_label.setText("Editor still matches the active run snapshot.")
            else:
                self.optimization_editor_state_value_label.setText("Editor changes will be used for the next run.")
        if self.optimization_run_dir_value is not None:
            self.optimization_run_dir_value.setText("" if self.optimization_run_dir is None else str(self.optimization_run_dir))
        selected_record = self._selected_optimization_record()
        if self.optimization_eval_dir_value is not None:
            self.optimization_eval_dir_value.setText(
                "" if selected_record is None else str(getattr(selected_record.result, "work_dir", ""))
            )
        if self.optimization_follow_live_button is not None:
            self.optimization_follow_live_button.setEnabled(self.optimization_manual_selected_eval is not None)

    def _update_optimization_blocking_issues(self) -> None:
        if self.optimization_blocking_text is None:
            return
        if not self.optimization_blocking_errors:
            self.optimization_blocking_text.setPlainText("No blocking issues.")
            return
        self.optimization_blocking_text.setPlainText("\n".join(f"- {line}" for line in self.optimization_blocking_errors))

    def _selected_optimization_record(self) -> OptimizationEvalRecord | None:
        if self.optimization_manual_selected_eval is None:
            return None
        for record in self.optimization_eval_records:
            if int(record.evaluation_count) == int(self.optimization_manual_selected_eval):
                return record
        return None

    def _refresh_optimization_eval_table(self) -> None:
        if self.optimization_eval_table is None:
            return
        table = self.optimization_eval_table
        with QtCore.QSignalBlocker(table):
            table.setRowCount(len(self.optimization_eval_records))
            for row_index, record in enumerate(self.optimization_eval_records):
                state_text = "ok" if record.ok else "error"
                cost_text = "nan" if not math.isfinite(record.cost) else f"{record.cost:.6g}"
                values = (
                    str(record.evaluation_count),
                    state_text,
                    cost_text,
                    "yes" if record.is_best else "",
                    f"{record.elapsed_seconds:.2f}",
                    record.backend_name,
                    record.cache_key[:12],
                )
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, int(record.evaluation_count))
                    table.setItem(row_index, column, item)
            if self.optimization_manual_selected_eval is not None:
                for row_index, record in enumerate(self.optimization_eval_records):
                    if int(record.evaluation_count) == int(self.optimization_manual_selected_eval):
                        table.selectRow(row_index)
                        break

    def _format_optimization_record_detail(self, record: OptimizationEvalRecord) -> str:
        result = record.result
        lines = [
            f"Evaluation: {record.evaluation_count}",
            f"Backend: {record.backend_name}",
            f"Elapsed seconds: {record.elapsed_seconds:.3f}",
            f"Unique evaluations: {record.unique_evaluation_count}",
            f"Is best: {record.is_best}",
            f"Cost: {record.cost:.6g}" if math.isfinite(record.cost) else "Cost: nan",
        ]
        lines.extend(self._format_eval_result_summary(result))
        return "\n".join(lines)

    def _refresh_selected_optimization_detail(self) -> None:
        if self.optimization_eval_detail_text is None:
            return
        selected_record = self._selected_optimization_record()
        if selected_record is None:
            if self.optimization_best_result is not None:
                self.optimization_eval_detail_text.setPlainText(
                    "Selected evaluation: none\n\nShowing best-result summary.\n\n"
                    + "\n".join(self._format_eval_result_summary(self.optimization_best_result))
                )
            else:
                self.optimization_eval_detail_text.setPlainText("Selected evaluation: none")
            return
        self.optimization_eval_detail_text.setPlainText(self._format_optimization_record_detail(selected_record))

    def _apply_optimization_preview_selection(self) -> None:
        result = None
        title = "Current Design Preview"
        selected_record = self._selected_optimization_record()
        if selected_record is not None:
            result = selected_record.result
            title = f"Evaluation {selected_record.evaluation_count} Preview"
        elif self.optimization_preview_mode == "latest" and self.optimization_last_result is not None:
            result = self.optimization_last_result
            title = "Latest Evaluated Preview"
        elif self.optimization_best_result is not None:
            result = self.optimization_best_result
            title = "Best Design Preview"
        elif self.optimization_last_result is not None:
            result = self.optimization_last_result
            title = "Latest Evaluated Preview"
        should_refresh_preview = result is not None and result is not self.optimization_selected_result
        self.optimization_selected_result = result
        if should_refresh_preview:
            self._set_preview_from_result(result, title=title)
        self._draw_best_metrics_plot()
        self._update_optimization_run_metadata()
        self._refresh_selected_optimization_detail()

    def _follow_live_optimization_preview(self) -> None:
        self.optimization_manual_selected_eval = None
        if self.optimization_eval_table is not None:
            self.optimization_eval_table.clearSelection()
        self._apply_optimization_preview_selection()

    def _on_optimization_preview_mode_changed(self, *_args) -> None:
        if self.optimization_preview_mode_combo is None:
            return
        self.optimization_preview_mode = str(self.optimization_preview_mode_combo.currentData() or "best")
        if self.optimization_manual_selected_eval is None:
            self._apply_optimization_preview_selection()

    def _on_optimization_selection_changed(self) -> None:
        if self.optimization_eval_table is None:
            return
        selected_rows = self.optimization_eval_table.selectionModel().selectedRows()
        if not selected_rows:
            self.optimization_manual_selected_eval = None
            self._refresh_selected_optimization_detail()
            self._update_optimization_run_metadata()
            return
        row = selected_rows[0].row()
        if row < 0 or row >= len(self.optimization_eval_records):
            return
        self.optimization_manual_selected_eval = int(self.optimization_eval_records[row].evaluation_count)
        self._apply_optimization_preview_selection()

    def _is_optimization_running(self) -> bool:
        return self.optimization_thread is not None and self.optimization_thread.isRunning()

    def _is_optimization_paused(self) -> bool:
        if not self._is_optimization_running() or self.optimization_thread is None:
            return False
        pause_requested = getattr(self.optimization_thread, "is_pause_requested", None)
        if not callable(pause_requested):
            return False
        try:
            paused = pause_requested()
        except Exception:
            return False
        return paused is True

    def _set_optimization_controls_running(self, running: bool) -> None:
        paused = self._is_optimization_paused()
        self.run_optimization_button.setEnabled(not running)
        self.run_optimization_button.setText("Start Optimization")
        self.pause_optimization_button.setEnabled(running and not paused)
        self.resume_optimization_button.setEnabled(running and paused)
        self.stop_optimization_button.setEnabled(running)
        self.export_best_sparams_button.setEnabled(self.optimization_best_result is not None)
        self._update_optimization_run_metadata()

    def _start_optimization(self) -> None:
        if self._is_optimization_running():
            if self._is_optimization_paused():
                assert self.optimization_thread is not None
                self.optimization_thread.request_resume()
                self.optimization_log_lines.append("Optimization resumed.")
                self._set_optimization_status("Optimization resumed.")
                self._set_status("Optimization resumed.")
                self._refresh_optimization_text()
                self._set_optimization_controls_running(True)
                return
            self._set_optimization_status("Optimization is already running.")
            return
        try:
            run_config, geometry, gdstk_check, geometry_errors, yaml_text = self._build_live_context()
        except Exception as exc:
            self.optimization_blocking_errors = [f"Cannot build the current config: {exc}"]
            self._update_optimization_blocking_issues()
            self._set_optimization_status("Optimization start blocked.")
            self._set_status(f"Cannot start optimization: {exc}")
            self._refresh_optimization_text()
            return
        self.optimization_blocking_errors = self._collect_optimization_start_errors(
            run_config=run_config,
            geometry=geometry,
            gdstk_check=gdstk_check,
            geometry_errors=geometry_errors,
        )
        self._update_optimization_blocking_issues()
        if self.optimization_blocking_errors:
            self._set_optimization_status("Optimization start blocked.")
            self._set_status("Fix the blocking optimization issues before starting a run.")
            self._refresh_optimization_text()
            return

        run_dir = self._create_optimization_run_dir(backend_name=str(run_config.optimizer.name))
        self.optimization_run_dir = run_dir
        self.optimization_history = []
        self.optimization_eval_records = []
        self.optimization_best_result = None
        self.optimization_last_result = None
        self.optimization_manual_selected_eval = None
        self.optimization_selected_result = None
        self.optimization_launch_snapshot_yaml = yaml_text
        self.optimization_launch_snapshot_name = str(run_config.optimizer.name)
        self.optimization_editor_dirty_since_start = False
        self.optimization_log_lines = [
            f"Starting {run_config.optimizer.name} optimization.",
            f"Run dir: {run_dir}",
            "This run uses a snapshot of the current GUI configuration.",
        ]
        self._write_run_history_entry(
            run_dir,
            status="running",
            snapshot_yaml=yaml_text,
        )
        self._draw_convergence_plot()
        self._draw_best_metrics_plot()
        self._refresh_optimization_text()
        self._refresh_optimization_eval_table()
        self._refresh_selected_optimization_detail()
        self._set_optimization_status(f"Optimization running in {run_dir}")
        self._set_status("Optimization started.")

        worker = OptimizationWorkerThread(run_config=run_config, run_dir=run_dir, parent=self)
        worker.progress.connect(self._handle_optimization_progress)
        worker.completed.connect(self._finish_optimization)
        worker.failed.connect(self._handle_optimization_failure)
        worker.finished.connect(self._cleanup_optimization_thread)
        self.optimization_thread = worker
        self._update_optimization_run_metadata()
        self._set_optimization_controls_running(True)
        worker.start()

    def _pause_optimization(self) -> None:
        if not self._is_optimization_running():
            self._set_optimization_status("No optimization is running.")
            return
        if self._is_optimization_paused():
            self._set_optimization_status("Optimization is already paused.")
            return
        assert self.optimization_thread is not None
        self.optimization_thread.request_pause()
        self.optimization_log_lines.append("Pause requested by user.")
        self._set_optimization_status("Pause requested. The optimizer will pause after the current evaluation step.")
        self._set_status("Optimization pause requested.")
        self._refresh_optimization_text()
        self._set_optimization_controls_running(True)

    def _resume_optimization(self) -> None:
        if not self._is_optimization_running():
            self._set_optimization_status("No optimization is running.")
            return
        if not self._is_optimization_paused():
            self._set_optimization_status("Optimization is not paused.")
            return
        assert self.optimization_thread is not None
        self.optimization_thread.request_resume()
        self.optimization_log_lines.append("Optimization resumed.")
        self._set_optimization_status("Optimization resumed.")
        self._set_status("Optimization resumed.")
        self._refresh_optimization_text()
        self._set_optimization_controls_running(True)

    def _stop_optimization(self) -> None:
        if not self._is_optimization_running():
            self._set_optimization_status("No optimization is running.")
            return
        assert self.optimization_thread is not None
        self.optimization_thread.request_stop()
        self.optimization_log_lines.append("Stop requested by user.")
        self._set_optimization_status("Stop requested. Waiting for the current evaluation to finish.")
        self._refresh_optimization_text()

    def _cleanup_optimization_thread(self) -> None:
        if self.optimization_thread is not None and not self.optimization_thread.isRunning():
            self.optimization_thread.deleteLater()
            self.optimization_thread = None
        self._set_optimization_controls_running(False)

    def _handle_optimization_progress(self, event: object) -> None:
        payload = dict(event or {})
        result = payload.get("result")
        if result is None:
            return
        evaluation_count = int(payload.get("evaluation_count", len(self.optimization_history) + 1))
        unique_evaluation_count = int(payload.get("unique_evaluation_count", evaluation_count))
        backend_name = str(payload.get("backend_name", self._combo_text("optimizer.name")))
        elapsed_seconds = float(payload.get("elapsed_seconds", 0.0))
        cost = float(payload.get("cost", math.nan))
        prior_best = cost if not self.optimization_history else self.optimization_history[-1][2]
        best_cost = cost if not self.optimization_history else min(prior_best, cost)
        self.optimization_history.append((evaluation_count, cost, best_cost))
        self.optimization_eval_records.append(
            OptimizationEvalRecord(
                evaluation_count=evaluation_count,
                unique_evaluation_count=unique_evaluation_count,
                backend_name=backend_name,
                elapsed_seconds=elapsed_seconds,
                is_best=bool(payload.get("is_best", False)),
                cost=cost,
                result=result,
            )
        )
        self.optimization_last_result = result
        if bool(payload.get("is_best", False)):
            self.optimization_best_result = result
            self.export_best_sparams_button.setEnabled(True)
        objective = getattr(result, "objective", None)
        error = getattr(result, "error", None)
        if error is not None:
            line = f"Eval {evaluation_count}: error={error}"
        elif objective is None:
            line = f"Eval {evaluation_count}: no objective"
        else:
            marker = " [best]" if bool(payload.get("is_best", False)) else ""
            line = f"Eval {evaluation_count}: cost={float(objective.total_cost):.6g}{marker}"
        self.optimization_log_lines.append(line)
        self.optimization_log_lines = self.optimization_log_lines[-40:]
        self._set_optimization_status(
            ("Optimization paused." if self._is_optimization_paused() else f"Running optimization: {evaluation_count} evals, best cost {best_cost:.6g}")
        )
        self._draw_convergence_plot()
        self._refresh_optimization_text()
        self._refresh_optimization_eval_table()
        if self.optimization_manual_selected_eval is None:
            self._apply_optimization_preview_selection()
        else:
            self._refresh_selected_optimization_detail()
        self._set_optimization_controls_running(True)

    def _finish_optimization(self, result: object, cancelled: bool, run_dir: str) -> None:
        if self._is_closing:
            return
        if result is not None and self.optimization_best_result is None:
            self.optimization_best_result = result
        if self.optimization_best_result is not None:
            self.export_best_sparams_button.setEnabled(True)
        eval_count = 0 if not self.optimization_history else self.optimization_history[-1][0]
        best_cost = (
            math.nan
            if self.optimization_best_result is None or getattr(self.optimization_best_result, "objective", None) is None
            else float(self.optimization_best_result.objective.total_cost)
        )
        outcome = "cancelled" if cancelled else "completed"
        if math.isfinite(best_cost):
            self._set_optimization_status(
                f"Optimization {outcome}: {eval_count} evals, best cost {best_cost:.6g}"
            )
        else:
            self._set_optimization_status(f"Optimization {outcome}.")
        self._set_status(f"Optimization {outcome}.")
        self.optimization_log_lines.append(f"Optimization {outcome}. Run dir: {run_dir}")
        self._refresh_optimization_text()
        self._write_run_history_entry(
            Path(run_dir),
            status=outcome,
            snapshot_yaml=self.optimization_launch_snapshot_yaml,
        )
        self._apply_optimization_preview_selection()

    def _handle_optimization_failure(self, error: str, run_dir: str) -> None:
        if self._is_closing:
            return
        self._set_optimization_status(f"Optimization failed: {error}")
        self._set_status("Optimization failed.")
        self.optimization_log_lines.append(f"Optimization failed in {run_dir}: {error}")
        self._refresh_optimization_text()
        self._draw_best_metrics_plot()
        self._write_run_history_entry(
            Path(run_dir),
            status="failed",
            snapshot_yaml=self.optimization_launch_snapshot_yaml,
            error=error,
        )
        self._update_optimization_run_metadata()
        self._set_optimization_controls_running(False)

    def _set_preview_from_result(self, result, *, title: str = "Current Design Preview") -> None:
        if result is None:
            return
        preview_path = None
        layout = getattr(result, "layout", None)
        if layout is not None and getattr(layout, "preview_path", None) is not None:
            preview_path = Path(layout.preview_path)
        if preview_path is None or not preview_path.exists():
            try:
                layout = export_transformer_layout(
                    geometry=result.geometry,
                    run_config=self.run_config,
                    out_dir=self.preview_dir / "best_preview",
                    validate_geometry=False,
                )
                preview_path = layout.preview_path
            except Exception:
                return
        self.last_preview_path = None if preview_path is None else Path(preview_path)
        self.last_debug_preview_path = None if layout is None or getattr(layout, "debug_preview_path", None) is None else Path(layout.debug_preview_path)
        self.current_image = _theme_preview_image_array(mpimg.imread(preview_path))
        self._draw_preview_image(image=self.current_image, geometry=result.geometry, layout=layout, title=title)
        self._schedule_3d_view_refresh(layout)

    def _draw_convergence_plot(self) -> None:
        self.convergence_axis.clear()
        if not self.optimization_history:
            self.convergence_axis.set_title("No optimization data yet")
            self.convergence_axis.set_xlabel("Evaluation")
            self.convergence_axis.set_ylabel("Objective cost")
            self.convergence_axis.grid(True, alpha=0.25)
            self.convergence_axis.xaxis.offsetText.set_color("#58483a")
            self.convergence_axis.yaxis.offsetText.set_color("#58483a")
            self.convergence_figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.985), pad=0.75)
            self._draw_canvas_idle(self.convergence_canvas)
            return
        evals = [point[0] for point in self.optimization_history]
        costs = [point[1] for point in self.optimization_history]
        best_costs = [point[2] for point in self.optimization_history]
        self.convergence_axis.plot(evals, costs, color="#9aa5b1", linewidth=1.2, marker="o", markersize=3, label="Current")
        self.convergence_axis.plot(evals, best_costs, color="#0b5cab", linewidth=2.0, label="Best so far")
        finite_costs = [value for value in costs + best_costs if math.isfinite(value) and value > 0.0]
        if finite_costs:
            self.convergence_axis.set_yscale("log")
        self.convergence_axis.set_title("Objective Convergence")
        self.convergence_axis.set_xlabel("Evaluation")
        self.convergence_axis.set_ylabel("Objective cost")
        self.convergence_axis.grid(True, alpha=0.25)
        self.convergence_axis.legend(loc="best")
        self.convergence_axis.xaxis.offsetText.set_color("#58483a")
        self.convergence_axis.yaxis.offsetText.set_color("#58483a")
        self.convergence_figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.985), pad=0.75)
        self._draw_canvas_idle(self.convergence_canvas)

    def _optimization_metrics_plot_result(self) -> tuple[object | None, str]:
        selected_record = self._selected_optimization_record()
        if selected_record is not None:
            return selected_record.result, f"Evaluation {selected_record.evaluation_count}"
        if self.optimization_preview_mode == "latest" and self.optimization_last_result is not None:
            return self.optimization_last_result, "Latest evaluated"
        if self.optimization_best_result is not None:
            return self.optimization_best_result, "Best so far"
        if self.optimization_last_result is not None:
            return self.optimization_last_result, "Latest evaluated"
        return None, "No optimization result"

    def _draw_best_metrics_plot(self) -> None:
        for axis in (self.best_k_axis, self.best_q_axis, self.best_l_axis):
            axis.clear()
            axis.grid(True, alpha=0.25)
        self.best_metrics_cursors = []
        if self.best_metrics_motion_connection is not None:
            self.best_metrics_canvas.mpl_disconnect(self.best_metrics_motion_connection)
            self.best_metrics_motion_connection = None
        plot_result, plot_label = self._optimization_metrics_plot_result()
        curves = None
        if plot_result is not None and getattr(plot_result, "target", None) is not None:
            curves = _frequency_metric_curves(
                plot_result,
                differential_reference_impedance_ohm=plot_result.target.differential_reference_impedance_ohm,
            )
        try:
            self.best_metrics_context_label.setText(f"Viewing: {plot_label}")
        except RuntimeError:
            pass
        if curves is None:
            self.best_k_axis.set_title("No optimization frequency data")
            self.best_k_axis.set_ylabel("k")
            self.best_q_axis.set_ylabel("Q")
            self.best_l_axis.set_ylabel("L (nH)")
            self.best_l_axis.set_xlabel("Frequency (GHz)")
            try:
                self.best_metrics_cursor_label.setText("Cursor: no optimization frequency data")
            except RuntimeError:
                pass
            self.best_metrics_figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.972), pad=0.75, h_pad=0.55)
            self._draw_canvas_idle(self.best_metrics_canvas)
            return

        freqs_ghz = curves["freqs_ghz"]
        self.best_k_axis.plot(freqs_ghz, curves["k"], color="#0b5cab", linewidth=1.8)
        self.best_k_axis.set_title(f"{plot_label} Frequency Metrics")
        self.best_k_axis.set_ylabel("k")
        self.best_q_axis.plot(freqs_ghz, curves["q_primary"], color="#d55e00", linewidth=1.6, label="Qp")
        self.best_q_axis.plot(freqs_ghz, curves["q_secondary"], color="#009e73", linewidth=1.6, label="Qs")
        self.best_q_axis.set_ylabel("Q")
        self.best_q_axis.legend(loc="best")
        self.best_l_axis.plot(freqs_ghz, 1.0e9 * curves["lp_h"], color="#7b3294", linewidth=1.6, label="Lp")
        self.best_l_axis.plot(freqs_ghz, 1.0e9 * curves["ls_h"], color="#008837", linewidth=1.6, label="Ls")
        self.best_l_axis.set_ylabel("L (nH)")
        self.best_l_axis.set_xlabel("Frequency (GHz)")
        self.best_l_axis.legend(loc="best")
        for axis in (self.best_k_axis, self.best_q_axis, self.best_l_axis):
            axis.xaxis.offsetText.set_color("#58483a")
            axis.yaxis.offsetText.set_color("#58483a")
        try:
            self.best_metrics_cursor_label.setText("Cursor: move over a plot to read x/y values")
        except RuntimeError:
            pass
        for axis in (self.best_k_axis, self.best_q_axis, self.best_l_axis):
            self.best_metrics_cursors.append(Cursor(axis, useblit=False, color="#4a4a4a", linewidth=0.8))

        def _on_motion(event) -> None:
            if event is None or event.inaxes is None or event.xdata is None or event.ydata is None:
                return
            ylabel = event.inaxes.get_ylabel() or event.inaxes.get_title() or "value"
            try:
                self.best_metrics_cursor_label.setText(
                    f"Cursor: f={float(event.xdata):.6g} GHz, {ylabel}={float(event.ydata):.6g}"
                )
            except RuntimeError:
                return

        self.best_metrics_motion_connection = self.best_metrics_canvas.mpl_connect("motion_notify_event", _on_motion)
        self.best_metrics_figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.972), pad=0.75, h_pad=0.55)
        self._draw_canvas_idle(self.best_metrics_canvas)

    def _export_best_sparams(self) -> None:
        result = self.optimization_best_result
        if result is None:
            self._set_status("No best optimization result is available.")
            return
        export_result = (
            result.single_ended_sparams
            if getattr(result, "single_ended_sparams", None) is not None
            else getattr(result, "differential_sparams", None)
        )
        source_path = getattr(result, "touchstone_path", None)
        if source_path is None and export_result is None:
            self._set_status("Best result does not have any S-parameter data to export.")
            return
        if source_path is not None:
            initial_path = Path(source_path)
        else:
            suffix = ".s4p" if export_result is not None and export_result.num_ports == 4 else ".s2p"
            initial_path = self.preview_dir / f"best_result{suffix}"
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export best S-parameters",
            str(initial_path),
            "Touchstone files (*.s2p *.s4p *.s6p *.s8p);;All files (*.*)",
        )
        if not selected:
            return
        target_path = Path(selected).expanduser().resolve()
        try:
            if source_path is not None and Path(source_path).exists():
                shutil.copyfile(Path(source_path), target_path)
            elif export_result is not None:
                export_result.to_touchstone(target_path)
            else:
                raise RuntimeError("Best result does not contain exportable S-parameter data")
        except Exception as exc:
            self._set_status(f"Cannot export best S-parameters: {exc}")
            return
        self._set_status(f"Exported best S-parameters to {target_path}")

    def _refresh_optimization_text(self) -> None:
        if not hasattr(self, "optimization_text") or self.optimization_text is None:
            return
        status_text = "Optimization idle."
        if hasattr(self, "optimization_status_label") and self.optimization_status_label is not None:
            status_text = self.optimization_status_label.text()
        lines = [f"Status: {status_text}"]
        lines.append(f"Backend: {self.optimization_launch_snapshot_name or self._combo_text('optimizer.name')}")
        if self.optimization_run_dir is not None:
            lines.append(f"Run dir: {self.optimization_run_dir}")
        if self.optimization_launch_snapshot_yaml is not None:
            lines.append("Active run config: snapshot at start")
        if self._is_optimization_running() or self.optimization_launch_snapshot_yaml is not None:
            editor_state = (
                "modified since run start"
                if self.optimization_editor_dirty_since_start
                else "matches the active run snapshot"
            )
            lines.append(f"Editor state: {editor_state}")
        if self.optimization_history:
            lines.append(f"Evaluations: {self.optimization_history[-1][0]}")
            lines.append(f"Latest cost: {self.optimization_history[-1][1]:.6g}")
            lines.append(f"Best cost: {self.optimization_history[-1][2]:.6g}")
        if self.optimization_blocking_errors:
            lines.append("")
            lines.append("Blocking issues:")
            lines.extend(f"- {line}" for line in self.optimization_blocking_errors)
        if self.optimization_best_result is not None:
            lines.append("")
            lines.append("Best result:")
            lines.extend(self._format_eval_result_summary(self.optimization_best_result))
        if self.optimization_log_lines:
            lines.append("")
            lines.append("Recent events:")
            lines.extend(f"- {line}" for line in self.optimization_log_lines[-20:])
        text = "\n".join(lines)
        self.optimization_text.setPlainText(text)
        if self.optimization_summary_text is not None:
            self.optimization_summary_text.setPlainText(text)

    @staticmethod
    def _format_eval_result_summary(result) -> list[str]:
        lines = [
            f"cache_key: {result.cache_key}",
            f"work_dir: {result.work_dir}",
        ]
        if getattr(result, "touchstone_path", None) is not None:
            lines.append(f"touchstone: {result.touchstone_path}")
        if result.objective is not None:
            lines.append(f"cost: {float(result.objective.total_cost):.6g}")
        if result.metrics is not None:
            lines.append(
                "metrics: "
                f"Lp={float(result.metrics.lp_h):.6g} H, "
                f"Ls={float(result.metrics.ls_h):.6g} H, "
                f"k={float(result.metrics.k):.6g}, "
                f"Qp={float(result.metrics.q_primary):.6g}, "
                f"Qs={float(result.metrics.q_secondary):.6g}"
            )
        if result.error is not None:
            lines.append(f"error: {result.error}")
        return lines

    def _parse_range_text(self, key: str, fallback: tuple[float, float]) -> tuple[float, float]:
        raw = self._text(f"bounds.{key}")
        if not raw:
            return tuple(map(float, fallback))
        pieces = [piece.strip() for piece in raw.replace(";", ",").split(",") if piece.strip()]
        if len(pieces) != 2:
            raise ValueError(f"{key} must be expressed as 'lo, hi'")
        lo, hi = float(pieces[0]), float(pieces[1])
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)

    @staticmethod
    def _optional_string(value: str) -> str | None:
        stripped = str(value).strip()
        return None if not stripped else stripped

    def _optional_path_string(self, value: str) -> str | None:
        stripped = str(value).strip()
        if not stripped:
            return None
        return str(resolve_local_path(stripped))

    @staticmethod
    def _split_extra_args_csv(raw: str) -> list[str]:
        return [piece.strip() for piece in str(raw).split(",") if piece.strip()]

    @staticmethod
    def _extract_parallel_extra_arg(extra_args: list[str] | tuple[str, ...]) -> int | None:
        for arg in extra_args:
            stripped = str(arg).strip()
            if not stripped.startswith("--parallel="):
                continue
            try:
                return int(stripped.split("=", 1)[1])
            except Exception:
                return None
        return None

    def _build_emx_extra_args(self) -> list[str]:
        extra_args = [
            arg
            for arg in self._split_extra_args_csv(self._text("emx.extra_args"))
            if not arg.startswith("--parallel=")
        ]
        parallel_jobs = self._optional_number_string(self._text("emx.parallel"))
        if parallel_jobs is not None:
            extra_args.append(f"--parallel={int(parallel_jobs)}")
        return extra_args

    def _resolved_process_file_for_yaml(self) -> str:
        raw = self._text("emx.emx_process_file")
        if not raw:
            raw = self.run_config.emx.emx_process_file
        if self._combo_text("emx.execution_mode") == "remote_ssh":
            return raw
        try:
            return str(self._local_process_file_path(raw, interactive=False))
        except Exception:
            return raw

    def _local_process_file_path(self, raw: str | None = None, *, interactive: bool = True) -> Path:
        stripped = self._text("emx.emx_process_file") if raw is None else str(raw).strip()
        if not stripped:
            stripped = self.run_config.emx.emx_process_file
        candidate = resolve_local_path(stripped, extra_roots=(bundled_proc_dir(),))
        if candidate.exists():
            return candidate
        if interactive:
            chosen = self._browse_for_process_file(candidate)
            if chosen is not None:
                return chosen
            QtWidgets.QMessageBox.critical(
                self,
                "Missing Process File",
                f"Could not find process file:\n{candidate}\n\nSelect a valid .proc file to continue.",
            )
        raise FileNotFoundError(candidate)

    def _cached_proc_info_from_path(self, proc_path: Path):
        resolved = Path(proc_path).resolve()
        stat = resolved.stat()
        cache_key = (str(resolved), int(stat.st_mtime_ns), int(stat.st_size))
        cached = self.proc_info_cache.get(cache_key)
        if cached is not None:
            return cached
        proc_info = parse_proc_file(resolved)
        self.proc_info_cache = {cache_key: proc_info}
        return proc_info

    def _cached_proc_info(self, raw: str | None = None, *, interactive: bool = False):
        proc_path = self._local_process_file_path(raw, interactive=interactive)
        return self._cached_proc_info_from_path(proc_path)

    def _resolved_emx_binary_for_yaml(self) -> str:
        raw = self._text("emx.emx_binary") or "emx"
        if self._combo_text("emx.execution_mode") == "remote_ssh":
            return raw
        found = shutil.which(raw)
        return raw if found is None else found

    def _resolved_emx_home_for_yaml(self) -> str | None:
        raw = self._text("emx.emx_home")
        return self._optional_string(raw)

    @staticmethod
    def _optional_number_string(value: str) -> int | float | None:
        stripped = str(value).strip()
        if not stripped:
            return None
        if any(token in stripped.lower() for token in (".", "e", "inf")):
            return float(stripped)
        return int(stripped)

    @staticmethod
    def _optional_layer_value(value: int | None) -> int | None:
        if value is None:
            return None
        parsed = int(value)
        return None if parsed <= 0 else parsed

    def _inferred_bridge_route_fields(self, *, coil_layer: int, bridge_layer: int) -> dict[str, int | None]:
        proc_info = self._cached_proc_info(interactive=True)
        route = infer_bridge_route_layers(
            proc_info,
            coil_layer=int(coil_layer),
            bridge_layer=int(bridge_layer),
        )
        return {
            "bridge_layer": int(route.bridge_layer),
            "bridge_via_layer": int(route.bridge_via_layer),
            "bridge_lower_layer": None if route.bridge_lower_layer is None else int(route.bridge_lower_layer),
            "bridge_lower_via_layer": None if route.bridge_lower_via_layer is None else int(route.bridge_lower_via_layer),
        }

    @staticmethod
    def _display_bridge_target_layer(first_bridge_layer: int | None, final_bridge_layer: int | None) -> int | None:
        return final_bridge_layer if final_bridge_layer is not None else first_bridge_layer

    def _bound_fallback_for_key(self, key: str) -> tuple[float, float]:
        bounds = self.run_config.bounds
        mapping: dict[str, tuple[float, float]] = {
            "primary_outer_width_um": bounds.primary.outer_width_um,
            "primary_outer_height_um": bounds.primary.outer_height_um,
            "secondary_outer_width_um": bounds.secondary.outer_width_um,
            "secondary_outer_height_um": bounds.secondary.outer_height_um,
            "primary_width_um": bounds.primary.trace_width_um,
            "primary_spacing_um": bounds.primary.spacing_um,
            "primary_terminal_y_span_um": bounds.primary.terminal_y_span_um,
            "primary_feed_extension_um": bounds.primary.feed_extension_um,
            "secondary_width_um": bounds.secondary.trace_width_um,
            "secondary_spacing_um": bounds.secondary.spacing_um,
            "secondary_terminal_y_span_um": bounds.secondary.terminal_y_span_um,
            "secondary_feed_extension_um": bounds.secondary.feed_extension_um,
            "offset_um": bounds.offset_um,
        }
        if key not in mapping:
            raise KeyError(key)
        return tuple(map(float, mapping[key]))

    def _on_bounds_entry_change(self, key: str) -> None:
        slider_name = BOUND_SLIDER_FIELDS.get(key)
        if slider_name is not None and slider_name in self.slider_bindings:
            try:
                lo, hi = self._parse_range_text(key, self._bound_fallback_for_key(key))
            except Exception:
                self._schedule_refresh()
                return
            current_value = self._slider_value(slider_name)
            self._set_slider_bounds(slider_name, lo, hi)
            self._set_slider_value(slider_name, current_value)
        self._schedule_refresh()

    def _current_config_payload(self) -> dict[str, object]:
        current_cfg = self.run_config
        primary_turns = int(self._combo_text("topology.primary_turns") or "1")
        secondary_turns = int(self._combo_text("topology.secondary_turns") or "1")
        effective_topology_mode = self._effective_topology_mode(
            primary_turns=primary_turns,
            secondary_turns=secondary_turns,
        )
        primary_coil_layer = int(self.stackup_values["ap_layer"])
        secondary_coil_layer = int(self.stackup_values["m9_layer"])
        primary_vdd_bar_enabled = self._checked("topology.primary_vdd_bar_enabled")
        secondary_vdd_bar_enabled = self._checked("topology.secondary_vdd_bar_enabled")
        primary_vdd_bar_layer = self._optional_layer_value(self.layer_choice_values.get("primary_vdd_bar_layer"))
        secondary_vdd_bar_layer = self._optional_layer_value(self.layer_choice_values.get("secondary_vdd_bar_layer"))
        if primary_vdd_bar_enabled and primary_vdd_bar_layer is None:
            primary_vdd_bar_layer = primary_coil_layer
        if secondary_vdd_bar_enabled and secondary_vdd_bar_layer is None:
            secondary_vdd_bar_layer = secondary_coil_layer
        primary_vdd_bar_width = self._optional_number_string(self._text("topology.primary_vdd_bar_width_um"))
        secondary_vdd_bar_width = self._optional_number_string(self._text("topology.secondary_vdd_bar_width_um"))
        primary_vdd_bar_offset = float(self._text("topology.primary_vdd_bar_offset_um") or "0.0")
        secondary_vdd_bar_offset = float(self._text("topology.secondary_vdd_bar_offset_um") or "0.0")
        primary_bridge_target_layer = None if primary_turns <= 1 else int(self.stackup_values["primary_bridge_layer"])
        secondary_bridge_target_layer = None if secondary_turns <= 1 else int(self.stackup_values["secondary_bridge_layer"])
        primary_bridge_section = self._default_bridge_section_bounds(
            turns=primary_turns,
            existing=current_cfg.bounds.primary.bridge_section,
            is_primary=True,
            topology_mode=effective_topology_mode,
        )
        secondary_bridge_section = self._default_bridge_section_bounds(
            turns=secondary_turns,
            existing=current_cfg.bounds.secondary.bridge_section,
            is_primary=False,
            topology_mode=effective_topology_mode,
        )
        primary_route = (
            None
            if primary_turns <= 1
            else self._inferred_bridge_route_fields(
                coil_layer=primary_coil_layer,
                bridge_layer=int(primary_bridge_target_layer),
            )
        )
        secondary_route = (
            None
            if secondary_turns <= 1
            else self._inferred_bridge_route_fields(
                coil_layer=secondary_coil_layer,
                bridge_layer=int(secondary_bridge_target_layer),
            )
        )
        q_target_mode = self._combo_text("target.q_target_mode")
        return {
            "target": {
                "f0_hz": float(self._text("target.f0_hz")),
                "lp_h": float(self._text("target.lp_h")),
                "ls_h": float(self._text("target.ls_h")),
                "k_target": float(self._text("target.k_target")),
                "q_target_mode": q_target_mode,
                "q_primary_target": (
                    None if q_target_mode != "target" else float(self._text("target.q_primary_target"))
                ),
                "q_secondary_target": (
                    None if q_target_mode != "target" else float(self._text("target.q_secondary_target"))
                ),
                "topology_mode": effective_topology_mode,
                "differential_reference_impedance_ohm": float(self._text("target.differential_reference_impedance_ohm")),
                "band_points": int(self._text("target.band_points")),
                "fractional_bandwidth": float(self._text("target.fractional_bandwidth")),
            },
            "topology": {
                "primary": {
                    "turns": primary_turns,
                    "center_tap": self._checked("topology.primary_center_tap"),
                    "vdd_bar": {
                        "enabled": primary_vdd_bar_enabled,
                        "bar_layer": primary_vdd_bar_layer,
                        "width_um": primary_vdd_bar_width,
                        "offset_um": primary_vdd_bar_offset,
                    },
                },
                "secondary": {
                    "turns": secondary_turns,
                    "center_tap": self._checked("topology.secondary_center_tap"),
                    "vdd_bar": {
                        "enabled": secondary_vdd_bar_enabled,
                        "bar_layer": secondary_vdd_bar_layer,
                        "width_um": secondary_vdd_bar_width,
                        "offset_um": secondary_vdd_bar_offset,
                    },
                },
                "shield": {
                    "enabled": self._checked("transformer.shield.enabled"),
                    "kind": "ring",
                    "margin_um": float(self._text("transformer.shield.margin_um")),
                    "width_um": float(self._text("transformer.shield.width_um")),
                },
                "primary_bridge_section_pad_height_ratio": (
                    None if primary_bridge_section is None else float(primary_bridge_section.pad_height_ratio)
                ),
                "primary_bridge_section_via_size_ratio": (
                    None if primary_bridge_section is None else float(primary_bridge_section.via_size_ratio)
                ),
                "primary_bridge_section_via_width_ratio": (
                    None if primary_bridge_section is None else float(primary_bridge_section.via_width_ratio)
                ),
                "primary_bridge_section_via_spacing_ratio": (
                    None if primary_bridge_section is None else float(primary_bridge_section.via_spacing_ratio)
                ),
                "secondary_bridge_section_pad_height_ratio": (
                    None if secondary_bridge_section is None else float(secondary_bridge_section.pad_height_ratio)
                ),
                "secondary_bridge_section_via_size_ratio": (
                    None if secondary_bridge_section is None else float(secondary_bridge_section.via_size_ratio)
                ),
                "secondary_bridge_section_via_width_ratio": (
                    None if secondary_bridge_section is None else float(secondary_bridge_section.via_width_ratio)
                ),
                "secondary_bridge_section_via_spacing_ratio": (
                    None if secondary_bridge_section is None else float(secondary_bridge_section.via_spacing_ratio)
                ),
            },
            "bounds": {
                "topology_mode": effective_topology_mode,
                "primary_outer_width_um": self._parse_range_text("primary_outer_width_um", current_cfg.bounds.primary.outer_width_um),
                "primary_outer_height_um": self._parse_range_text("primary_outer_height_um", current_cfg.bounds.primary.outer_height_um),
                "secondary_outer_width_um": self._parse_range_text("secondary_outer_width_um", current_cfg.bounds.secondary.outer_width_um),
                "secondary_outer_height_um": self._parse_range_text("secondary_outer_height_um", current_cfg.bounds.secondary.outer_height_um),
                "primary_width_um": self._parse_range_text("primary_width_um", current_cfg.bounds.primary.trace_width_um),
                "primary_spacing_um": self._parse_range_text("primary_spacing_um", current_cfg.bounds.primary.spacing_um),
                "primary_terminal_y_span_um": self._parse_range_text(
                    "primary_terminal_y_span_um", current_cfg.bounds.primary.terminal_y_span_um
                ),
                "primary_feed_extension_um": self._parse_range_text(
                    "primary_feed_extension_um", current_cfg.bounds.primary.feed_extension_um
                ),
                "secondary_width_um": self._parse_range_text("secondary_width_um", current_cfg.bounds.secondary.trace_width_um),
                "secondary_spacing_um": self._parse_range_text("secondary_spacing_um", current_cfg.bounds.secondary.spacing_um),
                "secondary_terminal_y_span_um": self._parse_range_text(
                    "secondary_terminal_y_span_um", current_cfg.bounds.secondary.terminal_y_span_um
                ),
                "secondary_feed_extension_um": self._parse_range_text(
                    "secondary_feed_extension_um", current_cfg.bounds.secondary.feed_extension_um
                ),
                "offset_um": self._parse_range_text("offset_um", current_cfg.bounds.offset_um),
                "primary_bridge_section_pad_height_ratio": (
                    None
                    if primary_bridge_section is None
                    else float(self._text("bounds.primary_bridge_section_pad_height_ratio") or primary_bridge_section.pad_height_ratio)
                ),
                "primary_bridge_section_via_size_ratio": (
                    None
                    if primary_bridge_section is None
                    else float(self._text("bounds.primary_bridge_section_via_size_ratio") or primary_bridge_section.via_size_ratio)
                ),
                "primary_bridge_section_via_width_ratio": (
                    None
                    if primary_bridge_section is None
                    else float(self._text("bounds.primary_bridge_section_via_width_ratio") or primary_bridge_section.via_width_ratio)
                ),
                "primary_bridge_section_via_spacing_ratio": (
                    None
                    if primary_bridge_section is None
                    else float(self._text("bounds.primary_bridge_section_via_spacing_ratio") or primary_bridge_section.via_spacing_ratio)
                ),
                "secondary_bridge_section_pad_height_ratio": (
                    None
                    if secondary_bridge_section is None
                    else float(self._text("bounds.secondary_bridge_section_pad_height_ratio") or secondary_bridge_section.pad_height_ratio)
                ),
                "secondary_bridge_section_via_size_ratio": (
                    None
                    if secondary_bridge_section is None
                    else float(self._text("bounds.secondary_bridge_section_via_size_ratio") or secondary_bridge_section.via_size_ratio)
                ),
                "secondary_bridge_section_via_width_ratio": (
                    None
                    if secondary_bridge_section is None
                    else float(self._text("bounds.secondary_bridge_section_via_width_ratio") or secondary_bridge_section.via_width_ratio)
                ),
                "secondary_bridge_section_via_spacing_ratio": (
                    None
                    if secondary_bridge_section is None
                    else float(self._text("bounds.secondary_bridge_section_via_spacing_ratio") or secondary_bridge_section.via_spacing_ratio)
                ),
            },
            "emx": {
                "emx_binary": self._resolved_emx_binary_for_yaml(),
                "emx_home": self._resolved_emx_home_for_yaml(),
                "emx_process_file": self._resolved_process_file_for_yaml(),
                "top_cell_prefix": self._text("emx.top_cell_prefix"),
                "extra_args": self._build_emx_extra_args(),
                "use_cadence_license_env": self._checked("emx.use_cadence_license_env"),
                "skip_os_check": self._checked("emx.skip_os_check"),
                "execution_mode": self._combo_text("emx.execution_mode"),
                "license_file": (
                    self._optional_string(self._text("emx.license_file"))
                    if self._combo_text("emx.execution_mode") == "remote_ssh"
                    else self._optional_path_string(self._text("emx.license_file"))
                ),
                "cdslmd_license_file": (
                    self._optional_string(self._text("emx.cdslmd_license_file"))
                    if self._combo_text("emx.execution_mode") == "remote_ssh"
                    else self._optional_path_string(self._text("emx.cdslmd_license_file"))
                ),
                "remote_ssh_host": self._optional_string(self._text("emx.remote_ssh_host")),
                "remote_ssh_command": (
                    self._text("emx.remote_ssh_command").strip()
                    or self.run_config.emx.remote_ssh_command
                ),
                "remote_scp_command": (
                    self._text("emx.remote_scp_command").strip()
                    or self.run_config.emx.remote_scp_command
                ),
                "remote_repo_root": self._optional_string(self._text("emx.remote_repo_root")),
                "remote_work_root": (
                    self._text("emx.remote_work_root").strip()
                    or self.run_config.emx.remote_work_root
                ),
                "remote_python": (
                    self._text("emx.remote_python").strip()
                    or self.run_config.emx.remote_python
                ),
                "remote_venv_activate": self._optional_string(self._text("emx.remote_venv_activate")),
                "remote_emx_process_file": self._optional_string(self._text("emx.remote_emx_process_file")),
                "port_mode": self._combo_text("emx.port_mode"),
                "primary_coil_layer": primary_coil_layer,
                "secondary_coil_layer": secondary_coil_layer,
                "m5_layer": int(self.stackup_values.get("m5_layer", self.run_config.emx.m5_layer)),
                "primary_bridge_target_layer": primary_bridge_target_layer,
                "primary_bridge_layer": None if primary_route is None else primary_route["bridge_layer"],
                "primary_bridge_via_layer": None if primary_route is None else primary_route["bridge_via_layer"],
                "primary_bridge_lower_layer": None if primary_route is None else primary_route["bridge_lower_layer"],
                "primary_bridge_lower_via_layer": None if primary_route is None else primary_route["bridge_lower_via_layer"],
                "secondary_bridge_target_layer": secondary_bridge_target_layer,
                "secondary_bridge_layer": None if secondary_route is None else secondary_route["bridge_layer"],
                "secondary_bridge_via_layer": None if secondary_route is None else secondary_route["bridge_via_layer"],
                "secondary_bridge_lower_layer": None if secondary_route is None else secondary_route["bridge_lower_layer"],
                "secondary_bridge_lower_via_layer": None if secondary_route is None else secondary_route["bridge_lower_via_layer"],
                "shield_layer": self._optional_layer_value(self.layer_choice_values.get("shield_layer")),
                "metal_datatype": int(self.hidden_emx_values["metal_datatype"]),
                "label_layer": int(self.hidden_emx_values["label_layer"]),
                "label_datatype": int(self.hidden_emx_values["label_datatype"]),
            },
            "optimizer": {
                "name": self._combo_text("optimizer.name"),
                "max_evaluations": int(self._text("optimizer.max_evaluations")),
                "warm_start_samples": int(self._text("optimizer.warm_start_samples")),
                "warm_start_paths": tuple(
                    token.strip()
                    for token in self._text("optimizer.warm_start_paths").split(",")
                    if token.strip()
                ),
                "seed": int(self._text("optimizer.seed")),
                "resume_from_checkpoint": self._checked("optimizer.resume_from_checkpoint"),
                "checkpoint_interval_evaluations": int(self._text("optimizer.checkpoint_interval_evaluations")),
                "cma_es": {
                    "population_size": self._optional_number_string(self._text("optimizer.cma_es.population_size")),
                    "sigma0": self._optional_number_string(self._text("optimizer.cma_es.sigma0")),
                    "verbose": int(self._text("optimizer.cma_es.verbose")),
                },
                "turbo": {
                    "initial_length": float(self._text("optimizer.turbo.initial_length")),
                    "length_min": float(self._text("optimizer.turbo.length_min")),
                    "length_max": float(self._text("optimizer.turbo.length_max")),
                    "success_tolerance": int(self._text("optimizer.turbo.success_tolerance")),
                    "num_restarts": int(self._text("optimizer.turbo.num_restarts")),
                    "raw_samples": int(self._text("optimizer.turbo.raw_samples")),
                    "n_candidates": self._optional_number_string(self._text("optimizer.turbo.n_candidates")),
                    "max_cholesky_size": self._optional_number_string(self._text("optimizer.turbo.max_cholesky_size")),
                    "acquisition_function": self._combo_text("optimizer.turbo.acquisition_function"),
                },
            },
        }

    def _current_run_config(self):
        self._refresh_stackup_controls()
        payload = self._current_config_payload()
        yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
        self.generated_config_path.write_text(yaml_text, encoding="utf-8")
        return load_run_config_from_raw(payload)

    def _geometry_from_controls(self) -> TransformerGeometrySpec:
        run_config = self._current_run_config()
        adapter = TransformerOptimizationAdapter(run_config.bounds)
        values = [self._slider_value(name) for name in adapter.field_order()]
        return adapter.from_vector(values)

    def _apply_discrete_controls(self, geometry: TransformerGeometrySpec) -> TransformerGeometrySpec:
        return geometry.with_topology(
            primary_turns=int(self._combo_text("topology.primary_turns")),
            secondary_turns=int(self._combo_text("topology.secondary_turns")),
            primary_center_tap=self._checked("topology.primary_center_tap"),
            secondary_center_tap=self._checked("topology.secondary_center_tap"),
        )

    def _load_geometry(self, geometry: TransformerGeometrySpec) -> None:
        with self._signal_guard():
            for name, value in geometry.flat_dict().items():
                if name not in self.slider_bindings:
                    continue
                self._set_slider_value(name, value)

    def _default_bridge_section_bounds(
        self,
        *,
        turns: int,
        existing: BridgeSectionConfig | None,
        is_primary: bool,
        topology_mode: TopologyMode | None = None,
    ) -> BridgeSectionConfig | None:
        _ = is_primary
        resolved_topology_mode = self.topology_mode if topology_mode is None else topology_mode
        if int(turns) <= 1:
            return None
        if resolved_topology_mode == "1t1t":
            return None
        if existing is not None:
            return existing
        return BridgeSectionConfig()

    @staticmethod
    def _effective_topology_mode(*, primary_turns: int, secondary_turns: int) -> TopologyMode:
        if int(primary_turns) <= 1 and int(secondary_turns) <= 1:
            return "1t1t"
        if int(primary_turns) <= 1:
            return "1t2t"
        if int(secondary_turns) <= 1:
            return "2t1t"
        return "2t2t"

    def _reload_parameter_bounds(self) -> None:
        for name in self.slider_bindings:
            if not hasattr(self.bounds, name):
                continue
            lo, hi = getattr(self.bounds, name)
            self._set_slider_bounds(name, float(lo), float(hi))

    def _capture_live_context(self) -> tuple[object, TransformerGeometrySpec, str]:
        run_config = self._current_run_config()
        self.run_config = run_config
        self.bounds = run_config.bounds
        geometry = self._geometry_from_controls()
        geometry = self._apply_discrete_controls(geometry)
        yaml_text = yaml.safe_dump(_run_config_to_payload(run_config), sort_keys=False, allow_unicode=False)
        return run_config, geometry, yaml_text

    def _build_live_context(self) -> tuple[object, TransformerGeometrySpec, object, list[str], str]:
        run_config, geometry, yaml_text = self._capture_live_context()
        geometry_errors = geometry.validate()
        gdstk_check = run_transformer_gdstk_checks(geometry, run_config)
        return run_config, geometry, gdstk_check, geometry_errors, yaml_text

    def _optimization_dependency_errors(self, run_config) -> list[str]:
        backend = str(run_config.optimizer.name).strip().lower()
        requirements: dict[str, tuple[tuple[str, str], ...]] = {
            "cma_es": (("cma", "cma"),),
            "turbo": (("torch", "torch"), ("gpytorch", "gpytorch"), ("botorch", "botorch")),
        }
        errors: list[str] = []
        if backend not in requirements:
            errors.append(f"Unsupported optimizer backend '{run_config.optimizer.name}'.")
            return errors
        for import_name, package_name in requirements[backend]:
            if importlib.util.find_spec(import_name) is None:
                errors.append(
                    f"Missing optimizer dependency '{package_name}' required for backend '{run_config.optimizer.name}'."
                )
        return errors

    def _collect_optimization_start_errors(
        self,
        *,
        run_config,
        geometry: TransformerGeometrySpec,
        gdstk_check,
        geometry_errors: list[str],
    ) -> list[str]:
        errors: list[str] = []
        errors.extend(str(error) for error in geometry_errors)
        errors.extend(str(error) for error in getattr(gdstk_check, "errors", ()) or ())
        try:
            self._local_process_file_path(run_config.emx.emx_process_file)
        except Exception as exc:
            errors.append(f"Process file check failed: {exc}")
        errors.extend(self._optimization_dependency_errors(run_config))
        _ = geometry
        return errors

    def _queue_preview_refresh(self, *, run_config, geometry: TransformerGeometrySpec, yaml_text: str) -> None:
        self.preview_refresh_generation += 1
        request = (self.preview_refresh_generation, run_config, geometry, yaml_text)
        self.preview_refresh_pending_request = request
        self._set_status("Refreshing preview...")
        self._start_next_preview_refresh()

    def _start_next_preview_refresh(self) -> None:
        if self.preview_refresh_thread is not None and self.preview_refresh_thread.isRunning():
            return
        if self.preview_refresh_pending_request is None:
            return
        generation, run_config, geometry, yaml_text = self.preview_refresh_pending_request
        self.preview_refresh_pending_request = None
        worker = PreviewRefreshWorkerThread(
            generation=generation,
            run_config=run_config,
            geometry=geometry,
            yaml_text=yaml_text,
            preview_dir=self.preview_dir,
            parent=self,
        )
        worker.completed.connect(self._handle_preview_refresh_completed)
        worker.failed.connect(self._handle_preview_refresh_failed)
        worker.finished.connect(self._cleanup_preview_refresh_thread)
        self.preview_refresh_thread = worker
        self.preview_refresh_running_generation = generation
        worker.start()

    def _cleanup_preview_refresh_thread(self) -> None:
        if self._is_closing:
            return
        if self.preview_refresh_thread is not None and not self.preview_refresh_thread.isRunning():
            self.preview_refresh_thread.deleteLater()
            self.preview_refresh_thread = None
        self.preview_refresh_running_generation = None
        self._start_next_preview_refresh()

    def _handle_preview_refresh_completed(self, payload: object) -> None:
        if self._is_closing:
            return
        data = dict(payload or {})
        generation = int(data.get("generation", -1))
        if generation != self.preview_refresh_generation:
            return
        run_config = data["run_config"]
        geometry = data["geometry"]
        yaml_text = str(data["yaml_text"])
        bounds_errors = list(data.get("bounds_errors", ()))
        geometry_errors = [*bounds_errors, *list(data.get("geometry_errors", ()))]
        gdstk_check = data["gdstk_check"]
        layout = data["layout"]
        image = data["image"]

        self.run_config = run_config
        self.bounds = run_config.bounds
        self.generated_config_path.write_text(yaml_text, encoding="utf-8")
        validation_content = self._format_validation(gdstk_check)
        if geometry_errors:
            validation_content += "\n\nGeometry spec validation:\n" + "\n".join(f"- {err}" for err in geometry_errors)
        self.validation_text.setPlainText(validation_content)
        self.proc_text.setPlainText(self._format_proc_stackup(run_config=run_config))

        self.last_preview_path = Path(layout.preview_path)
        self.last_debug_preview_path = None if getattr(layout, "debug_preview_path", None) is None else Path(layout.debug_preview_path)
        self.current_image = image
        self._draw_preview_image(image=self.current_image, geometry=geometry, layout=layout, title="Current Design Preview")
        self._schedule_3d_view_refresh(layout)

        gdstk_errors = [*geometry_errors, *list(getattr(gdstk_check, "errors", ()) or ())]
        if gdstk_errors:
            self._set_status(f"Preview refreshed with gdstk violations from {layout.preview_path}")
        else:
            self._set_status(f"Preview refreshed from {layout.preview_path}")

    def _handle_preview_refresh_failed(self, payload: object) -> None:
        if self._is_closing:
            return
        data = dict(payload or {})
        generation = int(data.get("generation", -1))
        if generation != self.preview_refresh_generation:
            return
        error = str(data.get("error", "unknown preview refresh error"))
        self.validation_text.setPlainText(f"Preview refresh failed: {error}")
        self.proc_text.setPlainText("")
        self._set_status(f"Preview refresh failed: {error}")
        self._draw_invalid_preview([f"Render failed: {error}"])

    def _refresh(self) -> None:
        try:
            run_config, geometry, yaml_text = self._capture_live_context()
        except Exception as exc:
            message = f"Config/geometry update failed: {exc}"
            self.validation_text.setPlainText(message)
            self.proc_text.setPlainText("")
            self._set_status(message)
            self._draw_invalid_preview([message])
            return

        if self._is_optimization_running():
            self._set_status("Config updated while optimization is running. The active run stays on its start snapshot.")
            self._update_optimization_run_metadata()
            return
        self._queue_preview_refresh(run_config=run_config, geometry=geometry, yaml_text=yaml_text)

    def _draw_invalid_preview(self, errors: list[str]) -> None:
        if self._is_closing:
            return
        self.preview_refresh_generation += 1
        self.preview_refresh_pending_request = None
        self.last_preview_path = None
        self.last_debug_preview_path = None
        self.current_preview_geometry = None
        self.current_preview_layout = None
        self.current_preview_display_image = None
        self.current_preview_geometry_mask = None
        self.current_preview_extent = None
        self.viewer3d_refresh_timer.stop()
        self.viewer3d_pending_layout = None
        self.axis.clear()
        self.axis.set_facecolor("#f6f2ec")
        self.axis.set_axis_off()
        self.axis.text(
            0.5,
            0.6,
            "Constraints not satisfied",
            ha="center",
            va="center",
            fontsize=16,
            weight="bold",
            color="#8a3f12",
            transform=self.axis.transAxes,
        )
        self.axis.text(
            0.5,
            0.35,
            "\n".join(f"- {err}" for err in errors[:6]),
            ha="center",
            va="center",
            fontsize=10,
            color="#58483a",
            transform=self.axis.transAxes,
        )
        self._draw_canvas_idle(self.canvas)
        self._clear_3d_view(reason="3D preview unavailable while the current geometry is invalid.")

    def _format_validation(self, gdstk_check) -> str:
        warnings = tuple(getattr(gdstk_check, "warnings", ()) or ())
        if not gdstk_check.errors:
            lines = ["Geometry validation checks:", "- geometry checks: pass"]
            if warnings:
                lines.append(f"- warning_count: {len(warnings)}")
            layer_summary = self._validation_layer_summary(gdstk_check.metrics)
            if layer_summary is not None:
                lines.append(f"- active layers: {layer_summary}")
                lines.append("- preview note: flattened preview overlays all layers; cross-layer crossings are allowed")
            if warnings:
                lines.append("")
                lines.append("Geometry warnings:")
                lines.extend(f"- {warning}" for warning in warnings)
            return "\n".join(lines)

        lines: list[str] = ["Geometry validation checks:"]
        elapsed_ms = gdstk_check.metrics.get("elapsed_ms")
        if elapsed_ms is not None:
            lines.append(f"- elapsed_ms: {float(elapsed_ms):.2f}")
        if warnings:
            lines.append(f"- warning_count: {len(warnings)}")
        for key in (
            "primary_conductive_components",
            "secondary_conductive_components",
            "same_layer_spacing_violations",
            "primary_feed_clearance_violations",
            "secondary_feed_clearance_violations",
            "primary_to_secondary_bridge_feed_clearance_violations",
            "primary_intermediate_bridge_pad_count",
            "primary_intermediate_bridge_pad_same_layer_checks",
            "primary_intermediate_bridge_pad_target_polygons",
            "primary_intermediate_bridge_pad_clearance_violations",
            "primary_via_checked",
            "primary_via_spacing_violations",
            "primary_via_enclosure_violations",
            "primary_via_redundancy_violations",
            "primary_via_recommended_enclosure_warnings",
            "primary_via_stacked_depth_warnings",
            "secondary_via_checked",
            "secondary_via_spacing_violations",
            "secondary_via_enclosure_violations",
            "secondary_via_redundancy_violations",
            "secondary_via_recommended_enclosure_warnings",
            "secondary_via_stacked_depth_warnings",
        ):
            if key in gdstk_check.metrics:
                lines.append(f"- {key}: {gdstk_check.metrics[key]}")
        debug_keys = (
            "checker_primary_coil_layer",
            "checker_secondary_coil_layer",
            "checker_primary_bridge_route_layers",
            "checker_secondary_bridge_route_layers",
            "checker_primary_bridge_stack_layers",
            "checker_secondary_bridge_stack_layers",
            "checker_secondary_coil_group_layers",
            "checker_secondary_feed_group_layers",
            "primary_intermediate_bridge_pad_checked_stage_layers",
            "primary_intermediate_bridge_pad_target_layer_counts",
        )
        debug_lines = [f"- {key}: {gdstk_check.metrics[key]}" for key in debug_keys if key in gdstk_check.metrics]
        if debug_lines:
            lines.append("")
            lines.append("Checker layer debug:")
            lines.extend(debug_lines)
        if warnings:
            lines.append("")
            lines.append("Geometry warnings:")
            lines.extend(f"- {warning}" for warning in warnings)
        if gdstk_check.errors:
            lines.append("")
            lines.append("Geometry violations:")
            lines.extend(f"- {err}" for err in gdstk_check.errors)
        return "\n".join(lines)

    @staticmethod
    def _validation_layer_summary(metrics: dict[str, float | int | bool | str]) -> str | None:
        primary_coil = metrics.get("checker_primary_coil_layer")
        secondary_coil = metrics.get("checker_secondary_coil_layer")
        primary_bridge = metrics.get("checker_primary_bridge_route_layers")
        secondary_bridge = metrics.get("checker_secondary_bridge_route_layers")
        if primary_coil is None or secondary_coil is None or primary_bridge is None or secondary_bridge is None:
            return None
        return (
            f"primary coil={primary_coil}; "
            f"primary bridge={primary_bridge}; "
            f"secondary coil={secondary_coil}; "
            f"secondary bridge={secondary_bridge}"
        )

    def _format_proc_stackup(self, run_config) -> str:
        try:
            proc_info = self._cached_proc_info(run_config.emx.emx_process_file, interactive=False)
        except Exception as exc:
            return f"Proc parse failed.\n\nprocess file: {run_config.emx.emx_process_file}\nerror: {exc}"

        selected_layers = [
            ("Primary coil layer", run_config.emx.primary_coil_layer),
            ("M5 layer", run_config.emx.m5_layer),
            ("Primary VDD route metal", None if run_config.bounds.primary.vdd_bar is None else run_config.bounds.primary.vdd_bar.route_layer),
            ("Primary VDD route via", None if run_config.bounds.primary.vdd_bar is None else run_config.bounds.primary.vdd_bar.route_via_layer),
            ("Primary VDD bar layer", None if run_config.bounds.primary.vdd_bar is None else run_config.bounds.primary.vdd_bar.bar_layer),
            ("Primary VDD bar via", None if run_config.bounds.primary.vdd_bar is None else run_config.bounds.primary.vdd_bar.bar_via_layer),
            ("Primary bridge route metal 1", run_config.emx.primary_bridge_layer),
            ("Primary bridge route via 1", run_config.emx.primary_bridge_via_layer),
            ("Primary bridge route metal 2", run_config.emx.primary_bridge_lower_layer),
            ("Primary bridge route via 2", run_config.emx.primary_bridge_lower_via_layer),
            ("Secondary coil layer", run_config.emx.secondary_coil_layer),
            ("Secondary VDD route metal", None if run_config.bounds.secondary.vdd_bar is None else run_config.bounds.secondary.vdd_bar.route_layer),
            ("Secondary VDD route via", None if run_config.bounds.secondary.vdd_bar is None else run_config.bounds.secondary.vdd_bar.route_via_layer),
            ("Secondary VDD bar layer", None if run_config.bounds.secondary.vdd_bar is None else run_config.bounds.secondary.vdd_bar.bar_layer),
            ("Secondary VDD bar via", None if run_config.bounds.secondary.vdd_bar is None else run_config.bounds.secondary.vdd_bar.bar_via_layer),
            ("Secondary bridge route metal 1", run_config.emx.secondary_bridge_layer),
            ("Secondary bridge route via 1", run_config.emx.secondary_bridge_via_layer),
            ("Secondary bridge route metal 2", run_config.emx.secondary_bridge_lower_layer),
            ("Secondary bridge route via 2", run_config.emx.secondary_bridge_lower_via_layer),
        ]
        lines = [f"process file: {proc_info.path}", "", "selected layer mapping:"]
        for label, layer in selected_layers:
            if layer is None:
                lines.append(f"- {label}: none")
            else:
                layer_id = int(layer)
                lines.append(
                    f"- {label}: {proc_info.display_label_for_gds_layer(layer_id)} -> {proc_info.summary_for_gds_layer(layer_id)}"
                )

        lines.extend(["", "conductors:"])
        for conductor in proc_info.conductors:
            gds_layers = ", ".join(str(layer) for layer in conductor.gds_layers) or "n/a"
            lines.append(f"- {conductor.name}: thickness={conductor.thickness_um:.3f} um, gds_layers=[{gds_layers}]")

        lines.extend(["", "via and logical layer definitions:"])
        for definition in proc_info.layer_definitions:
            if definition.category == "metal":
                continue
            gds_layers = ", ".join(str(layer) for layer in definition.gds_layers) or "n/a"
            lines.append(f"- {definition.name}: category={definition.category}, gds_layers=[{gds_layers}]")

        lines.extend(["", "first dielectric slabs:"])
        for dielectric in proc_info.dielectrics[:12]:
            lines.append(f"- {dielectric.name}: thickness={dielectric.thickness_um:.3f} um, er={dielectric.epsilon_r:.3f}")
        return "\n".join(lines)

    @staticmethod
    def _clip_value(value: float, bounds: tuple[float, float]) -> float:
        lo, hi = float(bounds[0]), float(bounds[1])
        return min(max(float(value), lo), hi)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RFIC Transformer Toolkit GUI")
    parser.add_argument(
        "--config",
        help="Optional transformer YAML config to load on startup. Topology must match --topology.",
    )
    parser.add_argument(
        "--topology",
        choices=("1t1t", "1t2t", "2t1t", "2t2t"),
        default="2t2t",
        help="Transformer turn configuration to visualize. Default: 2t2t",
    )
    parser.add_argument(
        "--qt-platform",
        help="Optional Qt platform plugin override, for example 'offscreen' or 'minimal'.",
    )
    parser.add_argument(
        "--demo-optimization-view",
        action="store_true",
        help="Seed the Optimization viewer with synthetic results for screenshots.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    qt_platform = getattr(args, "qt_platform", None)
    if qt_platform:
        os.environ["QT_QPA_PLATFORM"] = str(qt_platform)
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        app.setStyle("Fusion")
    window = TransformerConstraintQtGui(
        topology_mode=args.topology,
        config_path=args.config,
        demo_optimization_view=bool(getattr(args, "demo_optimization_view", False)),
    )
    window.show()
    if owns_app:
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
