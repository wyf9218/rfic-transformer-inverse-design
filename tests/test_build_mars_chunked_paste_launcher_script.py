from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "rfic-transformer-inverse-design" / "scripts" / "build_mars_chunked_paste_launcher.py"


class BuildMarsChunkedPasteLauncherScriptTest(TransformerToolboxTestBase):
    def test_chunked_launcher_reassembles_packet_and_documents_final_pilot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            packet = tmp_path / "fake_final_candidate_gate.tar.gz"
            payload = b"codex-final-candidate-gate-packet\x00" * 200
            packet.write_bytes(payload)
            expected_sha = hashlib.sha256(payload).hexdigest()
            out_dir = tmp_path / "chunks"

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--packet",
                    str(packet),
                    "--packet-name",
                    "fake_final_candidate_gate.tar.gz",
                    "--out-dir",
                    str(out_dir),
                    "--chunk-size",
                    "1024",
                    "--work-dir",
                    "/tmp/codex_chunked_mars_test",
                    "--project",
                    "/tmp/codex_project",
                    "--sync-dir",
                    "fake_sync_dir",
                    "--force",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            scripts = sorted(out_dir.glob("*.sh"))
            self.assertGreater(len([p for p in scripts if p.name.startswith("PART_")]), 1)
            for script in scripts:
                syntax = subprocess.run(["bash", "-n", str(script)], check=False, capture_output=True, text=True)
                self.assertEqual(syntax.returncode, 0, f"{script}\n{syntax.stderr}")

            work_dir = tmp_path / "mars_work"
            env = os.environ.copy()
            env["WORK_DIR"] = str(work_dir)
            ordered = [
                out_dir / "00_INIT_MARS_FINAL_CANDIDATE_GATE_UPLOAD.sh",
                *sorted(out_dir.glob("PART_*.sh")),
                out_dir / "98_VERIFY_REASSEMBLE_ONLY.sh",
            ]
            for script in ordered:
                run = subprocess.run(["bash", str(script)], check=False, capture_output=True, text=True, env=env)
                self.assertEqual(run.returncode, 0, f"{script}\nSTDOUT={run.stdout}\nSTDERR={run.stderr}")

            restored = work_dir / "fake_final_candidate_gate.tar.gz"
            self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(), expected_sha)

            finalize = (out_dir / "99_FINALIZE_INSTALL_AND_RUN_20_PILOT.sh").read_text(encoding="utf-8")
            self.assertIn("MARS_S8P_20_AFTER_UNLOCK_20260626.sh", finalize)
            self.assertIn("INSTALL_ON_MARS.sh", finalize)
            self.assertIn("CODEX_FINAL_CANDIDATE_GATE_20_PILOT_STARTED", finalize)

            runbook = (out_dir / "README_CHUNKED_MARS_PASTE_20260626_CN.md").read_text(encoding="utf-8")
            self.assertIn("98_VERIFY", runbook)
            self.assertIn("99_FINALIZE", runbook)
            self.assertIn(expected_sha, runbook)
