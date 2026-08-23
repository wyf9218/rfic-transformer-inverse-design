from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import shutil
import sys
from unittest import mock


def _load_verify_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "verify_mars_handoff_install.py"
    spec = importlib.util.spec_from_file_location("verify_mars_handoff_install_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _block_yaml_import(name, *args, **kwargs):
    if name == "yaml":
        raise ModuleNotFoundError("No module named yaml")
    return _REAL_IMPORT(name, *args, **kwargs)


_REAL_IMPORT = __import__


def _target_emx_frequency_tokens(*, wrong: bool = False) -> str:
    if wrong:
        return "5000000000 5100000000 50000000000"
    return " ".join(str(freq) for freq in range(5_000_000_000, 50_000_000_000 + 100_000_000, 100_000_000))


def _write_handoff(
    root: Path,
    *,
    include_shape_gate: bool = True,
    local_path: bool = False,
    wrong_frequency: bool = False,
    wrong_target_emx_frequency_list: bool = False,
    omit_quality_fragment: str | None = None,
) -> None:
    verify = _load_verify_module()
    for name in verify.REQUIRED_SCRIPTS:
        path = root / "scripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name in {
            "patch_mars_config_paths.py",
            "discover_mars_emx_cadence_paths.py",
            "package_mars_dataset_run.py",
            "verify_mars_dataset_package.py",
            "run_dataset_quality_gates.py",
            "audit_zin_coverage.py",
            "audit_response_feature_coverage.py",
            "run_hfss_emx_validation_batch.py",
            "build_validation_chain_decision_card.py",
            "build_mars_next_action_packet.py",
            "run_accepted_emx_hfss_ads_validation.py",
            "verify_accepted_emx_hfss_ads_figures.py",
            "audit_248k_launch_readiness.py",
            "build_emx_first_validation_gate.py",
            "verify_target_emx_postrun_package.py",
            "audit_touchstone_transformer.py",
            "watch_mars_emx_return.py",
        }:
            shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / name, path)
        else:
            path.write_text(f"#!/usr/bin/env python3\n# {name}\n", encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    for rel_path in verify.REQUIRED_PACKAGE_SOURCES:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root / rel_path, target)
    config = root / "configs" / "mars_dataset_500_wideband_20260613.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "target:\n"
        f"  frequency_start_hz: {6.0e9 if wrong_frequency else 5.0e9}\n"
        "  frequency_stop_hz: 50000000000.0\n"
        "  frequency_step_hz: 100000000.0\n"
        "  band_points: 451\n"
        "transformer:\n"
        "  shield:\n"
        "    enabled: true\n"
        "    kind: ring\n"
        "emx:\n"
        "  port_mode: single_ended_shield_grounded\n"
        "  cadence_pin_purpose: 51\n",
        encoding="utf-8",
    )
    command = root / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    command.parent.mkdir(parents=True, exist_ok=True)
    fragments = list(verify.QUALITY_GATE_REQUIRED_FRAGMENTS)
    if not include_shape_gate:
        fragments = [item for item in fragments if "--touchstone-shape" not in item and "--touchstone-max-shape" not in item]
    if omit_quality_fragment is not None:
        fragments = [item for item in fragments if item != omit_quality_fragment]
    quality_args = "".join(f"  {fragment} \\\n" for fragment in fragments).rstrip(" \\\n") + "\n"
    progress_args = "".join(f"  {fragment} \\\n" for fragment in verify.PROGRESS_AUDIT_REQUIRED_FRAGMENTS[1:]).rstrip(" \\\n") + "\n"
    command.write_text(
        "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}\n"
        f"{'/home/researcher/.venv/bin/python' if local_path else '.venv/bin/python'} scripts/preflight_dataset_config.py \"$CONFIG\"\n"
        ".venv/bin/python scripts/audit_mars_run_progress.py \"$RUN_DIR\" \\\n"
        + progress_args
        + ".venv/bin/python scripts/run_dataset_quality_gates.py \"$RUN_DIR\" \\\n"
        + quality_args,
        encoding="utf-8",
    )
    compare_args = "".join(f"  {fragment} \\\n" for fragment in verify.COMPARE_GATE_REQUIRED_FRAGMENTS[1:]).rstrip(" \\\n") + "\n"
    (root / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md").write_text(
        "## HFSS/EMX compare\n\n"
        ".venv/bin/python scripts/compare_emx_hfss_ads.py \\\n"
        + compare_args
        + "\n"
        + ".venv/bin/python scripts/verify_accepted_emx_hfss_ads_figures.py \\\n"
        + "  --accepted-summary /path/to/accepted_emx_hfss_ads_validation_summary.json\n"
        + ".venv/bin/python scripts/run_accepted_emx_hfss_ads_validation.py \\\n"
        + "  --hfss-geometry-summary /path/to/hfss_model_geometry_asset_audit_summary.json\n\n"
        + "overall_status=PASS\n"
        + "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS\n"
        + "decision=ACCEPT_FINAL_LP_LS_Q_K_FIGURES\n"
        + "DO_NOT_USE_FINAL_LP_LS_Q_K_FIGURES\n"
        + "ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md\n"
        + "Lp/Ls/Qp/Qs/K figures must remain diagnostic or blocked\n",
        encoding="utf-8",
    )
    target_emx_command = root / "project_runbook" / "target_emx_wideband_rerun_20260613" / "target_emx_wideband_rerun.commands.sh"
    target_emx_command.parent.mkdir(parents=True, exist_ok=True)
    target_emx_command.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "mkdir -p /home/researcher/project/runs/evaluations/ec6698dfc575950b/emx_wideband_5_50_0p1\n"
        "/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx "
        "/home/researcher/project/runs/cadence_batches/batch/streamout/transformer_layout_cadpins.gds "
        "TRANSFORMER_021_ec6698df /path/to/pdk/proc.proc "
        "--touchstone --s-impedance=50 -s "
        "/home/researcher/project/runs/evaluations/ec6698dfc575950b/emx_wideband_5_50_0p1/emx.s4p "
        "--include-command-line --edge-width=1 --accuracy=standard --verbose=2 --cadence-pins=51 "
        "--port=P001=P001:P001_G --port=P002=P002:P002_G --port=P003=P003:P003_G --port=P004=P004:P004_G "
        f"{_target_emx_frequency_tokens(wrong=wrong_target_emx_frequency_list)}\n",
        encoding="utf-8",
    )
    target_emx_postrun_command = (
        root
        / "project_runbook"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_postrun_validation.commands.sh"
    )
    target_emx_postrun_command.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -s "$EMX_S4P"\n'
        'sha256sum "$EMX_S4P"\n'
        ".venv/bin/python scripts/audit_touchstone_transformer.py \"$EMX_S4P\" "
        "--expected-source-kind EMX "
        "--expected-frequency-start-ghz 5.0 --expected-frequency-stop-ghz 50.0 "
        "--expected-frequency-step-ghz 0.1 --expected-frequency-points 451 "
        "--positive-window-start-ghz 5.0 --positive-window-stop-ghz 30.0 "
        "--min-target-abs-k 0.05 --min-window-abs-k 0.05 "
        "--shape-window-start-ghz 5.0 --shape-window-stop-ghz 30.0\n"
        ".venv/bin/python scripts/build_emx_first_validation_gate.py --emx-s4p \"$EMX_S4P\" "
        "--required-sweep-start-ghz 5.0 --required-sweep-stop-ghz 50.0 "
        "--required-sweep-step-ghz 0.1 --required-sweep-points 451 "
        "--physical-window-start-ghz 5.0 --physical-window-stop-ghz 30.0 "
        "--max-shape-spike-ratio 4 --max-shape-relative-step 0.25 "
        "--photo-max-percent-error 5.0\n"
        'tar -czf "$TRANSFER_TARBALL" "$OUT_DIR"\n',
        encoding="utf-8",
    )
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
        encoding="utf-8",
    )


