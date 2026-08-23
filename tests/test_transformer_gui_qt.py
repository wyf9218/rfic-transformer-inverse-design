import os
from dataclasses import replace
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
import yaml


_QT_BINDING_AVAILABLE = any(
    importlib.util.find_spec(name) is not None
    for name in ("PySide6", "PyQt6", "PyQt5", "PySide2")
)
pytestmark = pytest.mark.skipif(
    not _QT_BINDING_AVAILABLE,
    reason="Qt GUI tests require optional gui extra: install PySide6/PyQt6/PyQt5/PySide2",
)


def test_interfaces_gui_main_routes_to_qt() -> None:
    from rfic_transformer_inverse_design.interfaces import gui_main, gui_qt_main

    assert gui_main is not None
    assert gui_qt_main is not None


def test_qt_gui_script_entrypoint_bootstraps_package_imports() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "rfic_transformer_inverse_design" / "interfaces" / "gui_qt.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RFIC Transformer Toolkit GUI" in result.stdout


def test_qt_gui_main_uses_sys_argv_when_creating_app() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces import gui_qt

    app_instance = mock.Mock()
    app_instance.exec.return_value = 0
    window_instance = mock.Mock()
    qapplication = mock.Mock()
    qapplication.instance.return_value = None
    qapplication.return_value = app_instance

    with (
        mock.patch.object(
            gui_qt,
            "_parse_args",
            return_value=SimpleNamespace(topology="2t2t", config=None, demo_optimization_view=False),
        ),
        mock.patch.object(gui_qt.QtWidgets, "QApplication", qapplication),
        mock.patch.object(gui_qt, "TransformerConstraintQtGui", return_value=window_instance) as window_ctor,
        mock.patch.object(gui_qt.sys, "argv", ["gui_qt.py"]),
        mock.patch.object(gui_qt.sys, "exit", side_effect=SystemExit) as exit_mock,
    ):
        with pytest.raises(SystemExit):
            gui_qt.main()

    qapplication.instance.assert_called_once_with()
    qapplication.assert_called_once_with(["gui_qt.py"])
    window_ctor.assert_called_once_with(topology_mode="2t2t", config_path=None, demo_optimization_view=False)
    window_instance.show.assert_called_once_with()
    exit_mock.assert_called_once_with(0)


def test_qt_gui_main_applies_qt_platform_override_before_app_creation() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces import gui_qt

    app_instance = mock.Mock()
    app_instance.exec.return_value = 0
    window_instance = mock.Mock()
    qapplication = mock.Mock()
    qapplication.instance.return_value = None
    qapplication.return_value = app_instance

    with (
        mock.patch.object(
            gui_qt,
            "_parse_args",
            return_value=SimpleNamespace(
                topology="2t2t",
                config=None,
                qt_platform="offscreen",
            ),
        ),
        mock.patch.object(gui_qt.QtWidgets, "QApplication", qapplication),
        mock.patch.object(gui_qt, "TransformerConstraintQtGui", return_value=window_instance),
        mock.patch.object(gui_qt.sys, "argv", ["gui_qt.py"]),
        mock.patch.object(gui_qt.sys, "exit", side_effect=SystemExit),
        mock.patch.dict(gui_qt.os.environ, {"QT_QPA_PLATFORM": "minimal"}, clear=False),
    ):
        assert gui_qt.os.environ["QT_QPA_PLATFORM"] == "minimal"
        with pytest.raises(SystemExit):
            gui_qt.main()
        assert gui_qt.os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_qt_gui_smoke() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    assert window.top_tabs.count() == 3
    assert window.top_tabs.tabText(0) == "Preview"
    assert window.top_tabs.tabText(1) == "3D"
    assert window.top_tabs.tabText(2) == "Optimization"
    assert not window.top_tabs.tabBar().isHidden()
    assert window.preview_group.title() == "Current Design Preview"
    assert window.preview_dimensions_checkbox is not None
    assert window.preview_dimensions_checkbox.isChecked()
    assert window.viewer3d_group.title() == "3D Stackup Viewer"
    assert window.viewer3d_show_dielectrics_checkbox is not None
    assert window.viewer3d_show_dielectrics_checkbox.isChecked()
    assert window.viewer3d_show_dimensions_checkbox is not None
    assert window.viewer3d_show_dimensions_checkbox.isChecked()
    assert window.viewer3d_mode_2d_button is not None
    assert window.viewer3d_mode_3d_button is not None
    assert not window.viewer3d_mode_2d_button.isChecked()
    assert window.viewer3d_mode_3d_button.isChecked()
    assert window.viewer3d_save_button is not None
    assert window.viewer3d_save_button.text() == "Save View..."
    assert window.viewer3d is None
    assert window.viewer3d_placeholder is not None
    assert (
        "pyvistaqt" in window.viewer3d_placeholder.text().lower()
        or "qt_qpa_platform=offscreen" in window.viewer3d_placeholder.text().lower()
    )
    assert not window.run_optimization_button.isHidden()
    assert not window.pause_optimization_button.isHidden()
    assert not window.resume_optimization_button.isHidden()
    assert not window.stop_optimization_button.isHidden()
    assert not window.export_best_sparams_button.isHidden()
    assert window.run_optimization_button.isEnabled()
    assert not window.pause_optimization_button.isEnabled()
    assert not window.resume_optimization_button.isEnabled()
    assert not window.stop_optimization_button.isEnabled()
    assert window.optimization_preview_mode_combo is not None
    assert window.optimization_preview_mode_combo.currentData() == "best"
    assert window.optimization_eval_table is not None
    assert window.optimization_eval_table.rowCount() == 0
    assert window.optimization_blocking_text is not None
    assert window.config_recent_tree is not None
    assert window.combo_boxes["target.q_target_mode"].currentText() == "max"
    assert not window.line_edits["target.q_primary_target"].isEnabled()
    assert not window.line_edits["target.q_secondary_target"].isEnabled()
    window.close()


