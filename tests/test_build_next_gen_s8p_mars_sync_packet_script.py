from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import os
import subprocess
import sys
import tarfile


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_next_gen_s8p_mars_sync_packet.py"
    spec = importlib.util.spec_from_file_location("build_next_gen_s8p_mars_sync_packet_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_required_repo(repo: Path) -> None:
    mod = _load_module()
    for rel in mod.REQUIRED_REPO_FILES:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    for rel in [
        "scripts/extra_helper.py",
        "rfic_transformer_inverse_design/__init__.py",
        "rfic_transformer_inverse_design/layout/__init__.py",
        "rfic_transformer_inverse_design/execution/__init__.py",
        "rfic_transformer_inverse_design/layout/ignored.pyc",
    ]:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".pyc":
            path.write_bytes(b"compiled")
        else:
            path.write_text(f"# {rel}\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")


def _write_execution_packet(project: Path) -> None:
    mod = _load_module()
    packet = project / mod.EXECUTION_PACKET_NAME
    packet.mkdir(parents=True, exist_ok=True)
    for rel in mod.REQUIRED_EXECUTION_PACKET_FILES:
        path = packet / rel
        path.write_text(f"# {rel}\n", encoding="utf-8")
        if path.suffix == ".sh":
            path.chmod(0o755)


def _write_required_evidence(project: Path) -> None:
    required = [
        "outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_port_map_approval_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_gated_unapproved/physical_feature_s8p_launch_packet_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_candidate/s8p_geometry_contract_approval_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_board.png",
        "outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/s8p_geometry_contract_approval_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_board.png",
        "outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_summary.json",
        "outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch.commands.sh",
        "outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_report.md",
        "outputs/next_gen_s8p_goal_readiness_user_approved_ready_20260619/next_gen_s8p_goal_readiness_summary.json",
        "outputs/s8p_mars_path_guard_verification_20260619/01_discovery_rejects_dryrun/mars_emx_cadence_path_discovery_summary.json",
        "reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz",
        "reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz.sha256",
    ]
    for rel in required:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".png":
            path.write_bytes(b"fake-png-for-packet-test")
        else:
            path.write_text(json.dumps({"status": "test", "path": rel}), encoding="utf-8")


def _write_recovery_files(project: Path) -> None:
    mod = _load_module()
    for name in mod.RECOVERY_FILE_NAMES:
        path = project / name
        if name == "NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh":
            path.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "echo '== Objective acceptance audit =='",
                        "echo next_gen_s8p_objective_acceptance_summary.json",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        else:
            path.write_text(f"# {name}\n", encoding="utf-8")
        if path.suffix == ".sh":
            path.chmod(0o755)


class BuildNextGenS8pMarsSyncPacketScriptTest(TransformerToolboxTestBase):
    def test_builds_traceable_sync_packet(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            repo.mkdir()
            project.mkdir()
            _write_required_repo(repo)
            _write_execution_packet(project)
            _write_required_evidence(project)
            _write_recovery_files(project)

            status = mod.main(
                [
                    "--repo-root",
                    str(repo),
                    "--project-root",
                    str(project),
                    "--execution-packet-dir",
                    str(project / mod.EXECUTION_PACKET_NAME),
                    "--packet-dir",
                    str(root / mod.PACKET_NAME),
                    "--tar-path",
                    str(root / f"{mod.PACKET_NAME}.tar.gz"),
                    "--bootstrap-path",
                    str(root / f"{mod.PACKET_NAME}_BOOTSTRAP.sh"),
                    "--report-dir",
                    str(root / "report"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "report" / "next_gen_s8p_mars_sync_packet_summary_20260619.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["decision"], "NEXT_GEN_S8P_MARS_SYNC_PACKET_READY")
            packet = root / mod.PACKET_NAME
            self.assertTrue((packet / "INSTALL_ON_MARS.sh").stat().st_mode & 0o111)
            installer = (packet / "INSTALL_ON_MARS.sh").read_text(encoding="utf-8")
            self.assertIn("Approved real EMX launch command", installer)
            self.assertIn(f"RUN_EMX=1 bash {mod.EXECUTION_PACKET_NAME}/next_gen_s8p_mars_execution.commands.sh", installer)
            self.assertNotIn("RUN_REAL_EMX=1", installer)
            self.assertIn("NEXT_GEN_S8P_POST_LOGIN", installer)
            self.assertIn("NEXT_GEN_S8P_START_CURRENT", installer)
            self.assertIn("NEXT_GEN_S8P_BOOTSTRAPPED_PROJECT", installer)
            self.assertIn("NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh", installer)
            self.assertIn("NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh", installer)
            self.assertIn("NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh", installer)
            self.assertNotIn("Next real EMX command after review", installer)
            self.assertIn(mod.EXECUTION_PACKET_NAME, installer)
            self.assertIn("next_gen_s8p_evidence_20260619", installer)
            self.assertIn("files/project_recovery", installer)
            self.assertIn(
                f'cp -R "$PACKET_DIR/files/project_runbooks/{mod.EXECUTION_PACKET_NAME}/." "{mod.EXECUTION_PACKET_NAME}/"',
                installer,
            )
            self.assertNotIn(f'cp -R "$PACKET_DIR/files/project_runbooks/{mod.EXECUTION_PACKET_NAME}" "{mod.EXECUTION_PACKET_NAME}"', installer)
            self.assertIn("cp -R \"$PACKET_DIR/files/evidence/outputs/.\" outputs/", installer)
            self.assertIn('"${PYTHON:-python3}" - "$RUNBOOK"', installer)
            self.assertTrue((root / f"{mod.PACKET_NAME}.tar.gz.sha256").is_file())
            self.assertTrue((root / f"{mod.PACKET_NAME}_BOOTSTRAP.sh.sha256").is_file())
            with tarfile.open(root / f"{mod.PACKET_NAME}.tar.gz", "r:gz") as tar:
                names = set(tar.getnames())
                status_check = tar.extractfile(
                    f"{mod.PACKET_NAME}/files/project_recovery/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh"
                )
                self.assertIsNotNone(status_check)
                status_check_text = status_check.read().decode("utf-8") if status_check is not None else ""
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/build_physical_feature_s8p_launch_packet.py", names)
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/build_next_gen_s8p_objective_acceptance_audit.py", names)
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/export_final_valid_emx_s8p_samples.py", names)
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/import_stage1_mars_calibration_return.py", names)
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/import_latest_s8p_20_pilot_return.py", names)
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/watch_s8p_20_pilot_return_and_process.py", names)
            self.assertIn(f"{mod.PACKET_NAME}/files/repo/scripts/run_gated_s8p_million_sample_campaign.py", names)
            self.assertIn(
                f"{mod.PACKET_NAME}/files/project_runbooks/{mod.EXECUTION_PACKET_NAME}/next_gen_s8p_mars_execution.commands.sh",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/project_recovery/NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/project_recovery/NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/project_recovery/NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/project_recovery/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh",
                names,
            )
            self.assertIn("== Objective acceptance audit ==", status_check_text)
            self.assertIn("next_gen_s8p_objective_acceptance_summary.json", status_check_text)
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_port_map_approval_summary.json",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_gated_unapproved/physical_feature_s8p_launch_packet_summary.json",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_candidate/s8p_geometry_contract_approval_summary.json",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_summary.json",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_board.png",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_summary.json",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch.commands.sh",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_report.md",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz",
                names,
            )
            self.assertIn(
                f"{mod.PACKET_NAME}/files/evidence/reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz.sha256",
                names,
            )
            self.assertNotIn(f"{mod.PACKET_NAME}/files/repo/rfic_transformer_inverse_design/layout/ignored.pyc", names)

    def test_readme_uses_actual_packet_dir_name(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            repo.mkdir()
            project.mkdir()
            _write_required_repo(repo)
            _write_execution_packet(project)
            _write_required_evidence(project)
            _write_recovery_files(project)

            packet_name = "next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix"
            status = mod.main(
                [
                    "--repo-root",
                    str(repo),
                    "--project-root",
                    str(project),
                    "--execution-packet-dir",
                    str(project / mod.EXECUTION_PACKET_NAME),
                    "--packet-dir",
                    str(root / packet_name),
                    "--tar-path",
                    str(root / f"{packet_name}.tar.gz"),
                    "--bootstrap-path",
                    str(root / f"{packet_name}_BOOTSTRAP.sh"),
                    "--report-dir",
                    str(root / "report"),
                ]
            )

            self.assertEqual(status, 0)
            readme = (root / packet_name / "README_CN.md").read_text(encoding="utf-8")
            self.assertIn(f"解压 `{packet_name}.tar.gz`", readme)
            self.assertIn(f"bash {packet_name}/INSTALL_ON_MARS.sh", readme)
            self.assertNotIn(f"解压 `{mod.PACKET_NAME}.tar.gz`", readme)

    def test_installer_bootstraps_missing_project_directory(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            repo.mkdir()
            project.mkdir()
            _write_required_repo(repo)
            _write_execution_packet(project)
            _write_required_evidence(project)
            _write_recovery_files(project)

            status = mod.main(
                [
                    "--repo-root",
                    str(repo),
                    "--project-root",
                    str(project),
                    "--execution-packet-dir",
                    str(project / mod.EXECUTION_PACKET_NAME),
                    "--packet-dir",
                    str(root / mod.PACKET_NAME),
                    "--tar-path",
                    str(root / f"{mod.PACKET_NAME}.tar.gz"),
                    "--bootstrap-path",
                    str(root / f"{mod.PACKET_NAME}_BOOTSTRAP.sh"),
                    "--report-dir",
                    str(root / "report"),
                ]
            )
            self.assertEqual(status, 0)

            target = root / "remote-empty-project"
            result = subprocess.run(
                ["bash", str(root / mod.PACKET_NAME / "INSTALL_ON_MARS.sh")],
                cwd=root,
                env={**os.environ, "PROJECT": str(target)},
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn(f"NEXT_GEN_S8P_BOOTSTRAPPED_PROJECT={target}", result.stdout)
            self.assertTrue((target / "pyproject.toml").is_file())
            self.assertTrue((target / "scripts" / "build_physical_feature_s8p_launch_packet.py").is_file())
            self.assertTrue((target / mod.EXECUTION_PACKET_NAME / "next_gen_s8p_mars_execution.commands.sh").is_file())
            self.assertTrue((target / "NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh").is_file())
            self.assertTrue((target / "NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh").is_file())

            second = subprocess.run(
                ["bash", str(root / mod.PACKET_NAME / "INSTALL_ON_MARS.sh")],
                cwd=root,
                env={**os.environ, "PROJECT": str(target)},
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn(f"NEXT_GEN_S8P_FOUND_PROJECT={target}", second.stdout)
            self.assertFalse((target / mod.EXECUTION_PACKET_NAME / mod.EXECUTION_PACKET_NAME).exists())

    def test_missing_execution_packet_fails(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo = root / "repo"
            project = root / "project"
            repo.mkdir()
            project.mkdir()
            _write_required_repo(repo)

            with self.assertRaises(SystemExit) as ctx:
                mod.main(
                    [
                        "--repo-root",
                        str(repo),
                        "--project-root",
                        str(project),
                        "--execution-packet-dir",
                        str(project / mod.EXECUTION_PACKET_NAME),
                        "--packet-dir",
                        str(root / mod.PACKET_NAME),
                        "--tar-path",
                        str(root / f"{mod.PACKET_NAME}.tar.gz"),
                        "--bootstrap-path",
                        str(root / f"{mod.PACKET_NAME}_BOOTSTRAP.sh"),
                    ]
                )

            self.assertIn(mod.EXECUTION_PACKET_NAME, str(ctx.exception))
