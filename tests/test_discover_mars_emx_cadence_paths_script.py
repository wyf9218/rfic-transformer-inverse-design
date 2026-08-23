from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import os
import sys
from unittest import mock


def _load_discovery_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "discover_mars_emx_cadence_paths.py"
    spec = importlib.util.spec_from_file_location("discover_mars_emx_cadence_paths_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_cadence_oa_tools(cadence_root: Path) -> None:
    bin_dir = cadence_root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in ("dbAccess", "strmin", "strmout"):
        path = bin_dir / tool
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)


class DiscoverMarsEmxCadencePathsScriptTest(TransformerToolboxTestBase):
    def test_permission_denied_paths_are_skipped_during_scan(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            denied = root / "lost+found"
            denied.mkdir()
            out_dir = root / "out"
            original_exists = Path.exists

            def guarded_exists(path: Path) -> bool:
                if "lost+found" in path.parts:
                    raise PermissionError("permission denied for test")
                return original_exists(path)

            with mock.patch.object(Path, "exists", guarded_exists):
                status = discovery.main(["--root", str(root), "--out-dir", str(out_dir), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertIn("emx_binary", summary["missing_candidate_kinds"])

    def test_discovers_candidates_and_writes_patch_suggestion(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_binary = root / "tools" / "emx" / "bin" / "emx"
            proc = root / "pdk" / "proc" / "rf.proc"
            cadence_root = root / "cadence" / "ICADVM"
            virtuoso = cadence_root / "bin" / "virtuoso"
            cds = root / "pdk" / "cds.lib"
            layer_map = root / "pdk" / "layers.layermap"
            for path in (emx_binary, proc, virtuoso, cds, layer_map):
                path.parent.mkdir(parents=True, exist_ok=True)
            emx_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            emx_binary.chmod(0o755)
            proc.write_text("# proc\n", encoding="utf-8")
            virtuoso.write_text("#!/bin/sh\n", encoding="utf-8")
            virtuoso.chmod(0o755)
            _write_cadence_oa_tools(cadence_root)
            cds.write_text("DEFINE tsmc65lp ./tsmc65lp\n", encoding="utf-8")
            layer_map.write_text("# layermap\n", encoding="utf-8")
            out_dir = root / "out"

            status = discovery.main(
                [
                    "--root",
                    str(root),
                    "--max-depth",
                    "6",
                    "--config",
                    "configs/mars_dataset_500_wideband_20260613.yaml",
                    "--out-dir",
                    str(out_dir),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertTrue(summary["ready_to_patch"])
            self.assertEqual(summary["missing_candidate_kinds"], [])
            self.assertIn("tsmc65lp", summary["tech_lib_candidates"])
            selected = summary["selected_candidates"]
            self.assertEqual(Path(selected["emx_binary"]["path"]).resolve(), emx_binary.resolve())
            self.assertEqual(Path(selected["emx_process_file"]["path"]).resolve(), proc.resolve())
            self.assertEqual(Path(selected["cadence_install_root"]["path"]).resolve(), cadence_root.resolve())
            self.assertEqual(Path(selected["cadence_pdk_cds_lib"]["path"]).resolve(), cds.resolve())
            self.assertEqual(Path(selected["cadence_layer_map"]["path"]).resolve(), layer_map.resolve())
            patch_script = (out_dir / "mars_emx_cadence_path_patch_suggestion.sh").read_text(encoding="utf-8")
            self.assertIn("scripts/patch_mars_config_paths.py", patch_script)
            self.assertIn("--check-paths", patch_script)
            self.assertIn(str(emx_binary), patch_script)
            self.assertIn("tsmc65lp", patch_script)
            report = (out_dir / "mars_emx_cadence_path_discovery_report.md").read_text(encoding="utf-8")
            self.assertIn("Required Follow-Up", report)

    def test_incomplete_when_required_candidates_are_missing(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"

            status = discovery.main(["--root", str(root), "--out-dir", str(out_dir), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertFalse(summary["ready_to_patch"])
            self.assertIn("emx_binary", summary["missing_candidate_kinds"])
            patch_script = (out_dir / "mars_emx_cadence_path_patch_suggestion.sh").read_text(encoding="utf-8")
            self.assertIn("<FILL_EMX_BINARY>", patch_script)

    def test_environment_candidates_take_priority_over_scan_and_path_candidates(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scan_emx = root / "scan" / "emx"
            env_emx = root / "env" / "emx"
            path_emx = root / "path_bin" / "emx"
            for path in (scan_emx, env_emx, path_emx):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#!/bin/sh\n", encoding="utf-8")
                path.chmod(0o755)
            out_dir = root / "out"
            old_value = os.environ.get("EMX_BINARY")
            old_path = os.environ.get("PATH")
            os.environ["EMX_BINARY"] = str(env_emx)
            os.environ["PATH"] = f"{path_emx.parent}{os.pathsep}{old_path or ''}"
            try:
                status = discovery.main(["--root", str(root), "--out-dir", str(out_dir), "--no-fail-exit"])
            finally:
                if old_value is None:
                    os.environ.pop("EMX_BINARY", None)
                else:
                    os.environ["EMX_BINARY"] = old_value
                if old_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = old_path

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(summary["selected_candidates"]["emx_binary"]["path"]).resolve(), env_emx.resolve())
            self.assertEqual(summary["selected_candidates"]["emx_binary"]["source"], "env:EMX_BINARY")

    def test_dry_run_emx_binary_is_rejected_even_from_environment(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc = root / "pdk" / "proc" / "rf.proc"
            cadence_root = root / "cadence" / "ICADVM"
            virtuoso = cadence_root / "bin" / "virtuoso"
            cds = root / "pdk" / "cds.lib"
            layer_map = root / "pdk" / "layers.layermap"
            for path in (proc, virtuoso, cds, layer_map):
                path.parent.mkdir(parents=True, exist_ok=True)
            proc.write_text("# proc\n", encoding="utf-8")
            virtuoso.write_text("#!/bin/sh\n", encoding="utf-8")
            virtuoso.chmod(0o755)
            _write_cadence_oa_tools(cadence_root)
            cds.write_text("DEFINE tsmc65lp ./tsmc65lp\n", encoding="utf-8")
            layer_map.write_text("# layermap\n", encoding="utf-8")
            out_dir = root / "out"
            old_value = os.environ.get("EMX_BINARY")
            os.environ["EMX_BINARY"] = "/usr/bin/true"
            try:
                status = discovery.main(
                    [
                        "--root",
                        str(root),
                        "--out-dir",
                        str(out_dir),
                        "--no-fail-exit",
                    ]
                )
            finally:
                if old_value is None:
                    os.environ.pop("EMX_BINARY", None)
                else:
                    os.environ["EMX_BINARY"] = old_value

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertFalse(summary["ready_to_patch"])
            self.assertIn("emx_binary", summary["missing_candidate_kinds"])
            self.assertNotIn("emx_binary", summary["selected_candidates"])
            rejected = summary["rejected_dry_run_candidates"]
            self.assertTrue(any(item["kind"] == "emx_binary" and item["path"] == "/usr/bin/true" for item in rejected))
            report = (out_dir / "mars_emx_cadence_path_discovery_report.md").read_text(encoding="utf-8")
            self.assertIn("Rejected Dry-Run Candidates", report)
            self.assertIn("/usr/bin/true", report)

    def test_prefers_real_ic_root_over_cae_wrapper_root(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_binary = root / "tools" / "emx" / "bin" / "emx"
            proc = root / "pdk" / "proc" / "rf.proc"
            wrapper_root = root / "cae" / "apps"
            wrapper_virtuoso = wrapper_root / "bin" / "virtuoso"
            real_root = root / "cae" / "apps" / "data" / "cadence-2025" / "installs" / "IC231"
            real_virtuoso = real_root / "bin" / "virtuoso"
            cds = root / "pdk" / "cds.lib"
            layer_map = root / "pdk" / "layers.layermap"
            for path in (emx_binary, proc, wrapper_virtuoso, real_virtuoso, cds, layer_map):
                path.parent.mkdir(parents=True, exist_ok=True)
            emx_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            emx_binary.chmod(0o755)
            proc.write_text("# proc\n", encoding="utf-8")
            wrapper_virtuoso.write_text("#!/bin/sh\n", encoding="utf-8")
            wrapper_virtuoso.chmod(0o755)
            real_virtuoso.write_text("#!/bin/sh\n", encoding="utf-8")
            real_virtuoso.chmod(0o755)
            _write_cadence_oa_tools(real_root)
            cds.write_text("DEFINE tsmcN65 ./tsmcN65\n", encoding="utf-8")
            layer_map.write_text("# layermap\n", encoding="utf-8")
            out_dir = root / "out"

            status = discovery.main(["--root", str(root), "--max-depth", "9", "--out-dir", str(out_dir)])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            selected = summary["selected_candidates"]
            self.assertEqual(Path(selected["cadence_install_root"]["path"]).resolve(), real_root.resolve())
            self.assertEqual(summary["overall_status"], "PASS")

    def test_cadence_wrapper_root_without_oa_tools_is_not_launch_ready(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_binary = root / "tools" / "emx" / "bin" / "emx"
            proc = root / "pdk" / "proc" / "rf.proc"
            cadence_root = root / "cae" / "apps"
            virtuoso = cadence_root / "bin" / "virtuoso"
            cds = root / "pdk" / "cds.lib"
            layer_map = root / "pdk" / "layers.layermap"
            for path in (emx_binary, proc, virtuoso, cds, layer_map):
                path.parent.mkdir(parents=True, exist_ok=True)
            emx_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            emx_binary.chmod(0o755)
            proc.write_text("# proc\n", encoding="utf-8")
            virtuoso.write_text("#!/bin/sh\n", encoding="utf-8")
            virtuoso.chmod(0o755)
            cds.write_text("DEFINE tsmcN65 ./tsmcN65\n", encoding="utf-8")
            layer_map.write_text("# layermap\n", encoding="utf-8")
            out_dir = root / "out"

            status = discovery.main(["--root", str(root), "--max-depth", "6", "--out-dir", str(out_dir), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            self.assertFalse(summary["ready_to_patch"])
            selected = summary["selected_candidates"]
            self.assertEqual(Path(selected["cadence_install_root"]["path"]).resolve(), cadence_root.resolve())

    def test_command_hint_file_seeds_emx_binary_and_proc_candidates(self) -> None:
        discovery = _load_discovery_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_binary = root / "cae" / "EMX20251" / "bin" / "emx"
            proc = root / "pdk" / "stack" / "typical.proc"
            command = root / "project_runbook" / "target_emx_wideband_rerun.commands.sh"
            for path in (emx_binary, proc, command):
                path.parent.mkdir(parents=True, exist_ok=True)
            emx_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            emx_binary.chmod(0o755)
            proc.write_text("# proc\n", encoding="utf-8")
            command.write_text(
                "# recovered target command\n"
                f"{emx_binary} /tmp/layout.gds TRANSFORMER_001 {proc} --touchstone -s /tmp/out.s4p 5000000000 50000000000\n",
                encoding="utf-8",
            )
            out_dir = root / "out"

            status = discovery.main(
                [
                    "--root",
                    str(root / "empty_scan_root"),
                    "--hint-command",
                    str(command),
                    "--out-dir",
                    str(out_dir),
                    "--tech-lib-hint",
                    "tsmc65lp",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_cadence_path_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertIn(str(command.resolve()), summary["hint_command_files"])
            selected = summary["selected_candidates"]
            self.assertEqual(Path(selected["emx_binary"]["path"]).resolve(), emx_binary.resolve())
            self.assertEqual(Path(selected["emx_process_file"]["path"]).resolve(), proc.resolve())
            self.assertEqual(selected["emx_binary"]["source"], f"hint-command:{command.name}")
            self.assertEqual(selected["emx_process_file"]["source"], f"hint-command:{command.name}")
            self.assertIn("--emx-binary", summary["suggested_patch_command"])
            self.assertIn(str(proc), summary["suggested_patch_command"])
            report = (out_dir / "mars_emx_cadence_path_discovery_report.md").read_text(encoding="utf-8")
            self.assertIn("Hint command files", report)