def test_qt_gui_does_not_expose_bridge_margin_ratio() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    payload = window._current_config_payload()

    assert "bounds.primary_bridge_section_containment_margin_ratio" not in window.line_edits
    assert "bounds.secondary_bridge_section_containment_margin_ratio" not in window.line_edits
    assert "primary_bridge_section_containment_margin_ratio" not in payload["topology"]
    assert "secondary_bridge_section_containment_margin_ratio" not in payload["topology"]
    assert "primary_bridge_section_containment_margin_ratio" not in payload["bounds"]
    assert "secondary_bridge_section_containment_margin_ratio" not in payload["bounds"]
    window.close()


def test_qt_gui_places_inductor_layers_on_primary_and_secondary_tabs() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    layer_groups = [group for group in window.findChildren(QtWidgets.QGroupBox) if group.title() == "Inductor Layers"]
    assert len(layer_groups) == 2
    assert "ap_layer" in window.stackup_combos
    assert "primary_bridge_layer" in window.stackup_combos
    assert "m9_layer" in window.stackup_combos
    assert "secondary_bridge_layer" in window.stackup_combos
    window.close()


def test_invalid_preview_updates_3d_status() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    window._draw_invalid_preview(["mock violation"])

    assert window.viewer3d_status_label is not None
    assert "geometry is invalid" in window.viewer3d_status_label.text().lower()
    window.close()


def test_polydata_from_points_handles_concave_polygon() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    concave_points = np.array(
        [
            [0.0, 0.0],
            [8.0, 0.0],
            [8.0, 2.0],
            [4.0, 2.0],
            [4.0, 6.0],
            [8.0, 6.0],
            [8.0, 8.0],
            [0.0, 8.0],
        ],
        dtype=float,
    )
    mesh = window._polydata_from_points(concave_points, z_bottom_um=12.0, thickness_um=3.5)

    assert mesh is not None
    surface = mesh.extract_surface(algorithm="dataset_surface")
    assert surface.n_cells > 0
    assert surface.is_all_triangles
    bounds = mesh.bounds
    assert bounds.z_min == pytest.approx(12.0)
    assert bounds.z_max == pytest.approx(15.5)
    window.close()


def test_polydata_from_points_preserves_hollow_ring_polygon() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets
    import gdstk

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    outer = gdstk.rectangle((0.0, 0.0), (10.0, 10.0), layer=1, datatype=0)
    inner = gdstk.rectangle((3.0, 3.0), (7.0, 7.0), layer=1, datatype=0)
    ring = gdstk.boolean([outer], [inner], "not", layer=1, datatype=0)[0]

    mesh = window._polydata_from_points(ring, z_bottom_um=4.0, thickness_um=2.5)

    assert mesh is not None
    assert mesh.translate((0.0, 0.0, -4.0), inplace=False).volume == pytest.approx(84.0 * 2.5)
    bounds = mesh.bounds
    assert bounds.z_min == pytest.approx(4.0)
    assert bounds.z_max == pytest.approx(6.5)
    window.close()


def test_polydata_from_points_preserves_feed_connected_coil_polygon_area() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.api import default_run_config
    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets
    from rfic_transformer_inverse_design.layout.builders import InductorLayoutSpec, _build_center_tapped_inductor_geometry

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    cfg = default_run_config("2t2t")
    geometry = cfg.bounds.midpoint()
    inductor = geometry.secondary
    bundle = _build_center_tapped_inductor_geometry(
        InductorLayoutSpec(
            geometry=inductor,
            center_x_um=geometry.offset_um,
            center_y_um=0.0,
            bridge_offset_y_um=0.0,
            bridge_anchor_gap_cap_um=None,
            metal_layer=cfg.emx.m9_layer,
            bridge_layer=int(inductor.bridge_layer),
            bridge_via_layer=inductor.bridge_via_layer,
            bridge_lower_layer=inductor.bridge_lower_layer,
            bridge_lower_via_layer=inductor.bridge_lower_via_layer,
            mirror_x=True,
        ),
        include_center_tap_feed=inductor.center_tap,
    )
    feed_connected_polygon = max(bundle.coil_polygons, key=lambda poly: float(poly.area()))

    mesh = window._polydata_from_points(feed_connected_polygon, z_bottom_um=8.0, thickness_um=1.0)

    assert mesh is not None
    assert mesh.translate((0.0, 0.0, -8.0), inplace=False).volume == pytest.approx(
        float(feed_connected_polygon.area()),
        rel=1.0e-6,
        abs=1.0e-3,
    )
    window.close()


def test_3d_actor_selection_toggle_updates_opacity() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    class _FakeProperty:
        def __init__(self, opacity: float) -> None:
            self._opacity = float(opacity)

        def SetOpacity(self, value: float) -> None:
            self._opacity = float(value)

        def GetOpacity(self) -> float:
            return self._opacity

    class _FakeActor:
        def __init__(self, addr: str, opacity: float) -> None:
            self._addr = addr
            self._property = _FakeProperty(opacity)
            self._visibility = 1

        def GetAddressAsString(self, _label: str) -> str:
            return self._addr

        def GetProperty(self) -> _FakeProperty:
            return self._property

        def SetVisibility(self, value: int) -> None:
            self._visibility = int(value)

        def GetVisibility(self) -> int:
            return self._visibility

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d = mock.Mock()

    actor_a = _FakeActor("Addr=A", 0.22)
    actor_b = _FakeActor("Addr=B", 0.94)
    actor_d_low = _FakeActor("Addr=D0", 0.22)
    actor_d_high = _FakeActor("Addr=D1", 0.22)
    window._register_3d_actor(
        actor_d_low,
        opacity=0.22,
        label="oxide_low",
        kind="Dielectric",
        selectable=False,
        z_min=0.0,
        z_max=5.0,
    )
    window._register_3d_actor(
        actor_d_high,
        opacity=0.22,
        label="oxide_high",
        kind="Dielectric",
        selectable=False,
        z_min=20.0,
        z_max=25.0,
    )
    window._register_3d_actor(actor_b, opacity=0.94, label="M10", kind="Metal", z_min=10.0, z_max=12.0)

    window._toggle_3d_actor_selection(actor_b)
    assert actor_b.GetProperty().GetOpacity() == pytest.approx(1.0)
    assert actor_d_low.GetProperty().GetOpacity() == pytest.approx(0.22)
    assert actor_d_high.GetProperty().GetOpacity() == pytest.approx(min(0.22 * 0.18, 0.05))
    assert window.viewer3d_selected_actor_id == "Addr=B"

    window._register_3d_actor(actor_a, opacity=0.22, label="M9", kind="Metal", z_min=2.0, z_max=4.0)
    window._toggle_3d_actor_selection(actor_a)
    assert actor_b.GetProperty().GetOpacity() == pytest.approx(0.94)
    assert actor_d_low.GetProperty().GetOpacity() == pytest.approx(0.22)
    assert actor_d_high.GetProperty().GetOpacity() == pytest.approx(min(0.22 * 0.18, 0.05))
    assert actor_a.GetProperty().GetOpacity() == pytest.approx(1.0)
    assert window.viewer3d_selected_actor_id == "Addr=A"

    window._toggle_3d_actor_selection(actor_a)
    assert actor_a.GetProperty().GetOpacity() == pytest.approx(0.22)
    assert actor_d_low.GetProperty().GetOpacity() == pytest.approx(0.22)
    assert actor_d_high.GetProperty().GetOpacity() == pytest.approx(0.22)
    assert window.viewer3d_selected_actor_id is None
    assert window.viewer3d.render.call_count == 3
    window.close()


