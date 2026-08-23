from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_progress_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_mars_run_progress.py"
    spec = importlib.util.spec_from_file_location("audit_mars_run_progress_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_eval(
    root: Path,
    key: str,
    freqs_hz: np.ndarray,
    *,
    ports: int = 4,
    suffix: str = ".s4p",
    summary: dict | None = None,
    emx_command: list[str] | None = None,
) -> None:
    eval_dir = root / "evaluations" / key
    (eval_dir / "emx").mkdir(parents=True)
    (eval_dir / "layout").mkdir(parents=True)
    (eval_dir / "summary.json").write_text(json.dumps({"ok": True} if summary is None else summary), encoding="utf-8")
    (eval_dir / "layout" / "transformer_layout.layout.json").write_text("{}", encoding="utf-8")
    s_matrix = np.zeros((len(freqs_hz), ports, ports), dtype=np.complex128)
    _write_touchstone(eval_dir / "emx" / f"emx{suffix}", freqs_hz, s_matrix)
    command = emx_command if emx_command is not None else _valid_emx_command(freqs_hz, ports=ports)
    (eval_dir / "emx" / "emx_command.json").write_text(json.dumps(command), encoding="utf-8")


def _valid_emx_command(freqs_hz: np.ndarray, *, ports: int = 4, pin_purpose: int = 51, grounded: bool = True) -> list[str]:
    command = [
        "emx",
        "layout.gds",
        "TRANSFORMER",
        "proc.proc",
        "--touchstone",
        "--s-impedance=50",
        "-s",
        "emx.s4p",
        "--include-command-line",
        f"--cadence-pins={pin_purpose}",
    ]
    for index in range(ports):
        name = f"P{index + 1:03d}"
        port_spec = f"{name}={name}:GND" if grounded else f"{name}={name}"
        command.append(f"--port={port_spec}")
    command.extend(str(float(freq)) for freq in freqs_hz)
    return command


def _write_clearance_audit(root: Path, keys: list[str], *, missing: int = 0) -> None:
    records = [
        {
            "cache_key": key,
            "status": "pass_signal_to_shield_clearance",
            "direct_signal_shield_overlap_area_um2": 0.0,
            "signal_shield_clearance_violation_area_um2": 0.0,
        }
        for key in keys
    ]
    audit = {
        "candidate_count": len(keys),
        "pass_count": len(keys) - int(missing),
        "reject_count": 0,
        "missing_or_other_count": int(missing),
        "selected": records[0] if records else None,
        "records": records,
    }
    (root / "final500_ground_clearance_audit.json").write_text(json.dumps(audit), encoding="utf-8")


def _geometry_manifest(count: int, *, shield_enabled: bool = True, internal_angle: float = 135.0, terminal_angle: float = 90.0) -> dict:
    return {
        "requested_count": count,
        "ok_count": count,
        "fail_count": 0,
        "port_mode": "single_ended_shield_grounded",
        "cadence_pin_purpose": 51,
        "shield_enabled": shield_enabled,
        "geometry_quality": {
            "geometry_check_count": count,
            "geometry_check_ok_count": count,
            "angle_checked_count": 4 * count,
            "primary_internal_angle_deg": {"min": internal_angle, "max": internal_angle},
            "secondary_internal_angle_deg": {"min": internal_angle, "max": internal_angle},
            "primary_terminal_interface_angle_deg": {"min": terminal_angle, "max": terminal_angle},
            "secondary_terminal_interface_angle_deg": {"min": terminal_angle, "max": terminal_angle},
        },
    }


