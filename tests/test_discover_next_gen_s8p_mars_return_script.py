from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "discover_next_gen_s8p_mars_return.py"
    spec = importlib.util.spec_from_file_location("discover_next_gen_s8p_mars_return_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_manifest(run_dir: Path) -> None:
    _write_json(
        run_dir / "dataset_manifest.json",
        {
            "port_mode": "single_ended_shield_grounded",
            "differential_port_pairs": [[1, 4], [5, 6]],
            "power_line_8port": {
                "enabled": True,
                "bridge_width_um": 10.0,
                "vertical_length_diameter_ratio": 1.5,
                "port_map": ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"],
                "ground_frame_width_um": 100.0,
                "ground_frame_policy": "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
            },
            "target_frequency": {
                "start_hz": 5.0e9,
                "stop_hz": 60.0e9,
                "step_hz": 5.0e8,
                "points": 111,
            },
        },
    )


def _write_parallel_summary(run_dir: Path, *, count: int, jobs: int) -> None:
    _write_json(
        run_dir / "parallel_candidate_queue_dataset_summary.json",
        {
            "overall_status": "PASS",
            "jobs_requested": jobs,
            "merged_row_count": count,
            "checks": [
                {"name": "requested_jobs_match_expected", "pass": True},
                {"name": "merged_count_matches_expected", "pass": True},
            ],
        },
    )


def _write_run(run_dir: Path, *, count: int = 2, jobs: int = 2, source: str = "emx") -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = ["evaluation", "ok", "touchstone_path"]
    with (run_dir / "dataset_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx in range(count):
            if source == "hfss":
                rel = f"evaluations/eval_{idx:03d}/hfss/HFSSDesign1.s8p"
                text = "! Touchstone file exported from HFSS 2025.1.0\n# GHz S RI R 50\n"
            else:
                rel = f"evaluations/eval_{idx:03d}/emx/emx.s8p"
                text = "! EMX generated 8-port synthetic placeholder for discovery tests\n# GHz S RI R 50\n"
            path = run_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="ascii")
            writer.writerow({"evaluation": f"eval_{idx:03d}", "ok": "true", "touchstone_path": rel})
    _write_manifest(run_dir)
    _write_parallel_summary(run_dir, count=count, jobs=jobs)


class DiscoverNextGenS8pMarsReturnScriptTest(TransformerToolboxTestBase):
    def test_waits_when_no_returned_s8p_run_is_found(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            status = mod.main(
                [
                    "--search-root",
                    str(root / "missing"),
                    "--out-dir",
                    str(root / "out"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "next_gen_s8p_mars_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_RETURN")
            self.assertEqual(summary["decision"], "WAIT_FOR_NEXT_GEN_S8P_MARS_RETURN")
            self.assertIsNone(summary["selected_candidate"])

    def test_selects_complete_emx_run_and_dispatches_strict_status_summary(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "returned" / "new_s8p_physical_feature_emx_500"
            _write_run(run_dir, count=2, jobs=2)

            status = mod.main(
                [
                    "--search-root",
                    str(root),
                    "--out-dir",
                    str(root / "out"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "next_gen_s8p_mars_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "READY_FOR_NEXT_GATES")
            self.assertEqual(summary["decision"], "RUN_S8P_PHYSICAL_FEATURE_QUALITY_GATES")
            self.assertEqual(summary["selected_candidate"]["status"], "PASS")
            run_status_path = Path(summary["run_status_result"]["summary_path"])
            run_status = json.loads(run_status_path.read_text(encoding="utf-8"))
            requirements = {item["requirement"]: item for item in run_status["evidence"]}
            self.assertEqual(requirements["all successful rows are traceable to EMX-generated .s8p files"]["status"], "PASS")
            self.assertEqual(requirements["dataset manifest matches approved S8P topology contract"]["status"], "PASS")

    def test_rejects_hfss_return_masquerading_as_s8p_training_data(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "returned" / "new_s8p_physical_feature_emx_500"
            _write_run(run_dir, count=2, jobs=2, source="hfss")

            status = mod.main(
                [
                    "--search-root",
                    str(root),
                    "--out-dir",
                    str(root / "out"),
                    "--expected-count",
                    "2",
                    "--expected-jobs",
                    "2",
                    "--max-touchstone-checks",
                    "0",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "next_gen_s8p_mars_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "FIX_STRICT_S8P_RUN_SUMMARY_FAILURE")
            run_status = summary["run_status_result"]["summary"]
            requirements = {item["requirement"]: item for item in run_status["evidence"]}
            source_gate = requirements["all successful rows are traceable to EMX-generated .s8p files"]
            self.assertEqual(source_gate["status"], "FAIL")
            self.assertIn("'HFSS': 2", source_gate["evidence"])
