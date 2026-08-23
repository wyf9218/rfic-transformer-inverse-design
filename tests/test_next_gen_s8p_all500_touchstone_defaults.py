from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_script(name: str, rel: str):
    script_path = Path(__file__).resolve().parents[1] / rel
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NextGenS8pAll500TouchstoneDefaultsTest(TransformerToolboxTestBase):
    def test_next_gen_s8p_tools_default_to_all_500_touchstone_checks(self) -> None:
        summarizer = _load_script("summarize_next_gen_s8p_mars_run_defaults", "scripts/summarize_next_gen_s8p_mars_run.py")
        audit_s8p = _load_script("audit_s8p_physical_feature_dataset_defaults", "scripts/audit_s8p_physical_feature_dataset.py")
        quality = _load_script("run_dataset_quality_gates_defaults", "scripts/run_dataset_quality_gates.py")
        importer = _load_script("import_next_gen_s8p_mars_return_package_defaults", "scripts/import_next_gen_s8p_mars_return_package.py")
        discover = _load_script("discover_next_gen_s8p_mars_return_defaults", "scripts/discover_next_gen_s8p_mars_return.py")

        self.assertEqual(summarizer._parse_args(["--run-dir", "run"]).max_touchstone_checks, 500)
        self.assertEqual(audit_s8p._parse_args(["dataset"]).max_touchstone_checks, 500)
        quality_args = quality._parse_args(["dataset"])
        self.assertEqual(quality_args.s8p_max_touchstone_checks, 500)
        self.assertEqual(quality_args.max_touchstone_frequency_checks, 500)
        import_args = importer._parse_args(["return.tar.gz"])
        self.assertEqual(import_args.max_touchstone_checks, 500)
        self.assertEqual(import_args.max_touchstone_frequency_checks, 500)
        self.assertEqual(discover._parse_args([]).max_touchstone_checks, 500)

    def test_local_return_import_shell_defaults_to_all_500_touchstone_checks(self) -> None:
        script = Path(__file__).resolve().parents[2] / "NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh"
        text = script.read_text(encoding="utf-8")

        self.assertIn('"--max-touchstone-checks" "${MAX_TOUCHSTONE_CHECKS:-500}"', text)
        self.assertIn('"--max-touchstone-frequency-checks" "${MAX_TOUCHSTONE_FREQUENCY_CHECKS:-500}"', text)