class VerifyMarsHandoffInstallScriptTest(TransformerToolboxTestBase):
    def test_valid_handoff_passes(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)

            status = verify.main([str(root), "--out-dir", str(root / "verify")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["path patcher smoke"]["status"], "PASS")
            self.assertEqual(checks["path discovery helper contract"]["status"], "PASS")
            self.assertEqual(checks["run-progress command contract"]["status"], "PASS")
            self.assertEqual(checks["dataset package helper contract"]["status"], "PASS")
            self.assertEqual(checks["dataset package verifier contract"]["status"], "PASS")
            self.assertEqual(checks["package source clearance contract"]["status"], "PASS")
            self.assertEqual(checks["target EMX post-run validation command contract"]["status"], "PASS")
            self.assertEqual(checks["accepted EMX/HFSS final runner contract"]["status"], "PASS")
            self.assertEqual(checks["accepted final figure verifier contract"]["status"], "PASS")
            self.assertEqual(checks["accepted final figure verifier runbook contract"]["status"], "PASS")
            self.assertEqual(checks["EMX-first gate script contract"]["status"], "PASS")
            self.assertEqual(checks["target EMX post-run import verifier contract"]["status"], "PASS")
            self.assertEqual(checks["Touchstone transformer audit contract"]["status"], "PASS")
            self.assertEqual(checks["target EMX return watcher contract"]["status"], "PASS")
            self.assertEqual(checks["target-envelope quality scripts contract"]["status"], "PASS")

    def test_missing_final_runner_verifier_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            runner = root / "scripts" / "run_accepted_emx_hfss_ads_validation.py"
            runner.write_text(runner.read_text(encoding="utf-8").replace("accepted EMX import verifier evidence", ""), encoding="utf-8")
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted EMX/HFSS final runner contract"]["status"], "FAIL")
            self.assertIn("accepted EMX import verifier evidence", checks["accepted EMX/HFSS final runner contract"]["detail"])

    def test_missing_final_figure_verifier_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            verifier = root / "scripts" / "verify_accepted_emx_hfss_ads_figures.py"
            verifier.write_text(
                verifier.read_text(encoding="utf-8").replace("ACCEPT_FINAL_LP_LS_Q_K_FIGURES", ""),
                encoding="utf-8",
            )
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted final figure verifier contract"]["status"], "FAIL")
            self.assertIn("ACCEPT_FINAL_LP_LS_Q_K_FIGURES", checks["accepted final figure verifier contract"]["detail"])

    def test_missing_final_figure_verifier_runbook_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            runbook = root / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
            runbook.write_text(
                runbook.read_text(encoding="utf-8").replace("scripts/verify_accepted_emx_hfss_ads_figures.py", ""),
                encoding="utf-8",
            )
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["accepted final figure verifier runbook contract"]["status"], "FAIL")
            self.assertIn(
                "scripts/verify_accepted_emx_hfss_ads_figures.py",
                checks["accepted final figure verifier runbook contract"]["detail"],
            )

    def test_stale_emx_first_gate_script_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            gate = root / "scripts" / "build_emx_first_validation_gate.py"
            gate.write_text("def x():\n    return 'target transformer sanity'\n", encoding="utf-8")
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first gate script contract"]["status"], "FAIL")
            self.assertIn("target transformer sanity", checks["EMX-first gate script contract"]["detail"])

    def test_stale_target_emx_import_verifier_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            verifier = root / "scripts" / "verify_target_emx_postrun_package.py"
            verifier.write_text(
                "def old_verifier():\n"
                "    return 'ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS without sha format or non-empty artifact gates'\n",
                encoding="utf-8",
            )
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target EMX post-run import verifier contract"]["status"], "FAIL")
            self.assertIn("accepted_emx_reference_bundle", checks["target EMX post-run import verifier contract"]["detail"])

    def test_stale_touchstone_transformer_audit_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            audit_script = root / "scripts" / "audit_touchstone_transformer.py"
            audit_script.write_text("def old_touchstone_gate():\n    return 'median step only'\n", encoding="utf-8")
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Touchstone transformer audit contract"]["status"], "FAIL")
            self.assertIn("bad_step_count", checks["Touchstone transformer audit contract"]["detail"])

    def test_stale_target_emx_return_watcher_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            watcher = root / "scripts" / "watch_mars_emx_return.py"
            watcher.write_text(
                "def old_watcher():\n"
                "    return 'only checks whether a file exists'\n",
                encoding="utf-8",
            )
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target EMX return watcher contract"]["status"], "FAIL")
            self.assertIn("discover_and_verify_mars_emx_return.py", checks["target EMX return watcher contract"]["detail"])

    def test_stale_target_envelope_quality_script_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            response_audit = root / "scripts" / "audit_response_feature_coverage.py"
            response_audit.write_text(response_audit.read_text(encoding="utf-8").replace("--target-k-min", ""), encoding="utf-8")
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target-envelope quality scripts contract"]["status"], "FAIL")
            self.assertIn("audit_response_feature_coverage.py", checks["target-envelope quality scripts contract"]["detail"])

    def test_missing_shape_gate_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root, include_shape_gate=False)

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Touchstone shape-window gate"]["status"], "FAIL")

    def test_missing_compare_grid_gate_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            runbook = root / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
            runbook.write_text(runbook.read_text(encoding="utf-8").replace("  --require-matching-frequency-grid \\\n", ""), encoding="utf-8")

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["HFSS/EMX compare grid gate"]["status"], "FAIL")
            self.assertIn("--require-matching-frequency-grid", checks["HFSS/EMX compare grid gate"]["detail"])

    def test_local_path_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root, local_path=True)

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["portable wideband commands"]["status"], "FAIL")

    def test_wrong_wideband_frequency_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root, wrong_frequency=True)

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["wideband config contract"]["status"], "FAIL")
            self.assertIn("frequency_start_hz", checks["wideband config contract"]["detail"])

    def test_wrong_target_emx_frequency_list_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root, wrong_target_emx_frequency_list=True)

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["target EMX wideband rerun command contract"]["status"], "FAIL")
            self.assertIn("451 points", checks["target EMX wideband rerun command contract"]["detail"])

    def test_missing_quality_gate_contract_arg_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root, omit_quality_fragment="--audit-zin-coverage")

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["quality-gate command contract"]["status"], "FAIL")
            self.assertIn("--audit-zin-coverage", checks["quality-gate command contract"]["detail"])

    def test_old_package_helper_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            helper = root / "scripts" / "package_mars_dataset_run.py"
            helper.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('run_dir')\n"
                "if __name__ == '__main__': parser.parse_args()\n",
                encoding="utf-8",
            )
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["dataset package helper contract"]["status"], "FAIL")
            self.assertIn("--report", checks["dataset package helper contract"]["detail"])

    def test_old_package_verifier_contract_fails(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)
            verifier = root / "scripts" / "verify_mars_dataset_package.py"
            verifier.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('tarball')\n"
                "if __name__ == '__main__': parser.parse_args()\n",
                encoding="utf-8",
            )
            files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
            (root / "SHA256SUMS.txt").write_text(
                "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
                encoding="utf-8",
            )

            status = verify.main([str(root), "--out-dir", str(root / "verify"), "--no-fail-exit"])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["dataset package verifier contract"]["status"], "FAIL")
            self.assertIn("--require-quality-gates", checks["dataset package verifier contract"]["detail"])

    def test_valid_handoff_passes_without_pyyaml(self) -> None:
        verify = _load_verify_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_handoff(root)

            with mock.patch("builtins.__import__", side_effect=_block_yaml_import):
                status = verify.main([str(root), "--out-dir", str(root / "verify")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "verify" / "mars_handoff_verify_summary.json").read_text(encoding="utf-8"))
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["wideband config contract"]["status"], "PASS")
