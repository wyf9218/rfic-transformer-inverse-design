from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_readiness_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_248k_launch_readiness.py"
    spec = importlib.util.spec_from_file_location("audit_248k_launch_readiness_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_real_path_config(root: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    template = repo_root / "configs" / "mars_dataset_248k_template.yaml"
    text = template.read_text(encoding="utf-8")
    emx_binary = root / "tools" / "emx"
    process_file = root / "proc" / "procfile.proc"
    cadence_root = root / "cadence" / "IC"
    cds_lib = root / "pdk" / "cds.lib"
    layer_map = root / "pdk" / "layers.layermap"
    for path in (emx_binary, process_file, cds_lib, layer_map):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    cadence_root.mkdir(parents=True, exist_ok=True)
    text = text.replace("/REPLACE/WITH/REAL/EMX/BINARY", str(emx_binary))
    text = text.replace("/REPLACE/WITH/REAL/TSMC65_OR_EMX_PROC_FILE.proc", str(process_file))
    text = text.replace("/REPLACE/WITH/REAL/CADENCE/IC/ROOT", str(cadence_root))
    text = text.replace("/REPLACE/WITH/REAL/PDK/cds.lib", str(cds_lib))
    text = text.replace("/REPLACE/WITH/REAL/PDK/layers.layermap", str(layer_map))
    text = text.replace("REPLACE_WITH_REAL_TECH_LIB_NAME", "TECH")
    config = root / "mars_dataset_248k_ready.yaml"
    config.write_text(text, encoding="utf-8")
    return config


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_compare_summary(
    path: Path,
    *,
    no_extrapolation_status: str = "PASS",
    emx_source: Path | None = None,
    hfss_source: Path | None = None,
    metric_max_percent_error: float = 0.0,
    criterion_max_percent_error: float = 5.0,
) -> None:
    _write_json(
        path,
        {
            "overall_status": "PASS",
            "criterion": {"max_percent_error": criterion_max_percent_error},
            "emx_source": str(emx_source) if emx_source is not None else "",
            "hfss_ads_source": str(hfss_source) if hfss_source is not None else "",
            "frequency_window_hz": {"min": 5.0e9, "max": 50.0e9, "count": 451},
            "frequency_grid_checks": {
                "ADS no-extrapolation coverage": {"status": no_extrapolation_status, "detail": "ok"},
                "expected frequency points": {"status": "PASS", "detail": "ok"},
                "expected frequency step": {"status": "PASS", "detail": "ok"},
                "matching HFSS/ADS frequency grid": {"status": "PASS", "detail": "ok"},
            },
            "metrics": {
                name: {"status": "PASS", "max_percent_error": metric_max_percent_error}
                for name in ("k", "qp", "qs", "lp_nh", "ls_nh")
            },
        },
    )


def _write_ready_evidence(root: Path, readiness) -> tuple[Path, Path, Path]:
    preflight = root / "248k_preflight.json"
    quality = root / "wideband" / "dataset_quality_gates_20260613" / "dataset_quality_gates_summary.json"
    hfss = root / "wideband" / "hfss_emx_validation_batch_20260613" / "hfss_emx_validation_batch_summary.json"
    compare_summaries = []
    emx_paths = []
    hfss_paths = []
    for index in range(8):
        summary_path = hfss.parent / "comparisons" / f"sample_{index + 1:02d}" / "emx_hfss_ads_comparison_summary.json"
        emx_path = root / "emx" / f"sample_{index + 1:02d}.s4p"
        hfss_path = root / "hfss" / f"sample_{index + 1:02d}.s4p"
        emx_path.parent.mkdir(parents=True, exist_ok=True)
        hfss_path.parent.mkdir(parents=True, exist_ok=True)
        emx_path.write_text("emx\n", encoding="utf-8")
        hfss_path.write_text("hfss\n", encoding="utf-8")
        _write_compare_summary(summary_path, emx_source=emx_path, hfss_source=hfss_path)
        compare_summaries.append(summary_path)
        emx_paths.append(emx_path)
        hfss_paths.append(hfss_path)
    _write_json(preflight, {"overall_status": "PASS"})
    _write_json(
        quality,
        {
            "overall_status": "PASS",
            "steps": [{"name": name, "status": "PASS"} for name in readiness.REQUIRED_QUALITY_STEPS],
        },
    )
    _write_json(
        hfss,
        {
            "overall_status": "PASS",
            "sample_count": 8,
            "status_counts": {"PASS": 8},
            "records": [
                {
                    "rank": index + 1,
                    "evaluation": f"sample_{index + 1:02d}",
                    "status": "PASS",
                    "no_extrapolation_status": "PASS",
                    "summary_path": str(compare_summaries[index]),
                    "emx_path": str(emx_paths[index]),
                    "hfss_path": str(hfss_paths[index]),
                }
                for index in range(8)
            ],
            "arguments": {
                "require_all_present": True,
                "require_all_pass": True,
                "compare_start_ghz": 5.0,
                "compare_stop_ghz": 50.0,
                "expected_frequency_step_ghz": 0.1,
                "expected_frequency_points": 451,
                "min_frequency_points": 451,
                "max_percent_error": 5.0,
            },
        },
    )
    return preflight, quality, hfss


def _write_filled_target_envelope_configs(root: Path) -> tuple[Path, Path]:
    config_dir = root / "target_envelopes"
    zin_config = config_dir / "zin_target_envelope_filled_20260614.json"
    response_config = config_dir / "response_target_envelopes_filled_20260614.json"
    _write_json(
        zin_config,
        {
            "schema": "zin_target_envelope.v1",
            "status": "PROJECT_FILLED_FOR_TEST",
            "zin_target_envelope": {
                "real_min_ohm": 5.0,
                "real_max_ohm": 120.0,
                "imag_min_ohm": -80.0,
                "imag_max_ohm": 80.0,
                "min_area_fraction": 0.25,
                "min_occupied_2d_bins": 4,
                "max_outside_fraction": 0.1,
                "target_count_per_bin": 1,
            },
        },
    )
    _write_json(
        response_config,
        {
            "schema": "response_target_envelopes.v1",
            "status": "PROJECT_FILLED_FOR_TEST",
            "response_target_envelopes": {
                "target_count_per_bin": 1,
                "k_qp": {
                    "k_min": -0.9,
                    "k_max": -0.1,
                    "qp_min": 1.0,
                    "qp_max": 30.0,
                    "min_area_fraction": 0.25,
                    "min_occupied_2d_bins": 4,
                    "max_outside_fraction": 0.1,
                },
                "lp_ls": {
                    "lp_min_nh": 0.05,
                    "lp_max_nh": 5.0,
                    "ls_min_nh": 0.05,
                    "ls_max_nh": 5.0,
                    "min_area_fraction": 0.25,
                    "min_occupied_2d_bins": 4,
                    "max_outside_fraction": 0.1,
                },
            },
        },
    )
    return zin_config, response_config


def _target_envelope_args(root: Path) -> list[str]:
    zin_config, response_config = _write_filled_target_envelope_configs(root)
    return [
        "--zin-target-envelope-config",
        str(zin_config),
        "--response-target-envelope-config",
        str(response_config),
    ]


class Audit248kLaunchReadinessScriptTest(TransformerToolboxTestBase):
    def test_passes_only_when_all_prelaunch_evidence_is_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item["status"] for item in summary["checks"]}
            self.assertEqual(checks["sampled HFSS/EMX batch gate"], "PASS")
            self.assertEqual(checks["Zin target-envelope config"], "PASS")
            self.assertEqual(checks["response target-envelope config"], "PASS")
            self.assertTrue((root / "readiness" / "248k_launch_commands.sh").exists())

    def test_template_target_envelope_configs_keep_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Zin target-envelope config"]["status"], "NOT_READY")
            self.assertEqual(checks["response target-envelope config"]["status"], "NOT_READY")
            self.assertIn("template-only", checks["Zin target-envelope config"]["detail"])

    def test_missing_hfss_batch_keeps_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)
            hfss.unlink()

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item["status"] for item in summary["checks"]}
            self.assertEqual(checks["sampled HFSS/EMX batch gate"], "NOT_READY")

    def test_missing_batch_no_extrapolation_evidence_keeps_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)
            data = json.loads(hfss.read_text(encoding="utf-8"))
            data["records"][0]["no_extrapolation_status"] = "MISSING"
            _write_json(hfss, data)

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["sampled HFSS/EMX batch gate"]["status"], "NOT_READY")
            self.assertIn("no_extrapolation_failures", checks["sampled HFSS/EMX batch gate"]["detail"])

    def test_bad_per_sample_compare_summary_keeps_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)
            data = json.loads(hfss.read_text(encoding="utf-8"))
            first_summary = Path(data["records"][0]["summary_path"])
            _write_compare_summary(
                first_summary,
                no_extrapolation_status="FAIL",
                emx_source=Path(data["records"][0]["emx_path"]),
                hfss_source=Path(data["records"][0]["hfss_path"]),
            )

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["sampled HFSS/EMX batch gate"]["status"], "NOT_READY")
            self.assertIn("ADS no-extrapolation coverage=FAIL", checks["sampled HFSS/EMX batch gate"]["detail"])

    def test_per_sample_metric_error_over_gate_keeps_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)
            data = json.loads(hfss.read_text(encoding="utf-8"))
            first_record = data["records"][0]
            _write_compare_summary(
                Path(first_record["summary_path"]),
                emx_source=Path(first_record["emx_path"]),
                hfss_source=Path(first_record["hfss_path"]),
                metric_max_percent_error=6.0,
            )

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertIn("metric_k_max_percent_error=6.0", checks["sampled HFSS/EMX batch gate"]["detail"])

    def test_per_sample_compare_criterion_over_gate_keeps_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)
            data = json.loads(hfss.read_text(encoding="utf-8"))
            first_record = data["records"][0]
            _write_compare_summary(
                Path(first_record["summary_path"]),
                emx_source=Path(first_record["emx_path"]),
                hfss_source=Path(first_record["hfss_path"]),
                criterion_max_percent_error=10.0,
            )

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertIn("criterion_max_percent_error=10.0", checks["sampled HFSS/EMX batch gate"]["detail"])

    def test_mismatched_per_sample_source_path_keeps_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _write_real_path_config(root)
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)
            data = json.loads(hfss.read_text(encoding="utf-8"))
            first_record = data["records"][0]
            other_emx = root / "emx" / "wrong_sample.s4p"
            other_emx.write_text("wrong\n", encoding="utf-8")
            _write_compare_summary(
                Path(first_record["summary_path"]),
                emx_source=other_emx,
                hfss_source=Path(first_record["hfss_path"]),
            )

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertIn("emx_source_mismatch", checks["sampled HFSS/EMX batch gate"]["detail"])

    def test_placeholder_paths_keep_launch_not_ready(self) -> None:
        readiness = _load_readiness_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = Path(__file__).resolve().parents[1]
            config = repo_root / "configs" / "mars_dataset_248k_template.yaml"
            preflight, quality, hfss = _write_ready_evidence(root, readiness)
            target_args = _target_envelope_args(root)

            status = readiness.main(
                [
                    "--production-config",
                    str(config),
                    "--production-preflight-summary",
                    str(preflight),
                    "--wideband-quality-summary",
                    str(quality),
                    "--hfss-batch-summary",
                    str(hfss),
                    *target_args,
                    "--out-dir",
                    str(root / "readiness"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "readiness" / "248k_launch_readiness_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "NOT_READY")
            checks = {item["name"]: item["status"] for item in summary["checks"]}
            self.assertEqual(checks["248k EMX/Cadence paths"], "NOT_READY")
