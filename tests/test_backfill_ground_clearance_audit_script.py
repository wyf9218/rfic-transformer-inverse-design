from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_backfill_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_ground_clearance_audit.py"
    spec = importlib.util.spec_from_file_location("backfill_ground_clearance_audit_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BackfillGroundClearanceAuditScriptTest(TransformerToolboxTestBase):
    def test_backfills_clearance_audit_from_dataset_rows(self) -> None:
        script = _load_backfill_module()
        cfg = default_run_config("1t1t")
        cfg = replace(cfg, emx=replace(cfg.emx, port_mode="single_ended_shield_grounded"))

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            config_path.write_text("emx:\n  port_mode: single_ended_shield_grounded\n", encoding="utf-8")
            rows_path = root / "dataset_rows.csv"
            flat = cfg.bounds.midpoint().flat_dict()
            with rows_path.open("w", newline="", encoding="utf-8") as handle:
                fieldnames = ["evaluation", *[f"geom__{name}" for name in flat]]
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow({"evaluation": "midpoint", **{f"geom__{name}": value for name, value in flat.items()}})

            status = script.main([str(root), "--config", str(config_path), "--expected-count", "1"])

            self.assertEqual(status, 0)
            audit = json.loads((root / "final500_ground_clearance_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["candidate_count"], 1)
            self.assertEqual(audit["pass_count"], 1)
            self.assertEqual(audit["missing_or_other_count"], 0)
            self.assertEqual(audit["records"][0]["status"], "pass_signal_to_shield_clearance")
            self.assertTrue((root / "ground_clearance_reexport" / "evaluations").is_dir())
