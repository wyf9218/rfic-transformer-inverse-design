from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_postrun_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_s8p_hfss_postrun_validation_from_aedt_packet.py"
    spec = importlib.util.spec_from_file_location("run_s8p_hfss_postrun_validation_from_aedt_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_synthetic_s8p_transformer(
    path: Path,
    *,
    scale_l: float = 1.0,
    scale_k: float = 1.0,
    freqs_ghz: list[float] | None = None,
) -> None:
    freqs_hz = np.asarray(freqs_ghz, dtype=float) * 1.0e9 if freqs_ghz is not None else np.linspace(5.0e9, 60.0e9, 111)
    z = np.repeat(np.eye(8, dtype=np.complex128)[None, :, :] * 5.0, len(freqs_hz), axis=0)
    for idx, freq_hz in enumerate(freqs_hz):
        omega = 2.0 * np.pi * float(freq_hz)
        for port in (0, 3):
            z[idx, port, port] = 20.0 + 1j * omega * 0.5e-9 * scale_l
        for port in (4, 5):
            z[idx, port, port] = 18.0 + 1j * omega * 0.6e-9 * scale_l
        mutual = 1j * omega * 0.30e-9 * scale_l * scale_k
        z[idx, 0, 4] = mutual
        z[idx, 4, 0] = mutual
    _write_touchstone(path, freqs_hz, z_to_s(z, z0=50.0))


def _formula_trace_text(port_pairs: str = "1,4:5,6") -> str:
    return "\n".join(
        [
            "# ADS/Python Formula Trace",
            "",
            f"- Port-pair syntax: `{port_pairs}`",
            "",
            "```text",
            "Z_diff = transpose(T) * Z_single * T",
            "Lp = imag(Zdiff[1,1]) / omega",
            "Ls = imag(Zdiff[2,2]) / omega",
            "M  = imag(Zdiff[2,1]) / omega",
            "Qp = imag(Zdiff[1,1]) / real(Zdiff[1,1])",
            "Qs = imag(Zdiff[2,2]) / real(Zdiff[2,2])",
            "Q  = min(Qp, Qs)",
            "K  = M / sqrt(abs(Lp * Ls))",
            "Kw = K",
            "```",
        ]
    )


