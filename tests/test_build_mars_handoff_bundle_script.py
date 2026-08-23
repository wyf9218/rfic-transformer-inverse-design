from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
import tarfile


def _load_handoff_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_mars_handoff_bundle.py"
    spec = importlib.util.spec_from_file_location("build_mars_handoff_bundle_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_minimal_handoff_inputs(repo: Path, project: Path, handoff) -> None:
    (repo / "scripts").mkdir(parents=True)
    (repo / "configs").mkdir(parents=True)
    project.mkdir()
    for name in handoff.DEFAULT_SCRIPT_NAMES:
        script_text = f"#!/usr/bin/env python3\n# {name}\n"
        if name == "build_emx_first_validation_gate.py":
            script_text += (
                "BOUNDARY = 'basic numeric physics sanity'\n"
                "PHYSICAL = 'physical metric window'\n"
                "SMOOTH = 'smooth transformer metric window'\n"
                "SHAPE = 'max_shape_relative_step'\n"
                "NOTE = 'not a golden-reference acceptance'\n"
            )
        (repo / "scripts" / name).write_text(script_text, encoding="utf-8")
    for name in handoff.DEFAULT_CONFIG_NAMES:
        (repo / "configs" / name).write_text("config: true\n", encoding="utf-8")
    for name in handoff.DEFAULT_PACKAGE_SOURCE_NAMES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\nSIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME = 'x'\n", encoding="utf-8")
    for name in handoff.DEFAULT_PROJECT_FILES:
        (project / name).write_text(f"{name}\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BuildMarsHandoffBundleScriptTest(TransformerToolboxTestBase):
    def test_builds_bundle_with_scripts_configs_inventory_and_sha(self) -> None:
        handoff = _load_handoff_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            _write_minimal_handoff_inputs(repo, project, handoff)

            bundle = root / "mars_handoff.tar.gz"
            status = handoff.main(
                [
                    "--repo-root",
                    str(repo),
                    "--project-root",
                    str(project),
                    "--out",
                    str(bundle),
                    "--force",
                ]
            )

            self.assertEqual(status, 0)
            self.assertTrue(bundle.exists())
            self.assertTrue((root / "mars_handoff.tar.gz.sha256").exists())
            staging = root / "mars_handoff"
            inventory = json.loads((staging / "MARS_HANDOFF_INVENTORY_20260613.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(inventory["file_count"], len(handoff.DEFAULT_SCRIPT_NAMES))
            self.assertTrue((staging / "SHA256SUMS.txt").exists())
            with tarfile.open(bundle, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("mars_handoff/scripts/build_mars_handoff_bundle.py", names)
            self.assertIn("mars_handoff/scripts/verify_mars_handoff_install.py", names)
            self.assertIn("mars_handoff/scripts/verify_mars_dataset_package.py", names)
            self.assertIn("mars_handoff/scripts/discover_mars_emx_cadence_paths.py", names)
            self.assertIn("mars_handoff/scripts/audit_mars_run_progress.py", names)
            self.assertIn("mars_handoff/scripts/watch_mars_run_progress.py", names)
            self.assertIn("mars_handoff/scripts/backfill_ground_clearance_audit.py", names)
            self.assertIn("mars_handoff/scripts/build_emx_first_validation_gate.py", names)
            self.assertIn("mars_handoff/scripts/compare_emx_hfss_ads.py", names)
            self.assertIn("mars_handoff/scripts/run_hfss_emx_validation_batch.py", names)
            self.assertIn("mars_handoff/scripts/verify_accepted_emx_hfss_ads_figures.py", names)
            self.assertIn("mars_handoff/scripts/audit_248k_launch_readiness.py", names)
            self.assertIn("mars_handoff/scripts/run_package_selfcheck_compare.py", names)
            self.assertIn("mars_handoff/rfic_transformer_inverse_design/dataset.py", names)
            self.assertIn("mars_handoff/rfic_transformer_inverse_design/execution/evaluator.py", names)
            self.assertIn("mars_handoff/rfic_transformer_inverse_design/layout/export.py", names)
            self.assertIn("mars_handoff/configs/mars_dataset_248k_template.yaml", names)
            self.assertIn("mars_handoff/configs/mars_dataset_500_wideband_20260613.yaml", names)
            self.assertIn("mars_handoff/project_runbook/mars_dataset_500_wideband_20260613.commands.sh", names)
            self.assertIn("mars_handoff/project_runbook/MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md", names)
            self.assertIn("mars_handoff/README_MARS_HANDOFF_20260613.md", names)
            commands = (staging / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}", commands)
            self.assertIn("scripts/audit_mars_run_progress.py", commands)
            self.assertIn("--require-emx-command", commands)
            self.assertIn("--expected-port-mode single_ended_shield_grounded", commands)
            self.assertIn("--expected-pin-purpose 51", commands)
            self.assertIn("--require-geometry-quality", commands)
            self.assertIn("--internal-angle-deg 135", commands)
            self.assertIn("--terminal-angle-deg 90", commands)
            self.assertIn("scripts/run_dataset_quality_gates.py", commands)
            self.assertIn("--require-clearance-audit", commands)
            self.assertIn("--sampling-require-uniform-closer-than-normal", commands)
            self.assertIn("--sampling-min-uniform-vs-normal-fields-fraction 0.8", commands)
            self.assertIn("--sampling-min-histogram-entropy-frac 0.85", commands)
            self.assertIn("--sampling-max-min-norm 0.05", commands)
            self.assertIn("--sampling-min-max-norm 0.95", commands)
            self.assertIn("--sampling-space-filling-strata 20", commands)
            self.assertIn("--sampling-max-space-filling-empty-strata-frac 0", commands)
            self.assertIn("--sampling-max-space-filling-duplicate-frac 0", commands)
            self.assertIn("--touchstone-shape-window-start-ghz 5.0", commands)
            self.assertIn("--touchstone-shape-window-stop-ghz 30", commands)
            self.assertIn("--touchstone-max-shape-spike-ratio 4", commands)
            self.assertIn("--touchstone-max-shape-relative-step 0.25", commands)
            self.assertIn("--audit-response-feature-coverage", commands)
            self.assertNotIn("/home/researcher", commands)
            readme = (staging / "README_MARS_HANDOFF_20260613.md").read_text(encoding="utf-8")
            self.assertIn("scripts/verify_mars_handoff_install.py", readme)
            self.assertIn("scripts/verify_mars_dataset_package.py --help", readme)
            self.assertIn("scripts/compare_emx_hfss_ads.py --help", readme)
            self.assertIn("scripts/build_validation_chain_decision_card.py --help", readme)
            self.assertIn("scripts/build_mars_next_action_packet.py --help", readme)
            self.assertIn("scripts/verify_accepted_emx_hfss_ads_figures.py --help", readme)
            self.assertIn("scripts/discover_mars_emx_cadence_paths.py --help", readme)
            self.assertIn("scripts/run_hfss_emx_validation_batch.py --help", readme)
            self.assertIn("mars_emx_cadence_path_discovery_20260613", readme)
            self.assertIn("--hint-command project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh", readme)
            self.assertIn("scripts/audit_248k_launch_readiness.py --help", readme)
            emx_gate = (staging / "scripts" / "build_emx_first_validation_gate.py").read_text(encoding="utf-8")
            self.assertIn("basic numeric physics sanity", emx_gate)
            self.assertIn("physical metric window", emx_gate)
            self.assertIn("smooth transformer metric window", emx_gate)
            self.assertIn("not a golden-reference acceptance", emx_gate)
            self.assertNotIn("target transformer sanity", emx_gate)

    def test_builds_deterministic_tar_for_unchanged_inputs(self) -> None:
        handoff = _load_handoff_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            _write_minimal_handoff_inputs(repo, project, handoff)

            bundle = root / "mars_handoff.tar.gz"
            args = [
                "--repo-root",
                str(repo),
                "--project-root",
                str(project),
                "--out",
                str(bundle),
                "--force",
            ]

            self.assertEqual(handoff.main(args), 0)
            first_hash = _sha256(bundle)
            self.assertEqual(handoff.main(args), 0)
            self.assertEqual(_sha256(bundle), first_hash)
            self.assertEqual((root / "mars_handoff.tar.gz.sha256").read_text(encoding="utf-8").split()[0], first_hash)
