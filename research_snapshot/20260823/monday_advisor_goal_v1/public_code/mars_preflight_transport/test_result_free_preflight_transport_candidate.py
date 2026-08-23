#!/usr/bin/env python3
"""Targeted result-blind tests for the prepared meta-candidate sources."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import unittest


ROOT = Path(__file__).resolve().parent
VERIFY_PATH = ROOT / "verify_result_free_preflight_transport_candidate.py"
SPEC = importlib.util.spec_from_file_location("meta_verify", VERIFY_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_index(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        assert len(line) >= 67 and line[64:66] == "  "
        result[line[66:]] = line[:64]
    return result


def exact_group(root: Path, expected_top: int, expected_index: int, index_name: str) -> None:
    entries = list(root.iterdir())
    if len(entries) != expected_top:
        raise AssertionError(f"{root}: top-level count {len(entries)} != {expected_top}")
    if any(not path.is_file() or path.is_symlink() for path in entries):
        raise AssertionError(f"{root}: non-regular top-level member")
    index = parse_index(root / index_name)
    if len(index) != expected_index:
        raise AssertionError(f"{root}: index count {len(index)} != {expected_index}")
    for name, expected in index.items():
        if digest(root / name) != expected:
            raise AssertionError(f"{root}/{name}: SHA mismatch")


def positive_snapshot() -> dict[str, object]:
    fake_sha = "a" * 64
    return {
        "host": "${MARS_HOST}",
        "watcher_present": True,
        "watcher_pid": 2901805,
        "watcher_ppid": 1,
        "watcher_state": "T",
        "full_cmdline_sha256": (
            "1b042949118aae7d3bc66e56a36a09a9f50cc14fedf66ad3ebdc6c4e4a53f83d"
        ),
        "boot_id": "synthetic-boot-id",
        "proc_start_ticks": 123456,
        "uid": 1234,
        "exe_path": "/synthetic/frozen/python",
        "exe_sha256": fake_sha,
        "script_path": "/synthetic/frozen/watcher.py",
        "script_sha256": fake_sha,
        "launch_receipt_path": "/synthetic/frozen/launch_receipt.json",
        "launch_receipt_sha256": fake_sha,
        "direct_children": 0,
        "matching_watcher_process_count": 1,
        "active_post_stage06_chain_processes": [],
        "stage07_output_exists": False,
        "stage08_output_exists": False,
        "production_runtime_root_identity_changed": False,
        "transport_receipt_bound": True,
        "native_preflight_terminal_pass_bound": True,
        "fresh_independent_candidate_go_bound": True,
        "separate_sigcont_authorization_bound": True,
        "pidfd_or_equivalent_identity_bound": True,
    }


class TestPreparedSourceBindings(unittest.TestCase):
    def test_contract_and_authority_boundary(self) -> None:
        contract = VERIFY.load_contract(ROOT / "MARS_STAGE07_08_RESUME_ONLY_CONTRACT.json")
        self.assertEqual(contract["status"], VERIFY.STATUS)
        self.assertFalse(contract["fresh_qa_boundary"]["fresh_auditor_go_authorizes_sigcont"])
        self.assertTrue(
            contract["fresh_qa_boundary"]["requires_separate_post_preflight_resume_authorization"]
        )

    def test_preflight_v3_exact_closure(self) -> None:
        root = ROOT / "upstream/preflight_v3_prepared"
        exact_group(root, 10, 9, "SHA256SUMS")
        self.assertEqual(
            digest(root / "SHA256SUMS"),
            "2688dd1e35adc2910e76ab210c23caa5d3f503658db704e2d7c618b512a3569b",
        )

    def test_transport_v10_exact_closure(self) -> None:
        root = ROOT / "upstream/transport_v10_prepared"
        exact_group(root, 15, 14, "SHA256SUMS")
        self.assertEqual(
            digest(root / "SHA256SUMS"),
            "e2073343323a19a153843079dd8b787c97929c02b6c9c4152fd03e0e2799acb2",
        )

    def test_transport_v10_independent_qa_exact_closure(self) -> None:
        root = ROOT / "upstream/transport_v10_independent_qa"
        exact_group(root, 14, 12, "SHA256SUMS")
        receipt = json.loads((root / "INDEPENDENT_QA_RECEIPT.json").read_text())
        self.assertEqual(receipt["finding_counts"], {"P0": 0, "P1": 0, "P2": 0, "P3": 0})
        self.assertEqual(
            receipt["action_scoped_verdict"],
            "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LOCAL_NATIVE_PREFLIGHT_PREREQUISITE_ONLY",
        )
        self.assertFalse(receipt["authority"]["signals_authorized"])
        self.assertFalse(receipt["authority"]["deployment_or_resume_authorized"])

    def test_root_redteam_is_supporting_only_and_exact(self) -> None:
        root = ROOT / "upstream/preflight_v3_root_redteam_supporting"
        self.assertEqual(len(list(root.iterdir())), 8)
        self.assertEqual(
            digest(root / "ROOT_REDTEAM_QA_HARNESS.py"),
            "c09d56ed3240c91cf53b4bbc251a2020205e4a2c9570ecbe36ee1f383e66ade7",
        )
        for name, expected in (
            (
                "ROOT_REDTEAM_QA_OUTPUT_ATTEMPT1.json",
                "4ddca376904106f2290ba7f5c2c4e87c2ea9d2450c8ce33af7efbfcd2f538f14",
            ),
            (
                "ROOT_REDTEAM_QA_OUTPUT_ATTEMPT2.json",
                "c57a36d2498cfbd8aff0ac8b8a96fbeb1f8223566590ea2b1a0ce189a2fb9844",
            ),
        ):
            self.assertEqual(digest(root / name), expected)
            output = json.loads((root / name).read_text())
            self.assertEqual((output["checked"], output["passed"], output["failed"]), (36, 36, 0))
        contract = json.loads((ROOT / "MARS_STAGE07_08_RESUME_ONLY_CONTRACT.json").read_text())
        binding = contract["upstream_exact_bindings"]["preflight_v3_root_redteam_supporting"]
        self.assertFalse(binding["independent"])
        self.assertTrue(binding["supporting_only"])

    def test_no_symlink_or_hardlink_in_upstream_closure(self) -> None:
        for current, dirnames, filenames in os.walk(ROOT / "upstream"):
            for name in dirnames + filenames:
                info = os.lstat(Path(current) / name)
                self.assertFalse(stat.S_ISLNK(info.st_mode))
                if stat.S_ISREG(info.st_mode):
                    self.assertEqual(info.st_nlink, 1)

    def test_verifier_has_no_network_process_or_signal_import(self) -> None:
        source = VERIFY_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "import socket",
            "import subprocess",
            "import signal",
            "import requests",
            "import paramiko",
            "os.kill(",
            "os.killpg(",
        ):
            self.assertNotIn(forbidden, source)


class TestFutureWatcherFailClosedMatrix(unittest.TestCase):
    def test_positive_synthetic_snapshot(self) -> None:
        result = VERIFY.evaluate_future_watcher_snapshot(positive_snapshot())
        self.assertEqual(result["target_pid"], 2901805)
        self.assertFalse(result["launch_replacement_allowed"])

    def assert_rejected(self, key: str, value: object) -> None:
        fixture = positive_snapshot()
        fixture[key] = value
        with self.assertRaises(VERIFY.VerificationError):
            VERIFY.evaluate_future_watcher_snapshot(fixture)

    def test_watcher_absent_rejected(self) -> None:
        self.assert_rejected("watcher_present", False)

    def test_pid_identity_drift_rejected(self) -> None:
        self.assert_rejected("watcher_pid", 2901806)

    def test_ppid_identity_drift_rejected(self) -> None:
        self.assert_rejected("watcher_ppid", 2)

    def test_running_state_rejected(self) -> None:
        self.assert_rejected("watcher_state", "S")

    def test_cmdline_drift_rejected(self) -> None:
        self.assert_rejected("full_cmdline_sha256", "b" * 64)

    def test_children_rejected(self) -> None:
        self.assert_rejected("direct_children", 1)

    def test_missing_watcher_signature_rejected(self) -> None:
        self.assert_rejected("matching_watcher_process_count", 0)

    def test_duplicate_watcher_signature_rejected(self) -> None:
        self.assert_rejected("matching_watcher_process_count", 2)

    def test_active_stage_process_rejected(self) -> None:
        self.assert_rejected("active_post_stage06_chain_processes", ["stage07"])

    def test_existing_stage07_output_rejected(self) -> None:
        self.assert_rejected("stage07_output_exists", True)

    def test_existing_stage08_output_rejected(self) -> None:
        self.assert_rejected("stage08_output_exists", True)

    def test_runtime_root_identity_drift_rejected(self) -> None:
        self.assert_rejected("production_runtime_root_identity_changed", True)

    def test_missing_transport_receipt_rejected(self) -> None:
        self.assert_rejected("transport_receipt_bound", False)

    def test_missing_native_preflight_pass_rejected(self) -> None:
        self.assert_rejected("native_preflight_terminal_pass_bound", False)

    def test_missing_fresh_qa_go_rejected(self) -> None:
        self.assert_rejected("fresh_independent_candidate_go_bound", False)

    def test_missing_separate_sigcont_authorization_rejected(self) -> None:
        self.assert_rejected("separate_sigcont_authorization_bound", False)

    def test_missing_pidfd_identity_rejected(self) -> None:
        self.assert_rejected("pidfd_or_equivalent_identity_bound", False)

    def test_missing_key_rejected(self) -> None:
        fixture = positive_snapshot()
        del fixture["boot_id"]
        with self.assertRaises(VERIFY.VerificationError):
            VERIFY.evaluate_future_watcher_snapshot(fixture)

    def test_boolean_integer_alias_rejected(self) -> None:
        self.assert_rejected("watcher_pid", True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