def _write_hfss_port_manifest(path: Path) -> None:
    records = []
    for idx in range(1, 9):
        x = float(idx * 10)
        signal = [x, 0.0, 12.0]
        ground = [x, 20.0, 0.0]
        records.append(
            {
                "port_name": f"P{idx:03d}",
                "role": "",
                "ground_name": f"P{idx:03d}_G",
                "sheet_name": f"port_sheet_P{idx:03d}",
                "signal_metal": "metal10" if idx <= 4 else "metal9",
                "ground_metal": "metal5",
                "signal_xyz_um": signal,
                "ground_xyz_um": ground,
                "integration_line": {"start_xyz_um": signal, "end_xyz_um": ground},
                "port_sheet_width_um": 10.0,
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema": "rfic_transformer_hfss_s8p_build_port_manifest.v1",
                "payload": str(path.parent / "hfss_s8p_build_payload.json"),
                "port_count": 8,
                "expected_port_order": [f"P{idx:03d}" for idx in range(1, 9)],
                "actual_port_order": [record["port_name"] for record in records],
                "ports": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_packet(
    root: Path,
    *,
    evaluation: str = "eval_s8p_001",
    include_formula_trace: bool = True,
    formula_port_pairs: str = "1,4:5,6",
    include_port_manifest: bool = True,
) -> tuple[Path, Path, Path]:
    payload_dir = root / "packet" / evaluation
    payload_dir.mkdir(parents=True, exist_ok=True)
    emx_s8p = payload_dir / f"{evaluation}_emx.s8p"
    hfss_s8p = root / "hfss_exports" / f"{evaluation}_hfss_export.s8p"
    hfss_s8p.parent.mkdir(parents=True, exist_ok=True)
    formula_trace = payload_dir / "hfss_ads_formula_trace.md"
    if include_formula_trace:
        formula_trace.write_text(_formula_trace_text(formula_port_pairs), encoding="utf-8")
    if include_port_manifest:
        _write_hfss_port_manifest(payload_dir / "hfss_s8p_build_port_manifest.json")
    _write_synthetic_s8p_transformer(emx_s8p)
    payload = {
        "schema": "rfic_transformer_hfss_s8p_build_payload.v1",
        "source_files": {
            "emx_s8p": str(emx_s8p),
            "ads_formula_trace": str(formula_trace) if include_formula_trace else "",
        },
        "ports": [{"name": f"P{idx:03d}", "index": idx} for idx in range(1, 9)],
        "differential_port_pairs": [
            {"plus_port_index": 1, "minus_port_index": 4},
            {"plus_port_index": 5, "minus_port_index": 6},
        ],
    }
    payload_json = payload_dir / "hfss_s8p_build_payload.json"
    payload_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    summary = {
        "overall_status": "PASS",
        "sample_results": [
            {
                "overall_status": "PASS",
                "selection_rank": 1,
                "evaluation": evaluation,
                "payload_json": str(payload_json),
            }
        ],
    }
    packet_summary = root / "hfss_s8p_aedt_script_packet_summary.json"
    packet_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return packet_summary, emx_s8p, hfss_s8p


class RunS8pHfssPostrunValidationFromAedtPacketScriptTest(TransformerToolboxTestBase):
    def test_passes_when_exported_hfss_s8p_matches_emx(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, emx_s8p, hfss_s8p = _write_packet(root)
            hfss_s8p.write_text(emx_s8p.read_text(encoding="ascii"), encoding="ascii")

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--hfss-results-dir",
                    str(hfss_s8p.parent),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_SELECTED_S8P_EMX_HFSS_PHYSICAL_VALIDATION")
            self.assertEqual(summary["frequency_grid_mode"], "final_5_60_0p5_111")
            self.assertTrue(summary["final_acceptance_candidate"])
            self.assertEqual(summary["status_counts"], {"PASS": 1})
            self.assertEqual(summary["arguments"]["expected_ports"], 8)
            self.assertTrue(summary["arguments"]["ground_unused_ports"])
            record = summary["records"][0]
            self.assertEqual(record["port_pairs"], "1,4:5,6")
            self.assertTrue(Path(record["ads_formula_trace"]).is_file())
            self.assertTrue(Path(record["hfss_port_manifest"]).is_file())
            self.assertAlmostEqual(float(record["worst_percent_error"]), 0.0, places=9)
            self.assertTrue(Path(record["compare_summary"]).is_file())
            self.assertTrue(Path(record["target_marker_csv"]).is_file())
            self.assertTrue(Path(record["ads_style_plot_summary"]).is_file())
            checks = {(item["evaluation"], item["name"]): item["status"] for item in summary["checks"]}
            self.assertEqual(checks[("eval_s8p_001", "EMX-vs-HFSS compare PASS")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison frequency window matches requested 5-60 GHz")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison frequency point count is 111")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison window has no EMX/HFSS extrapolation")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison grid reports expected point count")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison grid step is 0.5 GHz")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison grid starts at 5 GHz")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "comparison grid stops at 60 GHz")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "EMX and HFSS frequency grids match")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "target-frequency marker summary exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "target-frequency marker CSV exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "target-frequency marker PASS")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "target-frequency marker is at requested GHz")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "lp_nh target-marker <= 10% error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ls_nh target-marker <= 10% error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "q target-marker <= 10% error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "k target-marker <= 10% error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "kw target-marker <= 10% error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style EMX physical plot exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style HFSS physical plot exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style EMX/HFSS overlay plot exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style metric CSV exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "window-named EMX physical plot exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "window-named HFSS physical plot exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "window-named EMX/HFSS overlay plot exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style EMX plot source is 8-port")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style HFSS plot source is 8-port")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style EMX/HFSS plot port pairs match")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "formula trace contains port_pair_syntax")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "formula trace contains lp_formula")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "formula trace contains q_formula")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "formula trace contains k_formula")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "formula trace contains kw_alias")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "HFSS build port manifest exists")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "HFSS build port manifest port order is P001-P008")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "HFSS build port manifest ground names are P001_G-P008_G")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "HFSS build port manifest records integration lines")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "q <= 10% max error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "kw <= 10% max error")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style scalar Q error tracked")], "PASS")
            self.assertEqual(checks[("eval_s8p_001", "ADS-style Kw/K error tracked")], "PASS")
            audit_commands = [
                item["command"]
                for item in record["command_records"]
                if "audit_touchstone_transformer.py" in " ".join(item["command"])
            ]
            self.assertGreaterEqual(len(audit_commands), 2)
            for command in audit_commands:
                self.assertEqual(command[0], sys.executable)
                self.assertTrue(command[1].endswith("audit_touchstone_transformer.py"), command)
                self.assertNotEqual(command[1], sys.executable)
                self.assertIn("--ground-unused-ports", command)
            compare_commands = [
                item["command"]
                for item in record["command_records"]
                if "compare_emx_hfss_ads.py" in " ".join(item["command"])
            ]
            self.assertEqual(len(compare_commands), 1)
            compare_command = compare_commands[0]
            self.assertIn("--require-touchstone-suffix", compare_command)
            self.assertIn(".s8p", compare_command)
            self.assertIn("--expected-port-count", compare_command)
            self.assertIn("8", compare_command)
            self.assertIn("--expected-reference-ohm", compare_command)
            self.assertIn("50", compare_command)
            self.assertIn("--ground-unused-ports", compare_command)
            plot_commands = [
                item["command"]
                for item in record["command_records"]
                if "plot_emx_hfss_ads_style_metrics.py" in " ".join(item["command"])
            ]
            self.assertEqual(len(plot_commands), 1)
            self.assertIn("--ground-unused-ports", plot_commands[0])

    def test_diagnostic_two_point_grid_cannot_be_final_acceptance(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, emx_s8p, hfss_s8p = _write_packet(root)
            _write_synthetic_s8p_transformer(emx_s8p, freqs_ghz=[15.0, 15.5])
            hfss_s8p.write_text(emx_s8p.read_text(encoding="ascii"), encoding="ascii")

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--hfss-results-dir",
                    str(hfss_s8p.parent),
                    "--out-dir",
                    str(root / "postrun"),
                    "--compare-start-ghz",
                    "15",
                    "--compare-stop-ghz",
                    "15.5",
                    "--expected-frequency-step-ghz",
                    "0.5",
                    "--expected-frequency-points",
                    "2",
                    "--skip-ads-style-plots",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_DIAGNOSTIC_S8P_EMX_HFSS_SCREENING_ONLY_NOT_FINAL")
            self.assertEqual(summary["frequency_grid_mode"], "diagnostic_screening_only")
            self.assertFalse(summary["final_acceptance_candidate"])
            checks = {(item["evaluation"], item["name"]): item["status"] for item in summary["checks"]}
            self.assertEqual(checks[("eval_s8p_001", "comparison grid starts at 15 GHz")], "PASS")

    def test_finds_hfss_export_in_default_payload_results_dir_without_manual_map(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, emx_s8p, _ = _write_packet(root)
            default_results = emx_s8p.parent / "hfss_solve_export_results"
            default_results.mkdir(parents=True)
            default_hfss = default_results / "eval_s8p_001_hfss_export.s8p"
            default_hfss.write_text(emx_s8p.read_text(encoding="ascii"), encoding="ascii")

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(Path(summary["records"][0]["hfss_s8p"]), default_hfss.resolve())

    def test_fails_when_hfss_export_exists_without_build_port_manifest(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, emx_s8p, hfss_s8p = _write_packet(root, include_port_manifest=False)
            hfss_s8p.write_text(emx_s8p.read_text(encoding="ascii"), encoding="ascii")

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--hfss-results-dir",
                    str(hfss_s8p.parent),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {(item["evaluation"], item["name"]): item["status"] for item in summary["checks"]}
            self.assertEqual(checks[("eval_s8p_001", "HFSS build port manifest exists")], "FAIL")

    def test_waits_when_hfss_export_has_not_been_copied_back(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, _, _ = _write_packet(root)

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "WAITING_FOR_HFSS")
            self.assertEqual(summary["decision"], "WAIT_FOR_EXPORTED_HFSS_S8P")
            self.assertEqual(summary["status_counts"], {"WAITING_FOR_HFSS": 1})

    def test_fails_when_hfss_physical_curves_deviate_more_than_ten_percent(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, _, hfss_s8p = _write_packet(root)
            _write_synthetic_s8p_transformer(hfss_s8p, scale_l=1.20)

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--hfss-results-dir",
                    str(hfss_s8p.parent),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_USE_S8P_HFSS_VALIDATION_YET")
            record = summary["records"][0]
            self.assertEqual(record["status"], "FAIL")
            self.assertGreater(float(record["worst_percent_error"]), 10.0)

    def test_fails_when_formula_trace_is_missing(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, emx_s8p, hfss_s8p = _write_packet(root, include_formula_trace=False)
            hfss_s8p.write_text(emx_s8p.read_text(encoding="ascii"), encoding="ascii")

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--hfss-results-dir",
                    str(hfss_s8p.parent),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {(item["evaluation"], item["name"]): item["status"] for item in summary["checks"]}
            self.assertEqual(checks[("eval_s8p_001", "payload ADS/Python formula trace exists")], "FAIL")

    def test_fails_when_formula_trace_port_pairs_do_not_match_payload(self) -> None:
        postrun = _load_postrun_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            packet_summary, emx_s8p, hfss_s8p = _write_packet(root, formula_port_pairs="1,2:5,6")
            hfss_s8p.write_text(emx_s8p.read_text(encoding="ascii"), encoding="ascii")

            status = postrun.main(
                [
                    "--aedt-packet-summary",
                    str(packet_summary),
                    "--hfss-results-dir",
                    str(hfss_s8p.parent),
                    "--out-dir",
                    str(root / "postrun"),
                ]
            )

            self.assertEqual(status, 2)
            summary = json.loads((root / "postrun" / "s8p_hfss_postrun_validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {(item["evaluation"], item["name"]): item["status"] for item in summary["checks"]}
            self.assertEqual(checks[("eval_s8p_001", "formula trace contains port_pair_syntax")], "FAIL")
