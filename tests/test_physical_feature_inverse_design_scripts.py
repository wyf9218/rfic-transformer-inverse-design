from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_builder_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_physical_feature_inverse_training_table.py"
    spec = importlib.util.spec_from_file_location("build_physical_feature_inverse_training_table_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_predictor_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "predict_geometry_from_physical_features.py"
    spec = importlib.util.spec_from_file_location("predict_geometry_from_physical_features_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_quality_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_inverse_model_quality.py"
    spec = importlib.util.spec_from_file_location("audit_physical_feature_inverse_model_quality_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_training_model_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "train_physical_feature_inverse_model.py"
    spec = importlib.util.spec_from_file_location("train_physical_feature_inverse_model_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_saved_model_predictor_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "predict_geometry_with_saved_inverse_model.py"
    spec = importlib.util.spec_from_file_location("predict_geometry_with_saved_inverse_model_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_dataset(root: Path) -> None:
    rows = []
    for idx in range(16):
        w = 1.0 + 0.5 * idx
        s = 2.0 + 0.1 * (idx % 5)
        rows.append(
            {
                "evaluation": f"eval_{idx}",
                "ok": "true",
                "touchstone_path": f"evaluations/eval_{idx}/emx/emx.s8p",
                "lp_nh_center": 0.4 + 0.08 * w,
                "ls_nh_center": 0.6 + 0.05 * w + 0.02 * s,
                "qp_center": 8.0 + 0.2 * w,
                "qs_center": 7.0 + 0.1 * s,
                "q_center": min(8.0 + 0.2 * w, 7.0 + 0.1 * s),
                "k_center": 0.35 + 0.015 * idx,
                "geom__w_um": w,
                "geom__s_um": s,
            }
        )
    _write_csv(root / "dataset_rows.csv", rows)


def _write_config_geometry_dataset(root: Path, config_path: Path | None = None) -> None:
    cfg = load_run_config(config_path) if config_path is not None else load_run_config(None)
    adapter = TransformerOptimizationAdapter(cfg.bounds)
    midpoint = adapter.to_vector(cfg.bounds.midpoint())
    field_order = list(adapter.field_order())
    rows = []
    for idx in range(16):
        vector = midpoint.copy()
        vector[0] += idx * 0.5
        vector[1] += idx * 0.25
        vector[2] -= idx * 0.2
        vector[8] += (idx - 8) * 0.1
        row = {
            "evaluation": f"eval_{idx}",
            "ok": "true",
            "touchstone_path": f"evaluations/eval_{idx}/emx/emx.s8p",
            "lp_nh_center": 0.55 + 0.02 * idx,
            "ls_nh_center": 0.75 + 0.015 * idx,
            "qp_center": 10.0 + 0.1 * idx,
            "qs_center": 9.0 + 0.08 * idx,
            "q_center": min(10.0 + 0.1 * idx, 9.0 + 0.08 * idx),
            "k_center": 0.4 + 0.005 * idx,
        }
        for field, value in zip(field_order, vector, strict=True):
            row[f"geom__{field}"] = float(value)
        rows.append(row)
    _write_csv(root / "dataset_rows.csv", rows)


def _write_test_s8p_config(root: Path) -> Path:
    config_path = root / "s8p_config.yaml"
    template = Path(__file__).resolve().parents[1] / "configs" / "mars_s8p_physical_feature_500_template.yaml"
    proc_file = (
        Path(__file__).resolve().parents[1]
        / "rfic_transformer_inverse_design"
        / "process"
        / "assets"
        / "proc"
        / "default_typical.proc"
    )
    replacements = {
        "/REPLACE/WITH/REAL/EMX/BINARY": str(root / "emx"),
        "/REPLACE/WITH/REAL/TSMC65_OR_EMX_PROC_FILE.proc": str(proc_file),
        "/REPLACE/WITH/REAL/CADENCE/IC/ROOT": str(root / "cadence"),
        "/REPLACE/WITH/REAL/PDK/cds.lib": str(root / "cds.lib"),
        "REPLACE_WITH_REAL_TECH_LIB_NAME": "techlib",
        "/REPLACE/WITH/REAL/PDK/layers.layermap": str(root / "layers.layermap"),
        "TODO_CONFIRM_P001_TO_P008": "1,4:5,6",
        "TODO_P001_PRIMARY_TOP": "P001",
        "TODO_P002_LEFT_POWER_TOP": "P002",
        "TODO_P003_LEFT_POWER_BOTTOM": "P003",
        "TODO_P004_PRIMARY_BOTTOM": "P004",
        "TODO_P005_SECONDARY_BOTTOM": "P005",
        "TODO_P006_SECONDARY_TOP": "P006",
        "TODO_P007_RIGHT_POWER_TOP": "P007",
        "TODO_P008_RIGHT_POWER_BOTTOM": "P008",
    }
    text = template.read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    config_path.write_text(text, encoding="utf-8")
    return config_path


class PhysicalFeatureInverseDesignScriptsTest(TransformerToolboxTestBase):
    def test_builds_inverse_training_table_from_physical_features_to_geometry(self) -> None:
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)

            status = builder.main([str(root), "--out-dir", str(root / "inverse_table")])

            self.assertEqual(status, 0)
            manifest = json.loads((root / "inverse_table" / "physical_feature_inverse_training_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["overall_status"], "PASS")
            self.assertEqual(manifest["training_count"], 16)
            self.assertIn("input__lp_nh_center", manifest["input_columns"])
            self.assertIn("input__q_center", manifest["input_columns"])
            self.assertEqual(manifest["input_feature_contract"]["zin_columns"], [])
            self.assertIn("lp_nh_center", manifest["input_feature_contract"]["lp_columns"])
            self.assertEqual(manifest["input_feature_contract"]["q_columns"], ["q_center"])
            self.assertIn("k_center", manifest["input_feature_contract"]["k_columns"])
            self.assertIn("geom__w_um", manifest["geometry_columns"])
            with (root / "inverse_table" / "physical_feature_inverse_training_table.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 16)
            self.assertIn("input__k_center", rows[0])
            self.assertIn("geom__s_um", rows[0])

    def test_missing_physical_features_fail_without_training_rows(self) -> None:
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_csv(root / "dataset_rows.csv", [{"ok": "true", "geom__w_um": 1.0, "geom__s_um": 2.0, "touchstone_path": "a.s8p"}])

            status = builder.main([str(root), "--out-dir", str(root / "inverse_table"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            manifest = json.loads((root / "inverse_table" / "physical_feature_inverse_training_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["overall_status"], "FAIL")
            self.assertEqual(manifest["training_count"], 0)

    def test_explicit_geometry_columns_ignore_sparse_legacy_columns(self) -> None:
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            rows = []
            with (root / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
                for idx, row in enumerate(csv.DictReader(handle)):
                    row["geom__legacy_sparse_um"] = "" if idx < 8 else str(100.0 + idx)
                    rows.append(row)
            _write_csv(root / "dataset_rows.csv", rows)

            status = builder.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "inverse_table"),
                    "--geometry-columns",
                    "geom__w_um,geom__s_um",
                ]
            )

            self.assertEqual(status, 0)
            manifest = json.loads((root / "inverse_table" / "physical_feature_inverse_training_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["overall_status"], "PASS")
            self.assertEqual(manifest["training_count"], 16)
            self.assertEqual(manifest["geometry_columns"], ["geom__w_um", "geom__s_um"])
            self.assertEqual(manifest["geometry_contract"]["source"], "explicit_geometry_columns")

    def test_inverse_training_table_rejects_zin_input_columns(self) -> None:
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            rows = []
            with (root / "dataset_rows.csv").open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    row["zin_real_center_ohm"] = "50.0"
                    row["zin_imag_center_ohm"] = "120.0"
                    rows.append(row)
            _write_csv(root / "dataset_rows.csv", rows)

            status = builder.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "inverse_table"),
                    "--feature-columns",
                    "zin_real_center_ohm,zin_imag_center_ohm,k_center",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            manifest = json.loads((root / "inverse_table" / "physical_feature_inverse_training_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["overall_status"], "FAIL")
            checks = {item["name"]: item for item in manifest["checks"]}
            self.assertFalse(checks["inverse_inputs_do_not_use_zin"]["pass"])
            self.assertIn("zin_real_center_ohm", manifest["input_feature_contract"]["zin_columns"])

    def test_predicts_candidate_geometry_from_target_physical_features(self) -> None:
        builder = _load_builder_module()
        predictor = _load_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)
            training_csv = root / "inverse_table" / "physical_feature_inverse_training_table.csv"

            status = predictor.main(
                [
                    "--training-csv",
                    str(training_csv),
                    "--out-dir",
                    str(root / "prediction"),
                    "--target",
                    "lp_nh_center=0.76",
                    "--target",
                    "ls_nh_center=0.84",
                    "--target",
                    "q_center=7.2",
                    "--target",
                    "k_center=0.45",
                    "--candidate-count",
                    "3",
                    "--k-neighbors",
                    "4",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prediction" / "physical_feature_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["candidate_count"], 3)
            self.assertEqual(summary["input_feature_contract"]["zin_columns"], [])
            with (root / "prediction" / "physical_feature_inverse_geometry_candidates.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertIn("geom__w_um", rows[0])
            self.assertIn("geom__s_um", rows[0])
            self.assertEqual(rows[0]["inverse_prediction_source"], "knn_idw_weighted_inverse_prediction")

    def test_prediction_with_config_proves_candidates_can_rebuild_geometry(self) -> None:
        builder = _load_builder_module()
        predictor = _load_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = _write_test_s8p_config(root)
            _write_config_geometry_dataset(root, config_path)
            self.assertEqual(
                builder.main([str(root), "--out-dir", str(root / "inverse_table"), "--config", str(config_path)]),
                0,
            )
            manifest = json.loads((root / "inverse_table" / "physical_feature_inverse_training_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["overall_status"], "PASS")
            self.assertEqual(len(manifest["geometry_columns"]), len(manifest["geometry_contract"]["field_order"]))

            status = predictor.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "prediction"),
                    "--target",
                    "lp_nh_center=0.7",
                    "--target",
                    "ls_nh_center=0.85",
                    "--target",
                    "q_center=9.6",
                    "--target",
                    "k_center=0.44",
                    "--candidate-count",
                    "4",
                    "--k-neighbors",
                    "4",
                    "--config",
                    str(root / "default_config.yaml"),
                ]
            )

            self.assertEqual(status, 2)

            # The path above intentionally does not exist; now prove the same data
            # passes when the predictor uses a real config file.
            status = predictor.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "prediction_pass"),
                    "--target",
                    "lp_nh_center=0.7",
                    "--target",
                    "ls_nh_center=0.85",
                    "--target",
                    "q_center=9.6",
                    "--target",
                    "k_center=0.44",
                    "--candidate-count",
                    "4",
                    "--k-neighbors",
                    "4",
                    "--config",
                    str(config_path),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prediction_pass" / "physical_feature_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertTrue(checks["inverse_geometry_candidate_fields_match_config"]["pass"])
            self.assertTrue(checks["inverse_geometry_candidates_rebuild_from_config"]["pass"])
            self.assertEqual(summary["candidate_geometry_contract"]["valid_candidate_count"], 4)

    def test_prediction_requires_all_target_features(self) -> None:
        builder = _load_builder_module()
        predictor = _load_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)

            status = predictor.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "prediction"),
                    "--target",
                    "lp_nh_center=0.76",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prediction" / "physical_feature_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)

    def test_prediction_rejects_zin_input_columns(self) -> None:
        predictor = _load_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            training_csv = root / "training.csv"
            _write_csv(
                training_csv,
                [
                    {
                        "input__zin_real_center_ohm": 10.0,
                        "input__zin_imag_center_ohm": 100.0,
                        "geom__w_um": 1.0,
                        "geom__s_um": 2.0,
                    },
                    {
                        "input__zin_real_center_ohm": 20.0,
                        "input__zin_imag_center_ohm": 120.0,
                        "geom__w_um": 2.0,
                        "geom__s_um": 3.0,
                    },
                ],
            )

            status = predictor.main(
                [
                    "--training-csv",
                    str(training_csv),
                    "--out-dir",
                    str(root / "prediction"),
                    "--target",
                    "zin_real_center_ohm=15",
                    "--target",
                    "zin_imag_center_ohm=110",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "prediction" / "physical_feature_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["inverse_inputs_do_not_use_zin"]["pass"])
            self.assertFalse(checks["inverse_inputs_include_lp_ls_q_k"]["pass"])

    def test_audits_inverse_model_quality_from_physical_feature_training_table(self) -> None:
        builder = _load_builder_module()
        quality = _load_quality_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)

            status = quality.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "inverse_quality"),
                    "--k-neighbors",
                    "4",
                    "--max-normalized-mae",
                    "1.0",
                    "--max-normalized-rmse",
                    "1.0",
                    "--max-normalized-max-abs-error",
                    "1.5",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "inverse_quality" / "physical_feature_inverse_model_quality_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["input_feature_contract"]["zin_columns"], [])
            self.assertIn("geom__w_um", summary["quality_summary"]["per_geometry"])
            with (root / "inverse_quality" / "physical_feature_inverse_model_cv_predictions.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 16)
            self.assertIn("pred__w_um", rows[0])

    def test_inverse_model_quality_rejects_zin_inputs(self) -> None:
        quality = _load_quality_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            training_csv = root / "training.csv"
            _write_csv(
                training_csv,
                [
                    {
                        "input__zin_real_center_ohm": 10.0,
                        "input__zin_imag_center_ohm": 100.0,
                        "geom__w_um": 1.0,
                        "geom__s_um": 2.0,
                    },
                    {
                        "input__zin_real_center_ohm": 20.0,
                        "input__zin_imag_center_ohm": 120.0,
                        "geom__w_um": 2.0,
                        "geom__s_um": 3.0,
                    },
                ],
            )

            status = quality.main(["--training-csv", str(training_csv), "--out-dir", str(root / "quality"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "quality" / "physical_feature_inverse_model_quality_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["inverse_inputs_do_not_use_zin"]["pass"])
            self.assertFalse(checks["inverse_inputs_include_lp_ls_q_k"]["pass"])

    def test_trains_saved_inverse_model_from_physical_features_to_geometry(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)

            status = trainer.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "saved_model"),
                    "--target",
                    "lp_nh_center=0.76",
                    "--target",
                    "ls_nh_center=0.84",
                    "--target",
                    "q_center=7.2",
                    "--target",
                    "k_center=0.45",
                    "--max-normalized-mae",
                    "2.0",
                    "--max-normalized-rmse",
                    "2.0",
                    "--max-normalized-max-abs-error",
                    "3.0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_model" / "physical_feature_inverse_model_training_summary.json").read_text(encoding="utf-8"))
            model = json.loads((root / "saved_model" / "physical_feature_inverse_model.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["method"], "standardized_polynomial_ridge_regression")
            self.assertEqual(model["method"], "standardized_polynomial_ridge_regression")
            self.assertIn("input__lp_nh_center", summary["input_columns"])
            self.assertEqual(summary["input_feature_contract"]["zin_columns"], [])
            self.assertTrue(model["coefficients"])
            self.assertTrue(model["terms"])
            self.assertIn("geom__w_um", summary["quality_summary"]["per_geometry"])
            with (root / "saved_model" / "physical_feature_inverse_model_target_predictions.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["candidate_id"], "saved_inverse_target_000_candidate_001")
            self.assertEqual(rows[0]["candidate_rank"], "1")
            self.assertIn("geom__w_um", rows[0])
            self.assertIn("geom__s_um", rows[0])

    def test_saved_inverse_model_rejects_zin_inputs(self) -> None:
        trainer = _load_training_model_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            training_csv = root / "training.csv"
            _write_csv(
                training_csv,
                [
                    {
                        "input__zin_real_center_ohm": 10.0,
                        "input__zin_imag_center_ohm": 100.0,
                        "geom__w_um": 1.0,
                        "geom__s_um": 2.0,
                    },
                    {
                        "input__zin_real_center_ohm": 20.0,
                        "input__zin_imag_center_ohm": 120.0,
                        "geom__w_um": 2.0,
                        "geom__s_um": 3.0,
                    },
                ],
            )

            status = trainer.main(["--training-csv", str(training_csv), "--out-dir", str(root / "saved_model"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_model" / "physical_feature_inverse_model_training_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["inverse_inputs_do_not_use_zin"]["pass"])
            self.assertFalse(checks["inverse_inputs_include_lp_ls_q_k"]["pass"])

    def test_saved_inverse_model_rejects_target_outside_training_feature_envelope(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)

            status = trainer.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "saved_model"),
                    "--target",
                    "lp_nh_center=99.0",
                    "--target",
                    "ls_nh_center=0.84",
                    "--target",
                    "q_center=7.2",
                    "--target",
                    "k_center=0.45",
                    "--max-normalized-mae",
                    "2.0",
                    "--max-normalized-rmse",
                    "2.0",
                    "--max-normalized-max-abs-error",
                    "3.0",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_model" / "physical_feature_inverse_model_training_summary.json").read_text(encoding="utf-8"))
            model = json.loads((root / "saved_model" / "physical_feature_inverse_model.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertIn("input__lp_nh_center", model["input_domain"]["per_feature"])
            self.assertEqual(summary["target_prediction_count"], 0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertTrue(checks["target_feature_training_envelope_present"]["pass"])
            self.assertFalse(checks["target_features_inside_training_envelope"]["pass"])
            self.assertEqual(summary["target_feature_envelope"]["out_of_range"][0]["feature"], "input__lp_nh_center")

    def test_saved_inverse_model_predicts_candidate_geometry_csv(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        predictor = _load_saved_model_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)
            self.assertEqual(
                trainer.main(
                    [
                        "--training-csv",
                        str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                        "--out-dir",
                        str(root / "saved_model"),
                        "--max-normalized-mae",
                        "2.0",
                        "--max-normalized-rmse",
                        "2.0",
                        "--max-normalized-max-abs-error",
                        "3.0",
                    ]
                ),
                0,
            )

            status = predictor.main(
                [
                    "--model-json",
                    str(root / "saved_model" / "physical_feature_inverse_model.json"),
                    "--out-dir",
                    str(root / "saved_prediction"),
                    "--target",
                    "lp_nh_center=0.76",
                    "--target",
                    "ls_nh_center=0.84",
                    "--target",
                    "q_center=7.2",
                    "--target",
                    "k_center=0.45",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_prediction" / "physical_feature_saved_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["input_feature_contract"]["zin_columns"], [])
            self.assertEqual(summary["candidate_count"], 1)
            with (root / "saved_prediction" / "physical_feature_saved_inverse_geometry_candidates.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["inverse_prediction_source"], "saved_polynomial_ridge_baseline")
            self.assertIn("geom__w_um", rows[0])
            self.assertIn("geom__s_um", rows[0])

    def test_training_target_json_rejects_zin_fields(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)
            target_json = root / "target_with_zin.json"
            target_json.write_text(
                json.dumps(
                    {
                        "lp_nh_center": 0.76,
                        "ls_nh_center": 0.84,
                        "q_center": 7.2,
                        "k_center": 0.45,
                        "zin_real_center_ohm": 10.0,
                    }
                ),
                encoding="utf-8",
            )

            status = trainer.main(
                [
                    "--training-csv",
                    str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                    "--out-dir",
                    str(root / "saved_model"),
                    "--target-json",
                    str(target_json),
                    "--max-normalized-mae",
                    "2.0",
                    "--max-normalized-rmse",
                    "2.0",
                    "--max-normalized-max-abs-error",
                    "3.0",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_model" / "physical_feature_inverse_model_training_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["target_prediction_count"], 0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["target_features_parse"]["pass"])
            self.assertIn("zin_real_center_ohm", checks["target_features_parse"]["detail"])

    def test_saved_inverse_model_predictor_rejects_zin_target_json_fields(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        predictor = _load_saved_model_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)
            self.assertEqual(
                trainer.main(
                    [
                        "--training-csv",
                        str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                        "--out-dir",
                        str(root / "saved_model"),
                        "--max-normalized-mae",
                        "2.0",
                        "--max-normalized-rmse",
                        "2.0",
                        "--max-normalized-max-abs-error",
                        "3.0",
                    ]
                ),
                0,
            )
            target_json = root / "target_with_zin.json"
            target_json.write_text(
                json.dumps(
                    {
                        "lp_nh_center": 0.76,
                        "ls_nh_center": 0.84,
                        "q_center": 7.2,
                        "k_center": 0.45,
                        "zin_imag_center_ohm": 120.0,
                    }
                ),
                encoding="utf-8",
            )

            status = predictor.main(
                [
                    "--model-json",
                    str(root / "saved_model" / "physical_feature_inverse_model.json"),
                    "--out-dir",
                    str(root / "saved_prediction"),
                    "--target-json",
                    str(target_json),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_prediction" / "physical_feature_saved_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["target_features_parse"]["pass"])
            self.assertIn("zin_imag_center_ohm", checks["target_features_parse"]["detail"])

    def test_saved_inverse_model_predictor_rejects_missing_target_json_feature(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        predictor = _load_saved_model_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)
            self.assertEqual(
                trainer.main(
                    [
                        "--training-csv",
                        str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                        "--out-dir",
                        str(root / "saved_model"),
                        "--max-normalized-mae",
                        "2.0",
                        "--max-normalized-rmse",
                        "2.0",
                        "--max-normalized-max-abs-error",
                        "3.0",
                    ]
                ),
                0,
            )
            target_json = root / "target_missing_q.json"
            target_json.write_text(
                json.dumps(
                    {
                        "lp_nh_center": 0.76,
                        "ls_nh_center": 0.84,
                        "k_center": 0.45,
                    }
                ),
                encoding="utf-8",
            )

            status = predictor.main(
                [
                    "--model-json",
                    str(root / "saved_model" / "physical_feature_inverse_model.json"),
                    "--out-dir",
                    str(root / "saved_prediction"),
                    "--target-json",
                    str(target_json),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_prediction" / "physical_feature_saved_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["target_features_parse"]["pass"])
            self.assertIn("input__q_center", checks["target_features_parse"]["detail"])

    def test_saved_inverse_model_predictor_rejects_zin_model(self) -> None:
        predictor = _load_saved_model_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_path = root / "zin_model.json"
            model_path.write_text(
                json.dumps(
                    {
                        "method": "standardized_polynomial_ridge_regression",
                        "input_columns": ["input__zin_real_center_ohm", "input__zin_imag_center_ohm"],
                        "geometry_columns": ["geom__w_um"],
                        "input_mean": [10.0, 100.0],
                        "input_scale": [1.0, 1.0],
                        "terms": [{"name": "constant", "powers": [0, 0]}],
                        "coefficients": [[1.0]],
                    }
                ),
                encoding="utf-8",
            )

            status = predictor.main(
                [
                    "--model-json",
                    str(model_path),
                    "--out-dir",
                    str(root / "saved_prediction"),
                    "--target",
                    "zin_real_center_ohm=10",
                    "--target",
                    "zin_imag_center_ohm=100",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_prediction" / "physical_feature_saved_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertFalse(checks["saved_model_inputs_do_not_use_zin"]["pass"])
            self.assertFalse(checks["saved_model_inputs_include_lp_ls_q_k"]["pass"])

    def test_saved_inverse_model_predictor_rejects_target_outside_training_feature_envelope(self) -> None:
        builder = _load_builder_module()
        trainer = _load_training_model_module()
        predictor = _load_saved_model_predictor_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_dataset(root)
            self.assertEqual(builder.main([str(root), "--out-dir", str(root / "inverse_table")]), 0)
            self.assertEqual(
                trainer.main(
                    [
                        "--training-csv",
                        str(root / "inverse_table" / "physical_feature_inverse_training_table.csv"),
                        "--out-dir",
                        str(root / "saved_model"),
                        "--max-normalized-mae",
                        "2.0",
                        "--max-normalized-rmse",
                        "2.0",
                        "--max-normalized-max-abs-error",
                        "3.0",
                    ]
                ),
                0,
            )

            status = predictor.main(
                [
                    "--model-json",
                    str(root / "saved_model" / "physical_feature_inverse_model.json"),
                    "--out-dir",
                    str(root / "saved_prediction"),
                    "--target",
                    "lp_nh_center=99.0",
                    "--target",
                    "ls_nh_center=0.84",
                    "--target",
                    "q_center=7.2",
                    "--target",
                    "k_center=0.45",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "saved_prediction" / "physical_feature_saved_inverse_prediction_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["candidate_count"], 0)
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertTrue(checks["saved_model_target_feature_training_envelope_present"]["pass"])
            self.assertFalse(checks["saved_model_target_features_inside_training_envelope"]["pass"])
            self.assertEqual(summary["target_feature_envelope"]["out_of_range"][0]["feature"], "input__lp_nh_center")