def test_3d_dielectric_visibility_toggle_updates_actors() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    class _FakeProperty:
        def __init__(self, opacity: float) -> None:
            self._opacity = float(opacity)

        def SetOpacity(self, value: float) -> None:
            self._opacity = float(value)

        def GetOpacity(self) -> float:
            return self._opacity

    class _FakeActor:
        def __init__(self, addr: str, opacity: float) -> None:
            self._addr = addr
            self._property = _FakeProperty(opacity)
            self._visibility = 1

        def GetAddressAsString(self, _label: str) -> str:
            return self._addr

        def GetProperty(self) -> _FakeProperty:
            return self._property

        def SetVisibility(self, value: int) -> None:
            self._visibility = int(value)

        def GetVisibility(self) -> int:
            return self._visibility

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d = mock.Mock()

    dielectric = _FakeActor("Addr=D", 0.22)
    metal = _FakeActor("Addr=M", 0.94)
    window._register_3d_actor(dielectric, opacity=0.22, label="oxide", kind="Dielectric", selectable=False)
    window._register_3d_actor(metal, opacity=0.94, label="M10", kind="Metal")

    window._on_3d_show_dielectrics_toggled(False)
    assert dielectric.GetVisibility() == 0
    assert metal.GetVisibility() == 1
    assert window.viewer3d_show_dielectrics is False

    window._on_3d_show_dielectrics_toggled(True)
    assert dielectric.GetVisibility() == 1
    assert metal.GetVisibility() == 1
    assert window.viewer3d_show_dielectrics is True
    assert window.viewer3d.render.call_count == 2
    window.close()


def test_3d_dimensions_toggle_rerenders_current_layout() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.top_tabs.setCurrentWidget(window.viewer3d_group)
    window.current_preview_layout = object()
    window.viewer3d_last_source = ("gds", "mtime", "proc")

    with mock.patch.object(window, "_set_3d_view_from_layout") as render_mock:
        window._on_3d_show_dimensions_toggled(False)

    assert window.viewer3d_show_dimensions is False
    assert window.viewer3d_last_source is None
    render_mock.assert_called_once_with(window.current_preview_layout)
    window.close()


def test_3d_view_mode_toggle_applies_camera_mode() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    with mock.patch.object(window, "_apply_3d_viewer_camera_mode") as apply_mock:
        window._on_3d_view_mode_toggled("2d", True)

    assert window.viewer3d_view_mode == "2d"
    apply_mock.assert_called_once_with(reset_camera=True, render=True)
    window.close()


def test_apply_3d_viewer_camera_mode_switches_between_overhead_and_interactive() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d = mock.Mock()

    window.viewer3d_view_mode = "2d"
    window._apply_3d_viewer_camera_mode(reset_camera=True, render=False)
    window.viewer3d.view_xy.assert_called_once_with()
    window.viewer3d.enable_parallel_projection.assert_called_once_with()
    window.viewer3d.enable_2d_style.assert_called_once_with()
    window.viewer3d.view_isometric.assert_not_called()
    window.viewer3d.reset_camera.assert_called_once_with()

    window.viewer3d.reset_mock()
    window.viewer3d_view_mode = "3d"
    window._apply_3d_viewer_camera_mode(reset_camera=True, render=False)
    window.viewer3d.view_isometric.assert_called_once_with()
    window.viewer3d.disable_parallel_projection.assert_called_once_with()
    window.viewer3d.enable_trackball_style.assert_called_once_with()
    window.viewer3d.view_xy.assert_not_called()
    window.viewer3d.reset_camera.assert_called_once_with()
    window.close()


def test_3d_dimension_overlay_renders_all_tunable_labels() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces import gui_qt
    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    class _FakeActor:
        def __init__(self) -> None:
            self.pickable_off_calls = 0

        def PickableOff(self) -> None:
            self.pickable_off_calls += 1

    fake_pv = SimpleNamespace(
        Line=lambda start, end: ("line", tuple(float(value) for value in start), tuple(float(value) for value in end)),
        merge=lambda meshes: {"segments": list(meshes)},
    )

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d = mock.Mock()
    window.viewer3d.add_mesh.side_effect = lambda *args, **kwargs: _FakeActor()
    window.viewer3d.add_point_labels = mock.Mock()

    geometry = window.bounds.midpoint()
    specs = window._build_3d_dimension_specs(geometry=geometry, bounds_xy=(-220.0, 220.0, -200.0, 200.0))

    with mock.patch.object(gui_qt, "pv", fake_pv):
        window._render_3d_dimension_overlays(
            geometry=geometry,
            bounds_xy=(-220.0, 220.0, -200.0, 200.0),
            anchor_z=48.0,
            visual_height=48.0,
        )

    assert len(specs) >= 11
    assert window.viewer3d.add_mesh.call_count == len(specs)
    assert window.viewer3d.add_point_labels.call_count == len(specs)
    rendered_labels = {call.args[1][0] for call in window.viewer3d.add_point_labels.call_args_list}
    assert "Primary\nouter width" in rendered_labels
    assert "Secondary\nouter width" in rendered_labels
    assert "Primary\nline width" in rendered_labels
    assert "Secondary\nturn spacing" in rendered_labels
    assert "Primary-secondary\noffset" in rendered_labels
    window.close()