class AuditMarsRunProgressScriptTest(TransformerToolboxTestBase):
    def test_complete_run_passes_transfer_readiness_checks(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(root, "a", freqs_hz)
            _write_eval(root, "b", freqs_hz)
            (root / "dataset_manifest.json").write_text(
                json.dumps({"requested_count": 2, "ok_count": 2, "fail_count": 0}),
                encoding="utf-8",
            )
            (root / "dataset_rows.csv").write_text(
                "\n".join(
                    [
                        "sample_id,ok,touchstone_path",
                        "a,true,evaluations/a/emx/emx.s4p",
                        "b,true,evaluations/b/emx/emx.s4p",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            _write_clearance_audit(root, ["a", "b"])

            status = progress.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "progress"),
                    "--expected-count",
                    "2",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--require-emx-command",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--require-clearance-audit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["rows"]["ok_count"], 2)
            self.assertEqual(summary["evaluations"]["summary_ok_count"], 2)
            self.assertEqual(summary["evaluations"]["touchstone_file_count"], 2)
            self.assertEqual(summary["evaluations"]["emx_command_file_count"], 2)
            self.assertEqual(summary["touchstone_frequency_checks"]["checked_count"], 2)
            self.assertEqual(summary["emx_command_checks"]["status"], "PASS")
            self.assertEqual(summary["emx_command_checks"]["checked_count"], 2)
            self.assertEqual(summary["clearance_audit"]["candidate_count"], 2)
            self.assertEqual(summary["clearance_audit"]["missing_or_other_count"], 0)

    def test_required_clearance_audit_missing_does_not_pass(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(root, "a", freqs_hz)
            (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 1, "ok_count": 1}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text(
                "sample_id,ok,touchstone_path\na,true,evaluations/a/emx/emx.s4p\n",
                encoding="utf-8",
            )

            status = progress.main([str(root), "--out-dir", str(root / "progress"), "--expected-count", "1", "--require-clearance-audit"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("raw clearance audit file", failed_checks)

    def test_required_geometry_quality_passes_with_manifest_angle_contract(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(root, "a", freqs_hz)
            (root / "dataset_manifest.json").write_text(json.dumps(_geometry_manifest(1)), encoding="utf-8")
            (root / "dataset_rows.csv").write_text(
                "sample_id,ok,touchstone_path\na,true,evaluations/a/emx/emx.s4p\n",
                encoding="utf-8",
            )

            status = progress.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "progress"),
                    "--expected-count",
                    "1",
                    "--require-geometry-quality",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--internal-angle-deg",
                    "135",
                    "--terminal-angle-deg",
                    "90",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertTrue(summary["arguments"]["require_geometry_quality"])
            self.assertEqual(summary["manifest"]["geometry_quality"]["angle_checked_count"], 4)
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertFalse(failed_checks)

    def test_required_geometry_quality_rejects_bad_angles_and_missing_shield(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(root, "a", freqs_hz)
            manifest = _geometry_manifest(1, shield_enabled=False, internal_angle=120.0)
            (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "dataset_rows.csv").write_text(
                "sample_id,ok,touchstone_path\na,true,evaluations/a/emx/emx.s4p\n",
                encoding="utf-8",
            )

            status = progress.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "progress"),
                    "--expected-count",
                    "1",
                    "--require-geometry-quality",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("manifest shield enabled", failed_checks)
            self.assertIn("manifest primary_internal_angle_deg", failed_checks)
            self.assertIn("manifest secondary_internal_angle_deg", failed_checks)

    def test_emx_command_contract_rejects_wrong_pin_and_ungrounded_ports(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(
                root,
                "a",
                freqs_hz,
                emx_command=_valid_emx_command(freqs_hz, pin_purpose=50, grounded=False),
            )
            (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 1, "ok_count": 1}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text(
                "sample_id,ok,touchstone_path\na,true,evaluations/a/emx/emx.s4p\n",
                encoding="utf-8",
            )

            status = progress.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "progress"),
                    "--expected-count",
                    "1",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                    "--require-emx-command",
                    "--expected-port-mode",
                    "single_ended_shield_grounded",
                    "--expected-pin-purpose",
                    "51",
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("per-evaluation EMX command contract", failed_checks)
            mismatches = summary["emx_command_checks"]["checked"][0]["mismatches"]
            self.assertTrue(any("cadence pin purpose expected=51" in item for item in mismatches))
            self.assertTrue(any("ports missing ground delimiter" in item for item in mismatches))

    def test_incomplete_run_does_not_pass(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "evaluations" / "a").mkdir(parents=True)
            (root / "evaluations" / "a" / "summary.json").write_text("{}", encoding="utf-8")
            (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 2, "ok_count": 1}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text("sample_id,ok,touchstone_path\na,true,\n", encoding="utf-8")

            status = progress.main([str(root), "--out-dir", str(root / "progress"), "--expected-count", "2"])

            self.assertEqual(status, 2)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("completed ok rows", failed_checks)
            self.assertIn("per-evaluation summary ok status", failed_checks)
            self.assertIn("per-evaluation Touchstone files", failed_checks)
            self.assertIn("per-evaluation layout JSON files", failed_checks)

    def test_summary_error_status_does_not_pass(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(root, "a", freqs_hz, summary={"ok": False, "error": "emx failed"})
            (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 1, "ok_count": 1}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text(
                "sample_id,ok,touchstone_path\na,true,evaluations/a/emx/emx.s4p\n",
                encoding="utf-8",
            )

            status = progress.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "progress"),
                    "--expected-count",
                    "1",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("per-evaluation summary ok status", failed_checks)
            row_text = (root / "progress" / "mars_run_progress_rows.csv").read_text(encoding="utf-8")
            self.assertIn("emx failed", row_text)

    def test_two_port_s2p_is_rejected_for_four_port_transformer_dataset(self) -> None:
        progress = _load_progress_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            freqs_hz = np.array([5.0e9, 5.1e9, 5.2e9])
            _write_eval(root, "a", freqs_hz, ports=2, suffix=".s2p")
            (root / "dataset_manifest.json").write_text(json.dumps({"requested_count": 1, "ok_count": 1}), encoding="utf-8")
            (root / "dataset_rows.csv").write_text(
                "sample_id,ok,touchstone_path\na,true,evaluations/a/emx/emx.s2p\n",
                encoding="utf-8",
            )

            status = progress.main(
                [
                    str(root),
                    "--out-dir",
                    str(root / "progress"),
                    "--expected-count",
                    "1",
                    "--expected-frequency-start-ghz",
                    "5.0",
                    "--expected-frequency-stop-ghz",
                    "5.2",
                    "--expected-frequency-step-ghz",
                    "0.1",
                    "--expected-frequency-points",
                    "3",
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "progress" / "mars_run_progress_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "INCOMPLETE")
            failed_checks = {item["name"] for item in summary["checks"] if item["status"] == "FAIL"}
            self.assertIn("per-evaluation Touchstone extension", failed_checks)
            self.assertIn("sampled Touchstone file/port/frequency", failed_checks)
            checked = summary["touchstone_frequency_checks"]["checked"][0]
            self.assertIn("ports expected=4 actual=2", checked["mismatches"])
