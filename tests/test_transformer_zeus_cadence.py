import tempfile
import unittest
from unittest import mock
from pathlib import Path

import gdstk

from rfic_transformer_inverse_design.sim.emx.layout_export import EMXLayoutManifest, EMXPort

from rfic_transformer_inverse_design.api import default_run_config
from rfic_transformer_inverse_design.core.types import TransformerLayoutExport
from rfic_transformer_inverse_design.execution.zeus_cadence import (
    CADENCE_FATAL_STDERR_MARKERS,
    _run_logged_command,
    _write_batch_input_gds,
    build_create_library_skill,
    build_create_pins_batch_skill,
    build_create_pins_skill,
    build_local_cds_lib,
    build_strmout_command,
    cadence_binary,
    collect_cadence_pin_labels,
    create_zeus_cadence_workspace,
    run_transformer_zeus_cadence_roundtrip,
    sanitize_oa_lib_name,
)


class TransformerZeusCadenceTest(unittest.TestCase):
    def test_logged_cadence_command_rejects_fatal_stderr_with_zero_returncode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            result = mock.Mock(returncode=0, stdout="normal output\n", stderr="*Error* invalid SKILL argument\n")
            with mock.patch(
                "rfic_transformer_inverse_design.execution.zeus_cadence.subprocess.run",
                return_value=result,
            ):
                with self.assertRaisesRegex(RuntimeError, "fatal stderr markers"):
                    _run_logged_command(
                        command=["dbAccess"],
                        cwd=tmp,
                        env={},
                        stdout_path=tmp / "stdout.log",
                        stderr_path=tmp / "stderr.log",
                        failure_label="Cadence OA library creation",
                        fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
                    )

            self.assertEqual((tmp / "stderr.log").read_text(encoding="utf-8"), result.stderr)

    def test_logged_cadence_command_allows_nonfatal_warning_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            result = mock.Mock(returncode=0, stdout="normal output\n", stderr="*WARNING* optional library missing\n")
            with mock.patch(
                "rfic_transformer_inverse_design.execution.zeus_cadence.subprocess.run",
                return_value=result,
            ):
                _run_logged_command(
                    command=["dbAccess"],
                    cwd=tmp,
                    env={},
                    stdout_path=tmp / "stdout.log",
                    stderr_path=tmp / "stderr.log",
                    failure_label="Cadence OA library creation",
                    fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
                )

            self.assertEqual((tmp / "stderr.log").read_text(encoding="utf-8"), result.stderr)

    def test_build_create_library_skill_binds_with_tech_library_name_string(self) -> None:
        skill = build_create_library_skill(
            oa_lib_name="xfmr_test",
            oa_lib_dir=Path("/tmp/xfmr_test"),
            tech_lib_name="tsmcN65",
        )

        self.assertIn('techLib = ddGetObj("tsmcN65")', skill)
        self.assertIn('techBindTechFile(libObj "tsmcN65")', skill)
        self.assertNotIn("techBindTechFile(libObj techLib)", skill)

    def test_collect_cadence_pin_labels_preserves_port_order(self) -> None:
        manifest = EMXLayoutManifest(
            layout_path="layout.gds",
            top_cell="TOP",
            ports=(
                EMXPort(
                    name="P001",
                    signal_labels=("P001",),
                    ground_labels=("P001_G",),
                    internal_size_um=(4.0, 4.0),
                ),
                EMXPort(
                    name="P002",
                    signal_labels=("P002", "P001"),
                    ground_labels=(),
                    internal_size_um=(4.0, 4.0),
                ),
            ),
            metal_layer=1,
            metal_datatype=0,
            ground_layer=2,
            ground_datatype=0,
            label_layer=10,
            label_datatype=0,
            cadence_pin_purpose=51,
        )

        self.assertEqual(
            collect_cadence_pin_labels(manifest),
            ("P001", "P001_G", "P002"),
        )

    def test_sanitize_oa_lib_name_normalizes_symbols(self) -> None:
        self.assertEqual(sanitize_oa_lib_name("7b12a36482547683"), "xfmr_7b12a36482547683")
        self.assertEqual(sanitize_oa_lib_name("xfmr cadence/pins"), "xfmr_cadence_pins")

    def test_build_local_cds_lib_writes_scratch_define(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = Path(tmpdir) / "oa_lib"
            text = build_local_cds_lib(
                pdk_cds_lib="/disk/pdk/cds.lib",
                oa_lib_name="xfmr_test",
                oa_lib_dir=lib_dir,
            )

            self.assertEqual(
                text,
                f"INCLUDE /disk/pdk/cds.lib\nDEFINE xfmr_test {lib_dir.as_posix()}\n",
            )

    def test_create_workspace_writes_local_cds_lib(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = create_zeus_cadence_workspace(
                root_dir=Path(tmpdir),
                oa_lib_name="xfmr_test",
                pdk_cds_lib="/disk/pdk/cds.lib",
            )

            self.assertTrue(workspace.cds_lib_path.exists())
            self.assertIn("DEFINE xfmr_test", workspace.cds_lib_path.read_text(encoding="ascii"))
            self.assertEqual(workspace.streamout_gds_path.name, "transformer_layout_cadpins.gds")

    def test_build_create_pins_skill_embeds_labels_and_access_dirs(self) -> None:
        skill = build_create_pins_skill(
            oa_lib_name="xfmr_test",
            top_cell="TRANSFORMER_TOP",
            labels=("P001", "P002_G"),
        )

        self.assertIn('dbOpenCellViewByType("xfmr_test" "TRANSFORMER_TOP" "layout" "" "a")', skill)
        self.assertIn('foreach(port \'("P001" "P002_G")', skill)
        self.assertIn("pinFig = _xfmrFindPinFigure(cv label)", skill)
        self.assertIn("procedure(_xfmrFindDrawingFigure(cv labelFig)", skill)
        self.assertIn("procedure(_xfmrTraceWidthForFigure(fig pt)", skill)
        self.assertIn("procedure(_xfmrPolygonCrossingsAtY(points y)", skill)
        self.assertIn("procedure(_xfmrMinSpanFromCrossings(crossings)", skill)
        self.assertIn('cadr(fig~>lpp) == "drawing"', skill)
        self.assertIn("unless(found", skill)
        self.assertIn("bbox = pinFig~>bBox", skill)
        self.assertIn("halfHeight = _xfmrTraceWidthForLabel(cv labelFig) / 2.0", skill)
        self.assertIn("procedure(_xfmrGridCenteredContainedBBox(bbox pt grid)", skill)
        self.assertIn("center = list(_xfmrSnapToGrid(car(pt) grid)", skill)
        self.assertIn("halfWidth = floor((min(car(center) - car(ll) car(ur) - car(center))", skill)
        self.assertIn("bbox = _xfmrGridCenteredContainedBBox(list(ll ur) pt manufacturingGrid)", skill)
        self.assertIn("manufacturingGrid = 0.005", skill)
        self.assertIn("pinFig = _xfmrCreateLabelPinRect(cv label pinFig manufacturingGrid)", skill)
        self.assertIn('pinLpp = list(car(labelFig~>lpp) "pin")', skill)
        self.assertIn('pin~>accessDir = accessDir', skill)
        self.assertNotIn("dbDeleteObject(pinFig)", skill)
        self.assertNotIn("labelFig~>xy =", skill)

    def test_build_create_pins_batch_skill_handles_multiple_cells(self) -> None:
        skill = build_create_pins_batch_skill(
            oa_lib_name="xfmr_test",
            cells=(
                ("TOP_A", ("P001", "P002_G")),
                ("TOP_B", ("P003", "P004_G")),
            ),
        )

        self.assertIn('dbOpenCellViewByType("xfmr_test" "TOP_A" "layout" "" "a")', skill)
        self.assertIn('dbOpenCellViewByType("xfmr_test" "TOP_B" "layout" "" "a")', skill)
        self.assertIn('foreach(port \'("P001" "P002_G")', skill)
        self.assertIn('foreach(port \'("P003" "P004_G")', skill)

    def test_build_create_pins_batch_skill_uses_requested_manufacturing_grid(self) -> None:
        skill = build_create_pins_batch_skill(
            oa_lib_name="xfmr_test",
            cells=(("TOP", ("P001",)),),
            manufacturing_grid_um=0.01,
        )

        self.assertIn("manufacturingGrid = 0.01", skill)

    def test_build_create_pins_batch_skill_rejects_invalid_manufacturing_grid(self) -> None:
        for invalid in (0.0, -0.005, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "finite and positive"):
                    build_create_pins_batch_skill(
                        oa_lib_name="xfmr_test",
                        cells=(("TOP", ("P001",)),),
                        manufacturing_grid_um=invalid,
                    )

    def test_build_strmout_command_uses_geometry_and_text_pin_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = create_zeus_cadence_workspace(
                root_dir=Path(tmpdir),
                oa_lib_name="xfmr_test",
                pdk_cds_lib="/disk/pdk/cds.lib",
            )
            command = build_strmout_command(
                cadence_install_root="/cadence/IC231",
                workspace=workspace,
                top_cell="TRANSFORMER_TOP",
                layer_map_path="/disk/pdk/layermap",
                tech_lib_name="exampleTechLib",
                cadence_pin_purpose=51,
            )

            expected_prefix = (
                ["/usr/local/bin/uname26", "/cadence/IC231/bin/strmout", "-library"]
                if Path("/usr/local/bin/uname26").exists()
                else ["/cadence/IC231/bin/strmout", "-library"]
            )
            self.assertEqual(command[: len(expected_prefix)], expected_prefix)
            self.assertIn("-convertPin", command)
            self.assertIn("geometryAndText", command)
            self.assertIn("-pinAttNum", command)
            self.assertIn("51", command)
            self.assertIn(str(workspace.streamout_gds_path), command)

    def test_cadence_binary_uses_uname26_when_available(self) -> None:
        with mock.patch("rfic_transformer_inverse_design.execution.zeus_cadence.Path.exists", return_value=True):
            self.assertEqual(
                cadence_binary("/cadence/IC231", "strmout"),
                "/usr/local/bin/uname26 /cadence/IC231/bin/strmout",
            )

    def test_cadence_binary_falls_back_without_uname26(self) -> None:
        with mock.patch("rfic_transformer_inverse_design.execution.zeus_cadence.Path.exists", return_value=False):
            self.assertEqual(cadence_binary("/cadence/IC231", "strmout"), "/cadence/IC231/bin/strmout")

    def test_write_batch_input_gds_preserves_child_cells_and_datatypes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            top_a = "TOP_A"
            gds_a = tmp / "a.gds"
            lib_a = gdstk.Library()
            cell_a = lib_a.new_cell(top_a)
            cell_a.add(gdstk.rectangle((0.0, 0.0), (10.0, 4.0), layer=39, datatype=60))
            cell_a.add(gdstk.rectangle((1.0, 1.0), (3.0, 3.0), layer=58, datatype=40))
            lib_a.write_gds(gds_a)

            top_b = "TOP_B"
            gds_b = tmp / "b.gds"
            lib_b = gdstk.Library()
            cell_b = lib_b.new_cell(top_b)
            cell_b.add(gdstk.rectangle((0.0, 0.0), (8.0, 2.0), layer=74, datatype=0))
            cell_b.add(gdstk.rectangle((2.0, 0.5), (6.0, 1.5), layer=38, datatype=40))
            lib_b.write_gds(gds_b)

            layout_a = TransformerLayoutExport(
                gds_path=gds_a,
                manifest_path=tmp / "a.layout.json",
                preview_path=tmp / "a.png",
                debug_preview_path=tmp / "a_debug.png",
                top_cell=top_a,
            )
            layout_b = TransformerLayoutExport(
                gds_path=gds_b,
                manifest_path=tmp / "b.layout.json",
                preview_path=tmp / "b.png",
                debug_preview_path=tmp / "b_debug.png",
                top_cell=top_b,
            )

            batch_gds = tmp / "batch.gds"
            _write_batch_input_gds(
                layouts=(layout_a, layout_b),
                out_path=batch_gds,
                batch_top_cell="BATCH_TOP",
            )

            merged = gdstk.read_gds(str(batch_gds))
            batch_cell = next(cell for cell in merged.cells if cell.name == "BATCH_TOP")
            self.assertEqual(len(batch_cell.references), 2)

            merged_a = next(cell for cell in merged.cells if cell.name == top_a)
            merged_b = next(cell for cell in merged.cells if cell.name == top_b)
            counts_a = {(int(poly.layer), int(poly.datatype)) for poly in merged_a.polygons}
            counts_b = {(int(poly.layer), int(poly.datatype)) for poly in merged_b.polygons}

            self.assertEqual(counts_a, {(39, 60), (58, 40)})
            self.assertEqual(counts_b, {(74, 0), (38, 40)})

    def test_roundtrip_export_stop_writes_partial_summary_without_running_cadence(self) -> None:
        cfg = default_run_config("1t1t")
        geometry = cfg.bounds.midpoint()

        with tempfile.TemporaryDirectory() as tmpdir:
            payload = run_transformer_zeus_cadence_roundtrip(
                run_config=cfg,
                geometry=geometry,
                root_dir=Path(tmpdir),
                stop_after="export",
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["stop_after"], "export")
            self.assertIn("cadence", payload)
            summary_candidates = list(Path(tmpdir).glob("evaluations/*/summary_cadence_roundtrip.json"))
            self.assertEqual(len(summary_candidates), 1)


if __name__ == "__main__":
    unittest.main()