def test_configure_3d_viewer_rendering_disables_shadows() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d = mock.Mock()

    window._configure_3d_viewer_rendering()

    window.viewer3d.set_background.assert_called_once_with("#f6f2ec")
    window.viewer3d.enable_depth_peeling.assert_called_once_with(number_of_peels=8, occlusion_ratio=0.0)
    window.viewer3d.enable_lightkit.assert_called_once_with(only_active=False)
    window.viewer3d.disable_shadows.assert_called_once_with()
    window.viewer3d.view_isometric.assert_called_once_with()
    window.viewer3d.disable_parallel_projection.assert_called_once_with()
    window.viewer3d.set_scale.assert_called_once_with(xscale=1.0, yscale=1.0, zscale=0.72, reset_camera=False)
    window.viewer3d.reset_camera.assert_not_called()
    window.close()


def test_save_3d_view_prefers_viewer_screenshot(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d = mock.Mock()

    target_path = tmp_path / "stackup.png"
    with mock.patch.object(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        return_value=(str(target_path), "PNG files (*.png)"),
    ):
        window._save_3d_view()

    window.viewer3d.screenshot.assert_called_once_with(str(target_path))
    assert "saved 3d viewer image" in window.viewer3d_status_label.text().lower()
    window.close()


def test_qt_gui_q_target_mode_updates_enabled_state_and_payload() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    window._set_combo_text("target.q_target_mode", "target")
    window._on_q_target_mode_changed()
    window.line_edits["target.q_primary_target"].setText("18.5")
    window.line_edits["target.q_secondary_target"].setText("16.25")

    payload = window._current_config_payload()

    assert window.line_edits["target.q_primary_target"].isEnabled()
    assert window.line_edits["target.q_secondary_target"].isEnabled()
    assert payload["target"]["q_target_mode"] == "target"
    assert payload["target"]["q_primary_target"] == 18.5
    assert payload["target"]["q_secondary_target"] == 16.25

    window._set_combo_text("target.q_target_mode", "max")
    window._on_q_target_mode_changed()
    payload = window._current_config_payload()

    assert not window.line_edits["target.q_primary_target"].isEnabled()
    assert not window.line_edits["target.q_secondary_target"].isEnabled()
    assert payload["target"]["q_target_mode"] == "max"
    assert payload["target"]["q_primary_target"] is None
    assert payload["target"]["q_secondary_target"] is None
    window.close()


def test_qt_gui_vdd_bar_offset_round_trips_in_payload() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="1t1t")
    window.refresh_timer.stop()

    window._set_check("topology.primary_center_tap", True)
    window._set_check("topology.primary_vdd_bar_enabled", True)
    window._set_line_text("topology.primary_vdd_bar_offset_um", 7.25)

    payload = window._current_config_payload()

    assert "topology.primary_vdd_bar_offset_um" in window.line_edits
    assert payload["topology"]["primary"]["vdd_bar"]["offset_um"] == 7.25
    window.close()


def test_qt_gui_optimizer_checkpoint_and_warm_start_round_trip() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="1t1t")
    window.refresh_timer.stop()

    window._set_line_text("optimizer.warm_start_paths", "C:/tmp/one/summary.json, C:/tmp/two/optimization_summary.json")
    window._set_check("optimizer.resume_from_checkpoint", True)
    window._set_line_text("optimizer.checkpoint_interval_evaluations", 9)

    payload = window._current_config_payload()

    assert payload["optimizer"]["warm_start_paths"] == (
        "C:/tmp/one/summary.json",
        "C:/tmp/two/optimization_summary.json",
    )
    assert payload["optimizer"]["resume_from_checkpoint"] is True
    assert payload["optimizer"]["checkpoint_interval_evaluations"] == 9
    window.close()


def test_qt_gui_remote_ssh_emx_fields_round_trip() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="1t1t")
    window.refresh_timer.stop()

    window._set_combo_text("emx.execution_mode", "remote_ssh")
    window._set_line_text("emx.remote_ssh_host", "zeus")
    window._set_line_text("emx.remote_ssh_command", "wsl -d Ubuntu-22.04 -e ssh")
    window._set_line_text("emx.remote_scp_command", "wsl -d Ubuntu-22.04 -e scp")
    window._set_line_text("emx.remote_repo_root", "/srv/rfic_transformer_inverse_design-git")
    window._set_line_text("emx.remote_work_root", "/srv/rfic_transformer_inverse_design-runs")
    window._set_line_text("emx.remote_python", "python3")
    window._set_line_text("emx.remote_venv_activate", "/srv/rfic_transformer_inverse_design-git/.venv/bin/activate")
    window._set_line_text("emx.remote_emx_process_file", "/disk/pdk/typical.proc")

    payload = window._current_config_payload()

    assert payload["emx"]["execution_mode"] == "remote_ssh"
    assert payload["emx"]["remote_ssh_host"] == "zeus"
    assert payload["emx"]["remote_ssh_command"] == "wsl -d Ubuntu-22.04 -e ssh"
    assert payload["emx"]["remote_scp_command"] == "wsl -d Ubuntu-22.04 -e scp"
    assert payload["emx"]["remote_repo_root"] == "/srv/rfic_transformer_inverse_design-git"
    assert payload["emx"]["remote_work_root"] == "/srv/rfic_transformer_inverse_design-runs"
    assert payload["emx"]["remote_python"] == "python3"
    assert payload["emx"]["remote_venv_activate"] == "/srv/rfic_transformer_inverse_design-git/.venv/bin/activate"
    assert payload["emx"]["remote_emx_process_file"] == "/disk/pdk/typical.proc"
    window.close()


