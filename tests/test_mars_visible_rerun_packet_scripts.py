from tests.rfic_transformer_inverse_design.shared import *

import base64
import gzip
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PASTE_SCRIPT = ROOT / "mars_visible_rerun_packet_20260614" / "PASTE_MARS_ZIN_BALANCED_ACQUISITION_PLAN_20260614.sh"
REACHABLE_CANDIDATE_SCRIPT = (
    ROOT / "mars_visible_rerun_packet_20260614" / "PASTE_MARS_REACHABLE_CANDIDATE_SELECTION_20260615.sh"
)
PULL_SCRIPT = ROOT / "mars_visible_rerun_packet_20260614" / "PULL_MARS_WIDEBAND_EMX_AND_REPLOT_20260614.sh"
PULL_REACHABLE_SCRIPT = (
    ROOT / "mars_visible_rerun_packet_20260614" / "PULL_MARS_REACHABLE_QUEUE_AND_PUBLISH_20260615.sh"
)
RUN_REACHABLE_QUEUE_DATASET_SCRIPT = (
    ROOT / "mars_visible_rerun_packet_20260614" / "PASTE_MARS_RUN_REACHABLE_QUEUE_DATASET_20260615.sh"
)
PLANNER_SCRIPT = ROOT / "rfic-transformer-inverse-design" / "scripts" / "plan_zin_balanced_acquisition.py"
VERIFIER_SCRIPT = ROOT / "rfic-transformer-inverse-design" / "scripts" / "verify_zin_balanced_acquisition_plan.py"
ZIN_AUDIT_SCRIPT = ROOT / "rfic-transformer-inverse-design" / "scripts" / "audit_zin_coverage.py"
IMPORTER_SCRIPT = ROOT / "rfic-transformer-inverse-design" / "scripts" / "import_mars_validation_export_bundle.py"
RUN_CANDIDATE_QUEUE_DATASET_SCRIPT = (
    ROOT / "rfic-transformer-inverse-design" / "scripts" / "run_candidate_queue_dataset.py"
)
REACHABLE_PROCESSOR_SCRIPT = (
    ROOT / "reports" / "current_validation_status_20260614" / "process_reachable_candidate_queue_bundle.py"
)


def _embedded_text(script_text: str, variable: str) -> str:
    match = re.search(rf"{variable}='([^']+)'", script_text)
    assert match is not None, variable
    return gzip.decompress(base64.b64decode(match.group(1))).decode()


