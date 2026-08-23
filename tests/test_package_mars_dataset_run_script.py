from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys
import tarfile


def _load_package_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "package_mars_dataset_run.py"
    spec = importlib.util.spec_from_file_location("package_mars_dataset_run_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PackageMarsDatasetRunScriptTest(TransformerToolboxTestBase):
    def test_packages_minimal_dataset_artifacts(self) -> None:
        package = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "runs" / "dataset500"
            (run / "evaluations" / "abc" / "emx").mkdir(parents=True)
            (run / "evaluations" / "abc" / "layout").mkdir(parents=True)
            (run / "dataset_manifest.json").write_text("{}", encoding="utf-8")
            (run / "dataset_rows.csv").write_text("ok\ntrue\n", encoding="utf-8")
            (run / "final500_ground_clearance_audit.json").write_text('{"candidate_count":1}', encoding="utf-8")
            (run / "evaluations" / "abc" / "summary.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx.s4p").write_text("# GHz S RI R 50\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx_command.json").write_text("[]", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.gds").write_bytes(b"GDS")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout_preview.png").write_bytes(b"PNG")
            (run / "dataset_quality_gates" / "sampling_distribution_audit").mkdir(parents=True)
            (run / "dataset_quality_gates" / "dataset_quality_gates_summary.json").write_text("{}", encoding="utf-8")
            (run / "dataset_quality_gates" / "dataset_quality_gates_report.md").write_text("# gates\n", encoding="utf-8")
            (run / "dataset_quality_gates" / "sampling_distribution_audit" / "sampling_distribution_fields.csv").write_text("field\nx\n", encoding="utf-8")
            (run / "dataset_quality_gates" / "sampling_distribution_audit" / "sampling_distribution_hist.png").write_bytes(b"PNG")
            (run / "mars_run_progress_audit_20260613").mkdir()
            (run / "mars_run_progress_audit_20260613" / "mars_run_progress_summary.json").write_text('{"overall_status":"PASS"}', encoding="utf-8")
            (run / "mars_run_progress_audit_20260613" / "mars_run_progress_report.md").write_text("# progress\n", encoding="utf-8")
            (run / "mars_run_progress_audit_20260613" / "mars_run_progress_rows.csv").write_text("key\nabc\n", encoding="utf-8")
            (run / "mars_run_progress_watch_20260613" / "snapshots").mkdir(parents=True)
            (run / "mars_run_progress_watch_20260613" / "mars_run_progress_watch_summary.json").write_text('{"overall_status":"PASS"}', encoding="utf-8")
            (run / "mars_run_progress_watch_20260613" / "mars_run_progress_watch_history.csv").write_text("overall_status\nPASS\n", encoding="utf-8")
            (run / "mars_run_progress_watch_20260613" / "mars_run_progress_watch_history.jsonl").write_text('{"overall_status":"PASS"}\n', encoding="utf-8")
            (run / "mars_run_progress_watch_20260613" / "snapshots" / "iteration_000001_mars_run_progress_summary.json").write_text(
                '{"overall_status":"PASS"}',
                encoding="utf-8",
            )
            (run / "unneeded.raw").write_text("skip", encoding="utf-8")

            tarball = root / "dataset500_minimal.tar.gz"
            status = package.main([str(run), "--out", str(tarball)])

            self.assertEqual(status, 0)
            self.assertTrue(tarball.exists())
            inventory = json.loads((root / "dataset500_minimal.tar.gz.inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(inventory["file_count"], 17)
            self.assertEqual(inventory["empty_file_count"], 0)
            report_path = root / "dataset500_minimal.tar.gz.inventory.md"
            self.assertTrue(report_path.exists())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# MARS Dataset Transfer Inventory", report)
            self.assertIn("- Empty files: `0`", report)
            self.assertIn("Empty transfer files are refused before tarball creation.", report)
            self.assertIn("| `touchstone_files` | 1 |", report)
            self.assertIn("## Boundaries", report)
            self.assertFalse(inventory["include_gds"])
            self.assertFalse(inventory["include_layout_previews"])
            self.assertFalse(inventory["include_quality_figures"])
            self.assertEqual(
                inventory["category_counts"],
                {
                    "dataset_manifest": 1,
                    "dataset_rows": 1,
                    "run_config_files": 0,
                    "evaluation_summaries": 1,
                    "touchstone_files": 1,
                    "emx_command_files": 1,
                    "layout_json_files": 1,
                    "clearance_audit_files": 1,
                    "gds_files": 0,
                    "layout_preview_files": 0,
                    "quality_gate_top_summaries": 1,
                    "quality_gate_summary_files": 1,
                    "quality_gate_report_files": 1,
                    "quality_gate_csv_files": 1,
                    "quality_gate_figure_files": 0,
                    "progress_audit_summary_files": 1,
                    "progress_audit_report_files": 1,
                    "progress_audit_csv_files": 1,
                    "progress_watch_summary_files": 1,
                    "progress_watch_history_files": 2,
                    "progress_watch_snapshot_files": 1,
                    "next_gen_run_status_summary_files": 0,
                    "next_gen_run_status_report_files": 0,
                    "next_gen_run_status_csv_files": 0,
                    "objective_acceptance_summary_files": 0,
                    "objective_acceptance_report_files": 0,
                    "objective_acceptance_csv_files": 0,
                    "hfss_validation_asset_files": 0,
                    "hfss_validation_touchstone_files": 0,
                    "hfss_validation_script_files": 0,
                },
            )
            with tarfile.open(tarball, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("dataset500/dataset_manifest.json", names)
            self.assertIn("dataset500/final500_ground_clearance_audit.json", names)
            self.assertIn("dataset500/evaluations/abc/emx/emx.s4p", names)
            self.assertIn("dataset500/dataset_quality_gates/dataset_quality_gates_summary.json", names)
            self.assertIn("dataset500/dataset_quality_gates/dataset_quality_gates_report.md", names)
            self.assertIn("dataset500/dataset_quality_gates/sampling_distribution_audit/sampling_distribution_fields.csv", names)
            self.assertIn("dataset500/mars_run_progress_audit_20260613/mars_run_progress_summary.json", names)
            self.assertIn("dataset500/mars_run_progress_watch_20260613/mars_run_progress_watch_summary.json", names)
            self.assertIn("dataset500/mars_run_progress_watch_20260613/mars_run_progress_watch_history.jsonl", names)
            self.assertNotIn("dataset500/evaluations/abc/layout/transformer_layout.gds", names)
            self.assertNotIn("dataset500/evaluations/abc/layout/transformer_layout_preview.png", names)
            self.assertNotIn("dataset500/dataset_quality_gates/sampling_distribution_audit/sampling_distribution_hist.png", names)
            self.assertNotIn("dataset500/unneeded.raw", names)

    def test_can_include_gds_layout_previews_and_quality_figures_for_reaudit(self) -> None:
        package = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "runs" / "dataset500"
            (run / "evaluations" / "abc" / "emx").mkdir(parents=True)
            (run / "evaluations" / "abc" / "layout").mkdir(parents=True)
            (run / "dataset_manifest.json").write_text("{}", encoding="utf-8")
            (run / "dataset_rows.csv").write_text("ok\ntrue\n", encoding="utf-8")
            (run / "final500_ground_clearance_audit.json").write_text('{"candidate_count":1}', encoding="utf-8")
            (run / "evaluations" / "abc" / "summary.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx.s4p").write_text("# GHz S RI R 50\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.gds").write_bytes(b"GDS")
            (run / "evaluations" / "abc" / "layout" / "transformer_port_debug.png").write_bytes(b"PNG")
            (run / "evaluations" / "abc" / "layout" / "._transformer_port_debug.png").write_bytes(b"METADATA")
            (run / "evaluations" / "abc" / "layout" / "__pycache__").mkdir()
            (run / "evaluations" / "abc" / "layout" / "__pycache__" / "debug.cpython-312.pyc").write_bytes(b"BYTECODE")
            (run / "dataset_quality_gates" / "dataset_visualizations").mkdir(parents=True)
            (run / "dataset_quality_gates" / "dataset_visualizations" / "uniformity.png").write_bytes(b"PNG")

            tarball = root / "dataset500_with_gds.tar.gz"
            report_path = root / "custom_inventory_report.md"
            status = package.main(
                [
                    str(run),
                    "--out",
                    str(tarball),
                    "--report",
                    str(report_path),
                    "--include-gds",
                    "--include-layout-previews",
                    "--include-quality-figures",
                ]
            )

            self.assertEqual(status, 0)
            inventory = json.loads((root / "dataset500_with_gds.tar.gz.inventory.json").read_text(encoding="utf-8"))
            self.assertTrue(inventory["include_gds"])
            self.assertTrue(inventory["include_layout_previews"])
            self.assertTrue(inventory["include_quality_figures"])
            self.assertEqual(inventory["category_counts"]["gds_files"], 1)
            self.assertEqual(inventory["category_counts"]["layout_preview_files"], 1)
            self.assertEqual(inventory["category_counts"]["quality_gate_figure_files"], 1)
            self.assertEqual(inventory["category_counts"]["touchstone_files"], 1)
            self.assertTrue(report_path.exists())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("| `quality_gate_figure_files` | 1 |", report)
            self.assertIn("bytecode/cache and platform metadata files are excluded", report)
            inventory_paths = {item["relative_to_run_parent"] for item in inventory["files"]}
            self.assertFalse(any("__pycache__" in path or "/._" in path or path.endswith(".pyc") for path in inventory_paths))
            with tarfile.open(tarball, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("dataset500/evaluations/abc/layout/transformer_layout.gds", names)
            self.assertIn("dataset500/evaluations/abc/layout/transformer_port_debug.png", names)
            self.assertIn("dataset500/dataset_quality_gates/dataset_visualizations/uniformity.png", names)
            self.assertNotIn("dataset500/evaluations/abc/layout/._transformer_port_debug.png", names)
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

    def test_packages_final_s8p_run_config_for_after_import_target_layout_smoke(self) -> None:
        package = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "runs" / "dataset500"
            (run / "evaluations" / "abc" / "emx").mkdir(parents=True)
            (run / "evaluations" / "abc" / "layout").mkdir(parents=True)
            (run / "dataset_manifest.json").write_text("{}", encoding="utf-8")
            (run / "dataset_rows.csv").write_text("ok\ntrue\n", encoding="utf-8")
            (run / "final_s8p_physical_feature_500.yaml").write_text("dataset:\n  count: 500\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "summary.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx.s8p").write_text("# GHz S RI R 50\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx_command.json").write_text("[]", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")

            tarball = root / "dataset500_config.tar.gz"
            status = package.main([str(run), "--out", str(tarball)])

            self.assertEqual(status, 0)
            inventory = json.loads((root / "dataset500_config.tar.gz.inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(inventory["category_counts"]["run_config_files"], 1)
            with tarfile.open(tarball, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("dataset500/final_s8p_physical_feature_500.yaml", names)

    def test_packages_next_gen_s8p_status_and_objective_acceptance_evidence(self) -> None:
        package = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "runs" / "dataset500"
            (run / "evaluations" / "abc" / "emx").mkdir(parents=True)
            (run / "evaluations" / "abc" / "layout").mkdir(parents=True)
            (run / "dataset_manifest.json").write_text("{}", encoding="utf-8")
            (run / "dataset_rows.csv").write_text("ok\ntrue\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "summary.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx.s8p").write_text("# GHz S RI R 50\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx_command.json").write_text("[]", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
            status_dir = run / "next_gen_s8p_mars_run_status"
            status_dir.mkdir()
            (status_dir / "next_gen_s8p_mars_run_status_summary.json").write_text(
                '{"overall_status":"WAITING_FOR_HFSS_EXPORT"}',
                encoding="utf-8",
            )
            (status_dir / "next_gen_s8p_mars_run_status_report.md").write_text("# run status\n", encoding="utf-8")
            (status_dir / "next_gen_s8p_mars_run_status_evidence.csv").write_text("status\nWAITING\n", encoding="utf-8")
            objective_dir = run / "next_gen_s8p_objective_acceptance"
            objective_dir.mkdir()
            (objective_dir / "next_gen_s8p_objective_acceptance_summary.json").write_text(
                '{"overall_status":"WAITING","decision":"DO_NOT_CLAIM_NEXT_GEN_S8P_OBJECTIVE_COMPLETE"}',
                encoding="utf-8",
            )
            (objective_dir / "NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md").write_text("# objective\n", encoding="utf-8")
            (objective_dir / "next_gen_s8p_objective_acceptance_evidence.csv").write_text("status\nWAITING\n", encoding="utf-8")

            tarball = root / "dataset500_status.tar.gz"
            status = package.main([str(run), "--out", str(tarball)])

            self.assertEqual(status, 0)
            inventory = json.loads((root / "dataset500_status.tar.gz.inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(inventory["category_counts"]["next_gen_run_status_summary_files"], 1)
            self.assertEqual(inventory["category_counts"]["next_gen_run_status_report_files"], 1)
            self.assertEqual(inventory["category_counts"]["next_gen_run_status_csv_files"], 1)
            self.assertEqual(inventory["category_counts"]["objective_acceptance_summary_files"], 1)
            self.assertEqual(inventory["category_counts"]["objective_acceptance_report_files"], 1)
            self.assertEqual(inventory["category_counts"]["objective_acceptance_csv_files"], 1)
            with tarfile.open(tarball, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("dataset500/next_gen_s8p_mars_run_status/next_gen_s8p_mars_run_status_summary.json", names)
            self.assertIn("dataset500/next_gen_s8p_objective_acceptance/next_gen_s8p_objective_acceptance_summary.json", names)

    def test_can_include_selected_hfss_validation_assets(self) -> None:
        package = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "runs" / "dataset500"
            (run / "evaluations" / "abc" / "emx").mkdir(parents=True)
            (run / "evaluations" / "abc" / "layout").mkdir(parents=True)
            (run / "dataset_manifest.json").write_text("{}", encoding="utf-8")
            (run / "dataset_rows.csv").write_text("ok\ntrue\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "summary.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx.s8p").write_text("# GHz S RI R 50\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx_command.json").write_text("[]", encoding="utf-8")
            (run / "evaluations" / "abc" / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
            aedt = run / "dataset_quality_gates_s8p_physical_feature" / "selected_s8p_hfss_aedt_scripts"
            aedt.mkdir(parents=True)
            (aedt / "run_generated_hfss_s8p_scripts.commands.ps1").write_text("Write-Host ready\n", encoding="utf-8")
            (aedt / "build_hfss_s8p_from_payload.py").write_text("print('ready')\n", encoding="utf-8")
            (aedt / "source_geometry.gds").write_bytes(b"GDS")
            postrun = run / "dataset_quality_gates_s8p_physical_feature" / "selected_s8p_hfss_postrun_validation"
            postrun.mkdir()
            (postrun / "hfss_exported.s8p").write_text("# GHz S RI R 50\n", encoding="utf-8")

            tarball = root / "dataset500_hfss_assets.tar.gz"
            status = package.main([str(run), "--out", str(tarball), "--include-hfss-validation-assets"])

            self.assertEqual(status, 0)
            inventory = json.loads((root / "dataset500_hfss_assets.tar.gz.inventory.json").read_text(encoding="utf-8"))
            self.assertTrue(inventory["include_hfss_validation_assets"])
            self.assertGreaterEqual(inventory["category_counts"]["hfss_validation_asset_files"], 4)
            self.assertEqual(inventory["category_counts"]["hfss_validation_touchstone_files"], 1)
            self.assertEqual(inventory["category_counts"]["hfss_validation_script_files"], 2)
            with tarfile.open(tarball, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn(
                "dataset500/dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_aedt_scripts/run_generated_hfss_s8p_scripts.commands.ps1",
                names,
            )
            self.assertIn(
                "dataset500/dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_postrun_validation/hfss_exported.s8p",
                names,
            )

    def test_refuses_empty_transfer_file_before_creating_tarball(self) -> None:
        package = _load_package_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = root / "runs" / "dataset500"
            (run / "evaluations" / "abc" / "emx").mkdir(parents=True)
            (run / "dataset_manifest.json").write_text("{}", encoding="utf-8")
            (run / "dataset_rows.csv").write_text("ok\ntrue\n", encoding="utf-8")
            (run / "evaluations" / "abc" / "summary.json").write_text("{}", encoding="utf-8")
            (run / "evaluations" / "abc" / "emx" / "emx.s4p").write_text("", encoding="utf-8")

            tarball = root / "dataset500_minimal.tar.gz"

            with self.assertRaises(SystemExit) as raised:
                package.main([str(run), "--out", str(tarball)])

            self.assertIn("Refusing to package empty transfer files", str(raised.exception))
            self.assertIn("evaluations/abc/emx/emx.s4p", str(raised.exception))
            self.assertFalse(tarball.exists())
            self.assertFalse((root / "dataset500_minimal.tar.gz.inventory.json").exists())