def test_qt_gui_uses_loaded_config_topology_at_startup(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    base = TransformerConstraintQtGui(topology_mode="1t1t")
    base.refresh_timer.stop()
    payload = base._current_config_payload()
    config_path = tmp_path / "startup_1t1t.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    base.close()

    window = TransformerConstraintQtGui(topology_mode="2t2t", config_path=str(config_path))
    window.refresh_timer.stop()
    assert window.topology_mode == "1t1t"
    assert window.preview_dir.name == "gui_qt"
    assert window.windowTitle() == "RFIC Transformer Toolkit GUI (Qt, 1t1t)"
    window.close()


def test_validation_summary_is_brief_on_pass() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    summary = window._format_validation(
        SimpleNamespace(
            metrics={
                "elapsed_ms": 1.6,
                "primary_conductive_components": 1,
                "checker_primary_coil_layer": 74,
                "checker_secondary_coil_layer": 39,
                "checker_primary_bridge_route_layers": "via1=85, metal1=39, via2=58, metal2=38",
                "checker_secondary_bridge_route_layers": "via1=58, metal1=38, via2=None, metal2=None",
            },
            errors=[],
            warnings=[],
        )
    )

    assert "Geometry validation checks:\n- geometry checks: pass" in summary
    assert "active layers:" in summary
    assert "preview note: flattened preview overlays all layers; cross-layer crossings are allowed" in summary
    assert "primary coil=74" in summary
    assert "secondary coil=39" in summary
    window.close()


def test_validation_summary_includes_detail_on_failure() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    summary = window._format_validation(
        SimpleNamespace(
            metrics={
                "elapsed_ms": 1.6,
                "primary_conductive_components": 1,
                "primary_via_checked": 4,
                "primary_via_recommended_enclosure_warnings": 1,
                "checker_primary_coil_layer": 74,
            },
            errors=["mock violation"],
            warnings=["mock warning"],
        )
    )

    assert "- elapsed_ms: 1.60" in summary
    assert "- warning_count: 1" in summary
    assert "- primary_conductive_components: 1" in summary
    assert "- primary_via_checked: 4" in summary
    assert "Checker layer debug:" in summary
    assert "- checker_primary_coil_layer: 74" in summary
    assert "Geometry warnings:" in summary
    assert "- mock warning" in summary
    assert "Geometry violations:" in summary
    assert "- mock violation" in summary
    window.close()


def test_save_config_writes_current_yaml(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    save_path = tmp_path / "saved_config.yaml"

    with mock.patch.object(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        return_value=(str(save_path), "YAML files (*.yaml *.yml)"),
    ):
        window._save_config()

    assert save_path.exists()
    saved_text = save_path.read_text(encoding="utf-8")
    assert "optimizer:" in saved_text
    assert "name: cma_es" in saved_text
    assert "target:" in saved_text
    window.close()


def test_load_config_updates_current_yaml_and_controls(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    payload = window._current_config_payload()
    payload["target"]["f0_hz"] = 17.5e9
    payload["target"]["q_target_mode"] = "target"
    payload["target"]["q_primary_target"] = 18.5
    payload["target"]["q_secondary_target"] = 16.25
    payload["optimizer"]["name"] = "turbo"
    load_path = tmp_path / "loaded_config.yaml"
    load_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    window.generated_config_path = tmp_path / "generated.yaml"

    with (
        mock.patch.object(
            QtWidgets.QFileDialog,
            "getOpenFileName",
            return_value=(str(load_path), "YAML files (*.yaml *.yml)"),
        ),
        mock.patch.object(window, "_schedule_refresh") as schedule_refresh_mock,
    ):
        window._load_config()

    assert window.line_edits["target.f0_hz"].text() == "17500000000.0"
    assert window.combo_boxes["target.q_target_mode"].currentText() == "target"
    assert window.line_edits["target.q_primary_target"].text() == "18.5"
    assert window.line_edits["target.q_secondary_target"].text() == "16.25"
    assert window.combo_boxes["optimizer.name"].currentText() == "turbo"
    assert window.config_path == load_path.resolve()
    assert window.generated_config_path.exists()
    assert "Loaded config from" in window.status_label.text()
    assert schedule_refresh_mock.call_count >= 1
    window.close()


def test_load_config_switches_gui_topology_to_match_yaml(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = TransformerConstraintQtGui(topology_mode="1t1t")
    source.refresh_timer.stop()
    payload = source._current_config_payload()
    load_path = tmp_path / "loaded_1t1t.yaml"
    load_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    source.close()

    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    with (
        mock.patch.object(
            QtWidgets.QFileDialog,
            "getOpenFileName",
            return_value=(str(load_path), "YAML files (*.yaml *.yml)"),
        ),
        mock.patch.object(window, "_schedule_refresh"),
    ):
        window._load_config()

    assert window.topology_mode == "1t1t"
    assert window.preview_dir.name == "gui_qt"
    assert window.windowTitle() == "RFIC Transformer Toolkit GUI (Qt, 1t1t)"
    assert window.config_path == load_path.resolve()
    assert "Loaded config from" in window.status_label.text()
    window.close()


def test_export_best_sparams_copies_best_touchstone(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    source_path = tmp_path / "best.s4p"
    source_text = "! synthetic touchstone\n# GHz S RI R 50\n"
    source_path.write_text(source_text, encoding="utf-8")
    export_path = tmp_path / "copied_best.s4p"
    window.optimization_best_result = SimpleNamespace(
        touchstone_path=source_path,
        single_ended_sparams=None,
        differential_sparams=None,
    )

    with mock.patch.object(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        return_value=(str(export_path), "Touchstone files (*.s2p *.s4p *.s6p *.s8p)"),
    ):
        window._export_best_sparams()

    assert export_path.exists()
    assert export_path.read_text(encoding="utf-8") == source_text
    assert "Exported best S-parameters to" in window.status_label.text()
    window.close()


def test_start_optimization_blocks_on_validation_errors() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    fake_cfg = window.run_config
    fake_geom = window.bounds.midpoint()
    fake_gdstk = SimpleNamespace(errors=["mock gdstk violation"], warnings=[], metrics={})

    with mock.patch.object(
        window,
        "_build_live_context",
        return_value=(fake_cfg, fake_geom, fake_gdstk, ["mock geometry violation"], "snapshot-yaml"),
    ):
        window._start_optimization()

    assert window.optimization_thread is None
    assert "Optimization start blocked." in window.optimization_status_label.text()
    assert "mock geometry violation" in window.optimization_blocking_text.toPlainText()
    assert "mock gdstk violation" in window.optimization_blocking_text.toPlainText()
    window.close()


def test_start_optimization_captures_snapshot_and_tracks_editor_dirty_state(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces import gui_qt
    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    fake_cfg = window.run_config
    fake_geom = window.bounds.midpoint()
    fake_gdstk = SimpleNamespace(errors=[], warnings=[], metrics={})
    fake_worker = mock.Mock()
    fake_worker.isRunning.return_value = True
    fake_worker.is_pause_requested.return_value = False
    for signal_name in ("progress", "completed", "failed", "finished"):
        setattr(fake_worker, signal_name, mock.Mock(connect=mock.Mock()))

    with (
        mock.patch.object(
            window,
            "_build_live_context",
            return_value=(fake_cfg, fake_geom, fake_gdstk, [], "snapshot-yaml"),
        ),
        mock.patch.object(window, "_collect_optimization_start_errors", return_value=[]),
        mock.patch.object(gui_qt, "OptimizationWorkerThread", return_value=fake_worker) as worker_ctor,
    ):
        window._start_optimization()

    worker_ctor.assert_called_once()
    assert worker_ctor.call_args.kwargs["run_config"] is fake_cfg
    assert window.optimization_launch_snapshot_yaml == "snapshot-yaml"
    assert "snapshot captured" in window.optimization_snapshot_value_label.text().lower()
    assert "matches the active run snapshot" in window.optimization_editor_state_value_label.text().lower()
    assert not window.run_optimization_button.isEnabled()
    assert window.pause_optimization_button.isEnabled()
    assert not window.resume_optimization_button.isEnabled()
    assert window.stop_optimization_button.isEnabled()
    assert window.optimization_run_dir is not None
    assert window.optimization_run_dir.parent == window.history_runs_dir
    assert (window.optimization_run_dir / "entry.yaml").exists()
    assert (window.optimization_run_dir / "config.yaml").exists()

    window._schedule_refresh()
    assert window.optimization_editor_dirty_since_start is True
    assert "modified since run start" in window.optimization_editor_state_value_label.text().lower()
    window.close()


def test_optimization_controls_show_explicit_resume_state() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    fake_thread = mock.Mock()
    fake_thread.isRunning.return_value = True
    fake_thread.is_pause_requested.return_value = True
    window.optimization_thread = fake_thread

    window._set_optimization_controls_running(True)

    assert not window.run_optimization_button.isEnabled()
    assert not window.pause_optimization_button.isEnabled()
    assert window.resume_optimization_button.isEnabled()
    assert window.stop_optimization_button.isEnabled()
    window.close()


def test_right_panel_exposes_optimization_viewer_tab() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    tab_labels = [window.top_tabs.tabText(index) for index in range(window.top_tabs.count())]

    assert "Preview" in tab_labels
    assert "Optimization" in tab_labels
    window.close()


def test_optimization_viewer_uses_separate_plot_tabs() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    assert window.optimization_viewer_tabs is not None
    assert window.optimization_viewer_tabs.count() == 2
    assert window.optimization_viewer_tabs.tabText(0) == "Convergence"
    assert window.optimization_viewer_tabs.tabText(1) == "Frequency Metrics"
    assert window.convergence_canvas is not None
    assert window.best_metrics_canvas is not None
    window.close()


def test_demo_optimization_view_seeds_screenshot_data() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t", demo_optimization_view=True)
    window.refresh_timer.stop()

    assert len(window.optimization_eval_records) >= 4
    assert window.optimization_best_result is not None
    assert window.optimization_last_result is not None
    assert window.top_tabs.currentWidget() is window.optimization_viewer_group
    assert "Demo optimization results loaded" in window.optimization_status_label.text()
    assert "Viewing: Best so far" in window.best_metrics_context_label.text()
    window.close()


def test_optimization_frequency_plot_follows_live_and_selected_context() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces import gui_qt
    from rfic_transformer_inverse_design.interfaces.gui_qt import OptimizationEvalRecord, TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    best_result = SimpleNamespace(
        target=SimpleNamespace(differential_reference_impedance_ohm=100.0),
        cache_key="best",
    )
    latest_result = SimpleNamespace(
        target=SimpleNamespace(differential_reference_impedance_ohm=100.0),
        cache_key="latest",
    )
    selected_result = SimpleNamespace(
        target=SimpleNamespace(differential_reference_impedance_ohm=100.0),
        cache_key="selected",
    )
    curves = {
        "freqs_ghz": np.array([10.0, 12.0], dtype=float),
        "lp_h": np.array([1.0e-9, 1.1e-9], dtype=float),
        "ls_h": np.array([1.2e-9, 1.3e-9], dtype=float),
        "mutual_h": np.array([0.8e-9, 0.85e-9], dtype=float),
        "q_primary": np.array([12.0, 13.0], dtype=float),
        "q_secondary": np.array([14.0, 15.0], dtype=float),
        "k": np.array([0.71, 0.72], dtype=float),
    }

    with mock.patch.object(gui_qt, "_frequency_metric_curves", return_value=curves):
        window.optimization_best_result = best_result
        window._draw_best_metrics_plot()
        assert window.best_metrics_context_label.text() == "Viewing: Best so far"
        assert window.best_k_axis.get_title() == "Best so far Frequency Metrics"

        window.optimization_last_result = latest_result
        window.optimization_preview_mode = "latest"
        window._draw_best_metrics_plot()
        assert window.best_metrics_context_label.text() == "Viewing: Latest evaluated"
        assert window.best_k_axis.get_title() == "Latest evaluated Frequency Metrics"

        window.optimization_eval_records = [
            OptimizationEvalRecord(
                evaluation_count=3,
                unique_evaluation_count=3,
                backend_name="cma_es",
                elapsed_seconds=2.0,
                is_best=False,
                cost=1.5,
                result=selected_result,
            )
        ]
        window.optimization_manual_selected_eval = 3
        window._draw_best_metrics_plot()
        assert window.best_metrics_context_label.text() == "Viewing: Evaluation 3"
        assert window.best_k_axis.get_title() == "Evaluation 3 Frequency Metrics"

    window.close()


def test_refresh_now_triggers_immediate_refresh() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    with (
        mock.patch.object(window.refresh_timer, "stop") as stop_mock,
        mock.patch.object(window, "_refresh") as refresh_mock,
    ):
        window._refresh_now()

    stop_mock.assert_called_once_with()
    refresh_mock.assert_called_once_with()
    window.close()


def test_config_hub_saves_recent_configs_and_archives_bundle(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    save_path = tmp_path / "saved_config.yaml"
    window._save_config_to_path(save_path)

    assert window.config_path == save_path.resolve()
    assert window.recent_config_paths[0] == save_path.resolve()
    assert window.config_recent_tree is not None
    history_paths = []
    for index in range(window.config_recent_tree.topLevelItemCount()):
        section_item = window.config_recent_tree.topLevelItem(index)
        for group_index in range(section_item.childCount()):
            group_item = section_item.child(group_index)
            for child_index in range(group_item.childCount()):
                history_paths.append(group_item.child(child_index).text(3))
    assert any(str(save_path.resolve()) in value for value in history_paths)
    bundle_dirs = list(window.history_configs_dir.glob("*/entry.yaml"))
    assert bundle_dirs
    window.close()


def test_optimization_preview_mode_switches_between_best_and_latest() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    best_result = SimpleNamespace(
        cache_key="best",
        work_dir=Path("best"),
        touchstone_path=None,
        objective=SimpleNamespace(total_cost=1.0),
        metrics=None,
        error=None,
        geometry=window.bounds.midpoint(),
        layout=None,
    )
    latest_result = SimpleNamespace(
        cache_key="latest",
        work_dir=Path("latest"),
        touchstone_path=None,
        objective=SimpleNamespace(total_cost=2.0),
        metrics=None,
        error=None,
        geometry=window.bounds.midpoint(),
        layout=None,
    )

    with mock.patch.object(window, "_set_preview_from_result") as preview_mock:
        window._handle_optimization_progress(
            {
                "evaluation_count": 1,
                "unique_evaluation_count": 1,
                "backend_name": "cma_es",
                "elapsed_seconds": 1.2,
                "is_best": True,
                "cost": 1.0,
                "result": best_result,
            }
        )
        preview_mock.assert_called_once_with(best_result, title="Best Design Preview")
        preview_mock.reset_mock()

        window._handle_optimization_progress(
            {
                "evaluation_count": 2,
                "unique_evaluation_count": 2,
                "backend_name": "cma_es",
                "elapsed_seconds": 2.1,
                "is_best": False,
                "cost": 2.0,
                "result": latest_result,
            }
        )
        preview_mock.assert_not_called()

        window.optimization_preview_mode_combo.setCurrentIndex(1)
        preview_mock.assert_called_once_with(latest_result, title="Latest Evaluated Preview")

    assert window.optimization_eval_table.rowCount() == 2
    window.close()


def test_preview_title_tracks_current_vs_best_result(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"placeholder")
    fake_result = SimpleNamespace(
        layout=SimpleNamespace(preview_path=preview_path),
        geometry=window.bounds.midpoint(),
    )

    with mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))):
        window._set_preview_from_result(fake_result, title="Current Design Preview")
        assert window.preview_group.title() == "Current Design Preview"

        window._set_preview_from_result(fake_result, title="Best Design Preview")
        assert window.preview_group.title() == "Best Design Preview"

    window.close()


def test_preview_draws_tunable_parameter_annotations(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"placeholder")
    fake_result = SimpleNamespace(
        layout=SimpleNamespace(preview_path=preview_path, debug_preview_path=None),
        geometry=window.bounds.midpoint(),
    )

    with mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))):
        window._set_preview_from_result(fake_result, title="Current Design Preview")

    annotation_text = "\n".join(text.get_text() for text in window.axis.texts)
    expected_labels = (
        "Primary\nouter width",
        "Primary\nouter height",
        "Secondary\nouter width",
        "Secondary\nouter height",
        "Primary\nline width",
        "Secondary\nline width",
        "Primary\nturn spacing",
        "Secondary\nturn spacing",
        "Primary\nterminal y-span",
        "Secondary\nterminal y-span",
        "Primary-secondary\noffset",
        "Primary\nfeed extension",
        "Secondary\nfeed extension",
    )
    for label in expected_labels:
        assert label in annotation_text
    assert window.current_preview_extent is not None
    assert window.current_preview_display_image is not None
    window.close()


def test_preview_annotation_boxes_do_not_overlap(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"placeholder")
    fake_result = SimpleNamespace(
        layout=SimpleNamespace(preview_path=preview_path, debug_preview_path=None),
        geometry=window.bounds.midpoint(),
    )

    with mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))):
        window._set_preview_from_result(fake_result, title="Current Design Preview")

    window.canvas.draw()
    renderer = window.figure.canvas.get_renderer()
    visible_labels = [text for text in window.axis.texts if text.get_text().strip()]
    boxes = [text.get_window_extent(renderer=renderer).expanded(1.02, 1.05) for text in visible_labels]
    for idx, bbox in enumerate(boxes):
        for other in boxes[:idx]:
            overlap_x = min(bbox.x1, other.x1) - max(bbox.x0, other.x0)
            overlap_y = min(bbox.y1, other.y1) - max(bbox.y0, other.y0)
            assert overlap_x <= 1.0 or overlap_y <= 1.0
    for text in visible_labels:
        bbox_data = window._preview_text_bbox_data(text, renderer)
        assert not window._preview_bbox_overlaps_geometry(bbox_data)
    window.close()


