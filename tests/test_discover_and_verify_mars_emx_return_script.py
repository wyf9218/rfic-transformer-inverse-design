from tests.rfic_transformer_inverse_design.shared import *
from tests.rfic_transformer_inverse_design.shared import _write_touchstone

import hashlib
import importlib.util
import subprocess
import sys
from unittest import mock


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "discover_and_verify_mars_emx_return.py"
    spec = importlib.util.spec_from_file_location("discover_and_verify_mars_emx_return_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_s4p(path: Path, *, start_ghz: float, stop_ghz: float, points: int) -> None:
    freqs = np.linspace(start_ghz * 1.0e9, stop_ghz * 1.0e9, points)
    s_matrix = np.zeros((points, 4, 4), dtype=np.complex128)
    s_matrix[:, 0, 0] = 0.05 + 0.01j
    s_matrix[:, 1, 1] = 0.05 + 0.01j
    s_matrix[:, 2, 2] = 0.06 + 0.01j
    s_matrix[:, 3, 3] = 0.06 + 0.01j
    s_matrix[:, 0, 2] = 0.02j
    s_matrix[:, 2, 0] = 0.02j
    _write_touchstone(path, freqs, s_matrix)


def _write_tarball_with_sha(path: Path) -> Path:
    path.write_bytes(b"synthetic validation tarball bytes")
    sha_record = Path(str(path) + ".sha256")
    sha_record.write_text(f"{_sha256(path)}  {path.name}\n", encoding="utf-8")
    return sha_record


class DiscoverAndVerifyMarsEmxReturnScriptTest(TransformerToolboxTestBase):
    def test_waits_when_no_mars_return_files_exist(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "out"

            status = mod.main(["--search-root", str(root / "missing"), "--out-dir", str(out_dir)])

            self.assertEqual(status, 0)
            summary = json.loads((out_dir / "mars_emx_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_RETURN")
            self.assertEqual(summary["decision"], "WAIT_FOR_MARS_WIDEBAND_EMX_RETURN")
            self.assertIsNone(summary["selected"]["tarball"])
            self.assertIsNone(summary["selected"]["emx_s4p"])

    def test_rejects_current_style_narrowband_emx_candidate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_s4p(root / "emx.s4p", start_ghz=13.5, stop_ghz=16.5, points=9)

            status = mod.main(["--search-root", str(root), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "mars_emx_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_RETURN")
            self.assertIsNone(summary["selected"]["emx_s4p"])
            self.assertEqual(summary["s4p_candidates"][0]["status"], "FAIL")
            self.assertIn("frequency points expected 451, got 9", "; ".join(summary["s4p_candidates"][0]["reasons"]))

    def test_dry_run_selects_wideband_files_and_uses_strict_emx_s4p_argument(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ec6698dfc575950b"
            root.mkdir()
            _write_s4p(root / "emx.s4p", start_ghz=5.0, stop_ghz=50.0, points=451)
            _write_tarball_with_sha(root / "validation_20260613_transfer.tar.gz")

            status = mod.main(["--search-root", str(root), "--out-dir", str(root / "out"), "--dry-run", "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "mars_emx_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "READY_TO_VERIFY")
            self.assertEqual(summary["decision"], "RUN_TARGET_EMX_POSTRUN_IMPORT_VERIFIER")
            command = summary["verifier_command"]
            self.assertIn("--tarball", command)
            self.assertIn("--sha-record", command)
            self.assertIn("--emx-s4p", command)
            self.assertIn("--require-emx-s4p", command)
            self.assertNotIn("--candidate-emx-s4p", command)

    def test_rejects_wideband_candidate_without_expected_sample_id(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "wrong_sample"
            root.mkdir()
            _write_s4p(root / "emx.s4p", start_ghz=5.0, stop_ghz=50.0, points=451)
            _write_tarball_with_sha(root / "validation_20260613_transfer.tar.gz")

            status = mod.main(["--search-root", str(root), "--out-dir", str(root / "out"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "mars_emx_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_MARS_RETURN")
            self.assertIsNone(summary["selected"]["emx_s4p"])
            self.assertIsNone(summary["selected"]["tarball"])
            self.assertEqual(summary["s4p_candidates"][0]["sample_status"], "FAIL")
            self.assertEqual(summary["tarball_candidates"][0]["sample_status"], "FAIL")
            self.assertIn("expected sample id ec6698dfc575950b", "; ".join(summary["s4p_candidates"][0]["reasons"]))

    def test_accepts_only_after_postrun_import_verifier_accepts(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ec6698dfc575950b"
            root.mkdir()
            _write_s4p(root / "emx.s4p", start_ghz=5.0, stop_ghz=50.0, points=451)
            _write_tarball_with_sha(root / "validation_20260613_transfer.tar.gz")
            command_seen: list[str] = []

            def fake_run(command, cwd, text, capture_output, check):
                command_seen.extend(command)
                out_path = Path(command[command.index("--out-dir") + 1])
                out_path.mkdir(parents=True, exist_ok=True)
                (out_path / "target_emx_postrun_import_summary.json").write_text(
                    json.dumps(
                        {
                            "overall_status": "PASS",
                            "decision": "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="accepted\n", stderr="")

            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                status = mod.main(["--search-root", str(root), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "mars_emx_return_discovery_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS")
            self.assertIn("--emx-s4p", command_seen)
            self.assertNotIn("--candidate-emx-s4p", command_seen)