class MarsVisibleRerunPacketScriptsTest(TransformerToolboxTestBase):
    def test_handoff_scripts_are_shell_parseable(self) -> None:
        for path in (
            PASTE_SCRIPT,
            REACHABLE_CANDIDATE_SCRIPT,
            RUN_REACHABLE_QUEUE_DATASET_SCRIPT,
            PULL_SCRIPT,
            PULL_REACHABLE_SCRIPT,
        ):
            result = subprocess.run(["bash", "-n", str(path)], check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_reachable_candidate_script_documents_no_label_creation(self) -> None:
        text = REACHABLE_CANDIDATE_SCRIPT.read_text(encoding="utf-8")

        for token in (
            "reachable_zin_targeted_candidate_selection.csv",
            "reachable_candidate_queue_summary.json",
            "01_reachable_candidate_queue_zin_overlay.png",
            "Surrogate predictions are only for acquisition priority and are not EMX/ADS labels.",
            "Unreachable target bins are skipped rather than filled with fallback candidates.",
            "MARS_REACHABLE_CANDIDATE_QUEUE_LATEST",
            "MARS_REACHABLE_CANDIDATE_QUEUE_BUNDLE",
            "sha256sum \"$BUNDLE\"",
            "REACHABLE_QUEUE_EXPORT_MANIFEST.txt",
        ):
            self.assertIn(token, text)

    def test_pull_reachable_script_uses_bundle_fallback_and_bounded_ssh(self) -> None:
        text = PULL_REACHABLE_SCRIPT.read_text(encoding="utf-8")

        for token in (
            "SSH_CONNECT_TIMEOUT",
            "MARS_REACHABLE_QUEUE_BUNDLE",
            "MARS_REACHABLE_QUEUE_BUNDLE_SHA256",
            "REMOTE_REACHABLE_QUEUE_BUNDLE",
            "mars_zin_candidate_queue_reachable_latest.tar.gz",
            "process_reachable_candidate_queue_bundle.py",
            "SSH_OPTS=(",
            "process_bundle()",
            "--sha256-file",
            "ssh \"${SSH_OPTS[@]}\"",
            "scp \"${SCP_OPTS[@]}\"",
            "MARS_REACHABLE_QUEUE_PROCESS_SUMMARY",
            "MARS_REACHABLE_QUEUE_PUBLISHER_MANIFEST",
        ):
            self.assertIn(token, text)
        self.assertTrue(REACHABLE_PROCESSOR_SCRIPT.is_file())

    def test_run_reachable_queue_dataset_script_runs_real_emx_labels_only(self) -> None:
        text = RUN_REACHABLE_QUEUE_DATASET_SCRIPT.read_text(encoding="utf-8")

        for token in (
            "run_candidate_queue_dataset.py",
            "reachable_zin_targeted_candidate_selection.csv",
            "mars_reachable_queue_emx_dataset_latest.tar.gz",
            "Predicted Zin columns are provenance only",
            "--force-wideband-5-50-0p1",
            "--force-port-mode single_ended_shield_grounded",
            "--expected-frequency-points 451",
            "audit_mars_run_progress.py",
            "run_dataset_quality_gates.py",
            "--audit-zin-coverage",
            "--audit-zin-sweep-coverage",
            "package_mars_dataset_run.py",
            "sha256sum \"$PACKAGE\"",
        ):
            self.assertIn(token, text)
        self.assertTrue(RUN_CANDIDATE_QUEUE_DATASET_SCRIPT.is_file())

    def test_paste_script_embeds_current_planner_and_verifier(self) -> None:
        text = PASTE_SCRIPT.read_text(encoding="utf-8")

        self.assertEqual(_embedded_text(text, "SCRIPT_B64"), PLANNER_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(_embedded_text(text, "VERIFY_B64"), VERIFIER_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(_embedded_text(text, "AUDIT_ZIN_B64"), ZIN_AUDIT_SCRIPT.read_text(encoding="utf-8"))

    def test_paste_script_exports_traceable_bundle(self) -> None:
        text = PASTE_SCRIPT.read_text(encoding="utf-8")

        for token in (
            "mars_validation_export_$STAMP",
            "mars_validation_export_latest.tar.gz",
            "final_zin_coverage_audit_$STAMP",
            "zin_uniformity_audit",
            "audit_zin_coverage.py",
            "zin_uniformity_status=$ZIN_UNIFORMITY_STATUS",
            "EXPORT_MANIFEST.txt",
            "remote_emx_status=$EMX_STATUS",
            "tar -C \"$RUN\" -czf \"$BUNDLE\"",
            "sha256sum \"$BUNDLE\"",
            "MARS_VALIDATION_EXPORT_LATEST_SHA256",
        ):
            self.assertIn(token, text)

    def test_pull_script_uses_bundle_fallback_and_bounded_ssh(self) -> None:
        text = PULL_SCRIPT.read_text(encoding="utf-8")

        for token in (
            "SSH_CONNECT_TIMEOUT",
            "MARS_EXPORT_BUNDLE",
            "MARS_EXPORT_BUNDLE_SHA256",
            "BUNDLE_IMPORTER",
            "ZIN_UNIFORMITY_PUBLISHER",
            "PLAUSIBILITY_AUDIT_SCRIPT",
            "import_mars_validation_export_bundle.py",
            "publish_verified_zin_uniformity_result.py",
            "audit_ads_style_curve_plausibility.py",
            "SSH_OPTS=(",
            "LOCAL_BUNDLE_SUPPLIED",
            "unpack_validation_bundle()",
            "--sha256-file",
            "--local-emx",
            "--local-zin-plan-dir",
            "--local-zin-uniformity-audit-dir",
            "ssh \"${SSH_OPTS[@]}\"",
            "scp \"${SCP_OPTS[@]}\"",
            "REMOTE_BUNDLE",
            "LOCAL_BUNDLE_UNPACK",
            "mars_validation_export_import_summary.json",
            "Using EMX S4P already copied from validation export bundle",
            "Using Zin plan already copied from validation export bundle",
            "publish_zin_uniformity_if_present()",
            "ZIN_UNIFORMITY_PUBLISHED=true",
            "Auditing ADS-style Lp/Ls/Q/Kw curve plausibility",
            "PLAUSIBILITY_AUDIT=$OUT_DIR/ads_style_curve_plausibility_audit_20260615.json",
        ):
            self.assertIn(token, text)
        self.assertTrue(IMPORTER_SCRIPT.is_file())