def test_preview_terminal_span_labels_stay_near_dimension_lines(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"placeholder")
    fake_result = SimpleNamespace(
        layout=SimpleNamespace(preview_path=preview_path, debug_preview_path=None),
        geometry=window.bounds.midpoint(),
    )

    with mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))):
        window._set_preview_from_result(fake_result, title="Current Design Preview")

    assert window.current_preview_extent is not None
    left, right, _bottom, _top = map(float, window.current_preview_extent)
    span_x = float(window.current_preview_extent[1] - window.current_preview_extent[0])
    labels = {text.get_text(): text for text in window.axis.texts if text.get_text().strip()}

    primary_terminal_x = float(labels["Primary\nterminal y-span"].get_position()[0])
    secondary_terminal_x = float(labels["Secondary\nterminal y-span"].get_position()[0])
    primary_terminal_line_x = window._clamp_preview_value(left + 0.08 * span_x, left + 0.05 * span_x, right - 0.24 * span_x)
    secondary_terminal_line_x = window._clamp_preview_value(right - 0.08 * span_x, left + 0.24 * span_x, right - 0.05 * span_x)
    max_line_delta = max(0.035 * span_x, 12.0)

    assert abs(primary_terminal_x - primary_terminal_line_x) <= max_line_delta
    assert abs(secondary_terminal_x - secondary_terminal_line_x) <= max_line_delta
    window.close()


