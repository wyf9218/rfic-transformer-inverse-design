import os
import tempfile
import unittest
from pathlib import Path

from rfic_transformer_inverse_design.sim.base import SolverType
from rfic_transformer_inverse_design.sim.emx.layout_export import EMXLayoutManifest, EMXPort
from rfic_transformer_inverse_design.sim.emx.simulation import EMXSimulation


def _manifest_with_ports(count: int, layout_path: Path) -> EMXLayoutManifest:
    ports = tuple(
        EMXPort(
            name=f"P{index + 1:03d}",
            signal_labels=(f"P{index + 1:03d}_SIG",),
            ground_labels=(f"P{index + 1:03d}_GND",),
            internal_size_um=(2.0, 2.0),
        )
        for index in range(count)
    )
    return EMXLayoutManifest(
        layout_path=str(layout_path),
        top_cell="TRANSFORMER",
        ports=ports,
        metal_layer=1,
        metal_datatype=0,
        ground_layer=2,
        ground_datatype=0,
        label_layer=10,
        label_datatype=0,
    )


class TransformerEmxBackendTest(unittest.TestCase):
    def test_build_subprocess_env_includes_cadence_settings(self) -> None:
        sim = EMXSimulation()
        sim._emx_home = "/cae/apps/data/cadence-2025/installs/EMX20251"
        sim._license_file = "27000@example-license-server"
        sim._cdslmd_license_file = "27000@example-license-server"
        sim._skip_os_check = True

        env = sim._build_subprocess_env()

        self.assertEqual(env["EMXHOME"], "/cae/apps/data/cadence-2025/installs/EMX20251")
        self.assertEqual(env["LM_LICENSE_FILE"], "27000@example-license-server")
        self.assertEqual(env["CDS_LIC_FILE"], "27000@example-license-server")
        self.assertEqual(env["CDSLMD_LICENSE_FILE"], "27000@example-license-server")
        self.assertEqual(env["CDS_SKIP_OS_CHECK_ON_STARTUP"], "1")
        self.assertEqual(
            env["PATH"].split(os.pathsep)[0],
            str(Path("/cae/apps/data/cadence-2025/installs/EMX20251/bin")),
        )

    def test_build_emx_command_uses_explicit_frequency_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            process = tmp / "typical.proc"
            layout = tmp / "layout.gds"
            process.write_text("proc", encoding="utf-8")
            layout.write_text("gds", encoding="utf-8")
            sim = EMXSimulation(emx_binary="/bin/emx", process_file=str(process), top_cell="TRANSFORMER")
            sim.create_project(tmp / "emx")
            sim.configure_solver(
                SolverType.FREQUENCY_DOMAIN,
                freq_start_hz=5.0e9,
                freq_stop_hz=50.0e9,
                num_freq_points=451,
                freq_points_hz=[5.0e9, 5.1e9, 50.0e9],
            )
            command = sim._build_emx_command(layout)

        self.assertEqual(command[-3:], ["5000000000", "5100000000", "50000000000"])

    def test_build_emx_command_uses_manifest_port_count_for_s8p_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            process = tmp / "typical.proc"
            layout = tmp / "layout.gds"
            process.write_text("proc", encoding="utf-8")
            layout.write_text("gds", encoding="utf-8")
            sim = EMXSimulation(emx_binary="/bin/emx", process_file=str(process), top_cell="TRANSFORMER")
            sim.create_project(tmp / "emx")
            sim._layout_manifest = _manifest_with_ports(8, layout)
            sim.configure_solver(
                SolverType.FREQUENCY_DOMAIN,
                freq_start_hz=5.0e9,
                freq_stop_hz=50.0e9,
                num_freq_points=2,
            )

            command = sim._build_emx_command(layout)

        self.assertIn("-s", command)
        output_path = Path(command[command.index("-s") + 1])
        self.assertEqual(output_path.name, "emx.s8p")

    def test_find_touchstone_output_prefers_manifest_matched_s8p_over_stale_s4p(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            layout = tmp / "layout.gds"
            sim = EMXSimulation()
            sim.create_project(tmp / "emx")
            sim._layout_manifest = _manifest_with_ports(8, layout)
            assert sim._project_dir is not None
            (sim._project_dir / "emx.s4p").write_text("stale 4-port output\n", encoding="ascii")
            expected = sim._project_dir / "emx.s8p"
            expected.write_text("new 8-port output\n", encoding="ascii")

            found = sim._find_touchstone_output()

        self.assertEqual(found, expected)

    def test_find_touchstone_output_fails_when_manifest_matched_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            layout = tmp / "layout.gds"
            sim = EMXSimulation()
            sim.create_project(tmp / "emx")
            sim._layout_manifest = _manifest_with_ports(8, layout)
            assert sim._project_dir is not None
            (sim._project_dir / "emx.s4p").write_text("stale 4-port output\n", encoding="ascii")

            with self.assertRaisesRegex(RuntimeError, "emx\\.s8p"):
                sim._find_touchstone_output()


if __name__ == "__main__":
    unittest.main()