def test_preview_dimension_toggle_hides_and_restores_annotations(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"placeholder")
    fake_result = SimpleNamespace(
        layout=SimpleNamespace(preview_path=preview_path, debug_preview_path=None),
        geometry=window.bounds.midpoint(),
    )

    with mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))):
        window._set_preview_from_result(fake_result, title="Current Design Preview")

    assert any(text.get_text().strip() for text in window.axis.texts)
    window.preview_dimensions_checkbox.setChecked(False)
    assert not any(text.get_text().strip() for text in window.axis.texts)
    window.preview_dimensions_checkbox.setChecked(True)
    assert any(text.get_text().strip() for text in window.axis.texts)
    window.close()


def test_preview_schedules_delayed_3d_refresh(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()
    window.viewer3d_refresh_timer.stop()
    window.top_tabs.setCurrentWidget(window.viewer3d_group)

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"placeholder")
    fake_layout = SimpleNamespace(preview_path=preview_path, debug_preview_path=None)
    fake_result = SimpleNamespace(layout=fake_layout, geometry=window.bounds.midpoint())

    with (
        mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))),
        mock.patch.object(window.viewer3d_refresh_timer, "start") as start_mock,
        mock.patch.object(window, "_set_3d_view_from_layout") as render3d_mock,
    ):
        window._set_preview_from_result(fake_result, title="Current Design Preview")

    start_mock.assert_called_once_with(5000)
    render3d_mock.assert_not_called()
    assert window.viewer3d_pending_layout is fake_layout
    window.close()


def test_qt_refresh_surfaces_actual_bridge_pad_collision(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from rfic_transformer_inverse_design.api import default_run_config
    from rfic_transformer_inverse_design.interfaces import gui_qt
    from rfic_transformer_inverse_design.interfaces.gui_qt import TransformerConstraintQtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = TransformerConstraintQtGui(topology_mode="2t2t")
    window.refresh_timer.stop()

    cfg = default_run_config("2t2t")
    bad_geom = replace(cfg.bounds.midpoint(), offset_um=16.0)
    window.generated_config_path = tmp_path / "generated.yaml"

    with (
        mock.patch.object(window, "_current_run_config", return_value=cfg),
        mock.patch.object(window, "_geometry_from_controls", return_value=bad_geom),
        mock.patch.object(window, "_apply_discrete_controls", side_effect=lambda geometry: geometry),
        mock.patch.object(window, "_queue_preview_refresh") as queue_mock,
    ):
        window._refresh()

    queue_mock.assert_called_once()
    queued_run_config = queue_mock.call_args.kwargs["run_config"]
    queued_geometry = queue_mock.call_args.kwargs["geometry"]
    yaml_text = queue_mock.call_args.kwargs["yaml_text"]
    gdstk_check = gui_qt.run_transformer_gdstk_checks(queued_geometry, queued_run_config)
    fake_layout = SimpleNamespace(preview_path=tmp_path / "preview.png", debug_preview_path=None)
    fake_layout.preview_path.write_bytes(b"placeholder")
    window.preview_refresh_generation = 1
    with mock.patch("rfic_transformer_inverse_design.interfaces.gui_qt.mpimg.imread", return_value=np.zeros((4, 4, 3))):
        window._handle_preview_refresh_completed(
            {
                "generation": 1,
                "run_config": queued_run_config,
                "geometry": queued_geometry,
                "yaml_text": yaml_text,
                "bounds_errors": list(queued_run_config.bounds.validate(queued_geometry)),
                "geometry_errors": list(queued_geometry.validate()),
                "gdstk_check": gdstk_check,
                "layout": fake_layout,
                "image": np.zeros((4, 4, 3)),
            }
        )

    validation = window.validation_text.toPlainText()
    assert "Geometry violations:" in validation
    assert "primary_intermediate_bridge_pad_clearance_violations: 1" in validation
    assert "same_layer_spacing_violations: 1" in validation
    assert "stage outer:intermediate_pad on layer 39" in validation
    annotation_text = "\n".join(text.get_text() for text in window.axis.texts)
    assert "Primary\nouter width" in annotation_text
    assert "Secondary\nouter width" in annotation_text
    assert "Primary-secondary\noffset" in annotation_text
    window.close()
