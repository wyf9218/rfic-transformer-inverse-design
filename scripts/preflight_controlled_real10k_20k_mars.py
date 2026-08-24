#!/usr/bin/env python3
"""Result-blind native-MARS preflight for controlled real-EMX 10K/20K work.

This program never invokes the materialization builder, paired runner, trainer,
EMX, or a process signal.  It verifies an immutable package and an exact
external CODE_GO receipt, probes the frozen Linux runtime, and optionally runs
only the exact reviewed isolated native-smoke script.  A PASS receipt is still
not launch authority: it permits a separate reviewer to consider signing one
result-blind data-materialization action.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


PACKAGE_SCHEMA = "controlled_real10k_20k_mars_package_v2"
PACKAGE_VERSION = "v5"
PACKAGE_RECEIPT_SCHEMA = "controlled_real10k_20k_mars_package_receipt_v2"
QA_REQUIRED_SCHEMA = "controlled_real10k_20k_mars_package_independent_qa_required_v3"
PACKAGE_COMMIT_SCHEMA = "controlled_real10k_20k_mars_package_commit_v2"
PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_body_v3"
)
PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
PACKAGE_BUILD_ATTEMPT_BODY_NAME = "PACKAGE_BUILD_ATTEMPT_RECEIPT.json"
PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME = "PACKAGE_BUILD_ATTEMPT_COMMITTED.json"
CODE_GO_SCHEMA = "controlled_real10k_20k_mars_code_go_v3"
CODE_GO_SCOPE = "MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY"
CODE_GO_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "status",
        "verdict",
        "scope",
        "issued_utc",
        "expires_utc",
        "nonce",
        "review",
        "findings",
        "bindings",
        "authorities",
    }
)
PREPARED_SCHEMA = "controlled_real10k_20k_mars_preflight_prepared_v3"
EXECUTION_QA_REQUIRED_SCHEMA = (
    "controlled_real10k_20k_mars_preflight_execution_qa_required_v3"
)
PREFLIGHT_SCHEMA = "controlled_real10k_20k_mars_preflight_receipt_body_v3"
PREFLIGHT_COMMITTED_SCHEMA = "controlled_real10k_20k_mars_preflight_committed_v3"
PREFLIGHT_FAILURE_SCHEMA = "controlled_real10k_20k_mars_preflight_fatal_fail_v3"
PREFLIGHT_LEASE_SCHEMA = "controlled_real10k_20k_mars_preflight_one_use_lease_v3"
MANIFEST_NAME = "MANIFEST.json"
RECEIPT_NAME = "RECEIPT.json"
SHA_INDEX_NAME = "SHA256SUMS.txt"
QA_REQUIRED_NAME = "INDEPENDENT_QA_REQUIRED.json"
PACKAGE_COMMIT_NAME = "PACKAGE_COMMIT.json"
PACKAGE_SINGLETON_LOCK_NAME = "CONTROLLED_SINGLETON.lock"
FILE_MODE = 0o444
DIRECTORY_MODE = 0o555
EXPECTED_PYTHON_VERSION = "3.12.13"
EXPECTED_NUMPY_VERSION = "2.5.0"
FROZEN_AUTHORITATIVE_100K_SHA256 = (
    "68468eb2d3678aa0793157c1c647e975f60e8ec1673c259050ababe9fd1ff08a"
)
FROZEN_HISTORICAL_10K_SHA256 = (
    "3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8"
)
FROZEN_HISTORICAL_SUMMARY_SHA256 = (
    "90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa"
)
FROZEN_TRAINER_SHA256 = (
    "92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be"
)
FROZEN_PREREGISTRATION_V1_SHA256 = (
    "19aca7778f4974fd3e7eadaca8b291783e8e08e99a53a9dca70b070a4bf16417"
)
FROZEN_PREREGISTRATION_ADDENDUM_V1_1_SHA256 = (
    "9f1eb0e071ade0e5a42597b4242409282ed8d34cf159104f71df2d4d0d0a8633"
)
FROZEN_PREREGISTRATION_ADDENDUM_V1_2_SHA256 = (
    "fb7c7d0f9e206e3743cf795a544004e570842f26495903ad0eafdd5f909f37a9"
)
REQUIRED_SOURCE_ROLES = {
    "authoritative_100k_csv": FROZEN_AUTHORITATIVE_100K_SHA256,
    "historical_10k_csv": FROZEN_HISTORICAL_10K_SHA256,
    "historical_model_summary_json": FROZEN_HISTORICAL_SUMMARY_SHA256,
}
REQUIRED_PREREGISTRATION_ROLES = {
    "preregistration_v1_json": FROZEN_PREREGISTRATION_V1_SHA256,
    "preregistration_addendum_v1_1_json": (
        FROZEN_PREREGISTRATION_ADDENDUM_V1_1_SHA256
    ),
    "preregistration_addendum_v1_2_json": (
        FROZEN_PREREGISTRATION_ADDENDUM_V1_2_SHA256
    ),
}
REQUIRED_CODE_ROLES = frozenset(
    {
        "package_builder_code",
        "runtime_bootstrap_code",
        "preflight_code",
        "materialization_builder_code",
        "materialization_gate_code",
        "splitter_code",
        "runner_code",
        "trainer_code",
        "evaluator_code",
        "runtime_package_init_code",
        "shared_contract_code",
    }
)
REQUIRED_NATIVE_TEST_ROLES = ("native_smoke_test",)
REQUIRED_CANDIDATE_OUTPUT_NAMES = (
    "materialization_gate_candidate_v1",
    "materialization_output_v1",
    "materialization_execution_receipt_v1",
)
PACKAGE_REQUIRED_ROLES = frozenset(
    REQUIRED_CODE_ROLES
    | set(REQUIRED_SOURCE_ROLES)
    | set(REQUIRED_PREREGISTRATION_ROLES)
    | set(REQUIRED_NATIVE_TEST_ROLES)
    | {
        "runtime_dependency_closure_tree",
        "runtime_dependency_closure_json",
        "process_singleton_contract_json",
    }
)
PACKAGE_ROLE_DESTINATIONS = {
    "package_builder_code": "builder/build_controlled_real10k_20k_mars_package.py",
    "runtime_bootstrap_code": "runtime/bootstrap/controlled_real10k_20k_runtime_bootstrap.py",
    "preflight_code": "runtime/scripts/preflight_controlled_real10k_20k_mars.py",
    "materialization_builder_code": "runtime/scripts/build_controlled_real10k_20k_nested.py",
    "materialization_gate_code": "runtime/scripts/run_controlled_real10k_20k_materialization.py",
    "runner_code": "runtime/scripts/run_controlled_real10k_20k_paired.py",
    "trainer_code": "runtime/scripts/train_physical_feature_tandem_inverse.py",
    "evaluator_code": "runtime/scripts/evaluate_controlled_real10k_20k_common.py",
    "runtime_package_init_code": "runtime/rfic_transformer_inverse_design/__init__.py",
    "shared_contract_code": "runtime/rfic_transformer_inverse_design/controlled_real10k_20k_contract.py",
    "splitter_code": "runtime/rfic_transformer_inverse_design/model_splitting.py",
    "runtime_dependency_closure_tree": "runtime/dependencies",
    "runtime_dependency_closure_json": "runtime/contracts/RUNTIME_CLOSURE.json",
    "process_singleton_contract_json": "runtime/contracts/PROCESS_SINGLETON_CONTRACT.json",
    "native_smoke_test": "runtime/tests/controlled_real10k_20k_mars_native_smoke.py",
    "preregistration_v1_json": "protocol/CONTROLLED_EXPERIMENT_PREREGISTRATION_V1.json",
    "preregistration_addendum_v1_1_json": "protocol/CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_1.json",
    "preregistration_addendum_v1_2_json": "protocol/CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_2.json",
    "authoritative_100k_csv": "inputs/authoritative_100k/physical_feature_inverse_training_table.csv",
    "historical_10k_csv": "inputs/historical_10k/multifrequency_physical_feature_training_table.csv",
    "historical_model_summary_json": "inputs/historical_model/physical_feature_tandem_inverse_summary.json",
}
PACKAGE_REQUIRED_GO_BINDING_KEYS = (
    "candidate_output_dirs",
    "code_role_identity",
    "host_expected",
    "native_test_roles",
    "package_build_attempt_body",
    "package_build_attempt_committed",
    "package_commit",
    "package_independent_qa_required",
    "package_manifest",
    "package_receipt",
    "package_role_identity",
    "package_sha_index",
    "preflight_implementation",
    "preflight_one_use_lease",
    "preflight_receipt_root",
    "preflight_terminal_commit",
    "preregistration_role_identity",
    "process_singleton_contract",
    "process_singleton_lock",
    "runtime_dependency_closure",
    "runtime_entrypoints",
    "runtime_expected",
    "source_role_identity",
)


def _expected_process_singleton_contract() -> dict[str, Any]:
    execution_identities = {
        "evaluator_code": ("sealed_runtime_entrypoint", "evaluator"),
        "materialization_builder_code": ("sealed_in_process_member", "materialization"),
        "materialization_gate_code": ("sealed_runtime_entrypoint", "materialization"),
        "native_smoke_test": ("sealed_runtime_entrypoint", "native_smoke"),
        "preflight_code": ("raw_hash_bound_script", None),
        "runner_code": ("sealed_runtime_entrypoint", "runner"),
        "runtime_bootstrap_code": ("sealed_bootstrap_fd", None),
        "trainer_code": ("sealed_runtime_entrypoint", "trainer"),
    }
    controller_roles = {
        "evaluator_code",
        "materialization_gate_code",
        "preflight_code",
        "runner_code",
    }
    return {
        "schema": "controlled_real10k_20k_process_singleton_contract_v1",
        "lock": {
            "relative_path": PACKAGE_SINGLETON_LOCK_NAME,
            "basename": PACKAGE_SINGLETON_LOCK_NAME,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "required_mode_octal": "0444",
            "required_nlink": 1,
            "mechanism": "fcntl.flock",
            "operation": "LOCK_EX|LOCK_NB",
            "open_flags": ["O_CLOEXEC", "O_NOFOLLOW", "O_RDONLY"],
            "scope": "one_active_controlled_controller_per_package_identity",
        },
        "protected_entrypoints": [
            {
                "role": role,
                "path": PACKAGE_ROLE_DESTINATIONS[role],
                "controller": role in controller_roles,
                "execution_identity": execution_identities[role][0],
                "runtime_entrypoint": execution_identities[role][1],
            }
            for role in sorted(execution_identities)
        ],
        "proc_audit": {
            "platform": "Linux",
            "proc_root": "/proc",
            "uid_scope": "current_effective_uid",
            "read_only": True,
            "performed_after_lock_acquisition": True,
            "self_pid_excluded": True,
            "substring_matching_allowed": False,
            "identity_sources": [
                "/proc/<pid>/cmdline",
                "/proc/<pid>/exe",
                "/proc/<pid>/fd/200",
                "/proc/<pid>/fd/201",
                "/proc/<pid>/fd/202",
                "/proc/<pid>/fd/203",
                "/proc/<pid>/status:Uid",
            ],
            "exact_match_fields": [
                "argv_bytes",
                "executable_device_inode_sha256",
                "raw_preflight_script_device_inode_sha256",
                "sealed_bootstrap_fd_200_sha256",
                "sealed_manifest_fd_202_sha256",
                "sealed_pure_archive_fd_203_sha256",
                "sealed_request_fd_201_entrypoint_and_sha_bindings",
                "script_role_from_package_manifest",
            ],
            "sealed_descriptor_numbers": {
                "bootstrap": 200,
                "request": 201,
                "manifest": 202,
                "pure_archive": 203,
            },
            "sealed_request_required_bindings": [
                "entrypoint",
                "expected_bootstrap_sha256",
                "expected_manifest_sha256",
                "expected_pure_archive_sha256",
            ],
            "raw_script_identity_roles": ["preflight_code"],
            "all_matching_pids_reported": True,
        },
        "lifetime": {
            "owner": "top_level_controller_process",
            "acquire_before": "EXECUTE_state_audit_and_any_controlled_child_launch",
            "held_across_all_controlled_children": True,
            "release_after": "terminal_receipt_or_commit_file_and_parent_directory_fsync",
            "full_lifetime_required": True,
        },
        "conflict_policy": {
            "verdict": "NO_GO_DUPLICATE_CONTROLLED_PROCESS",
            "controlled_process_start_authorized": False,
            "process_signal_authorized": False,
            "process_kill_authorized": False,
            "automatic_cleanup_authorized": False,
        },
    }
PACKAGE_QA_AUTHORITIES = {
    "native_linux_test_execution": False,
    "data_materialization": False,
    "training": False,
    "common_test_access": False,
    "numerical_metric_access": False,
    "fresh_emx": False,
    "process_signal": False,
}
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
BOOT_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
GO_NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
GO_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_CODE_GO_LIFETIME = timedelta(hours=24)
NATIVE_SMOKE_REQUEST_SCHEMA = "controlled_real10k_20k_native_smoke_request_v3"
NATIVE_SMOKE_RESULT_SCHEMA = "controlled_real10k_20k_native_smoke_result_v3"
NATIVE_SMOKE_TEST_ID = "descriptor_closed_package_consumer_graph_v5"
RUNTIME_ATTESTATION_SCHEMA = "controlled_real10k_20k_runtime_attestation_v1"
PREPARED_NAME = "PREFLIGHT_PREPARED.json"
EXECUTION_QA_REQUIRED_NAME = "PREFLIGHT_EXECUTION_QA_REQUIRED.json"
PREPARE_SHA_INDEX_NAME = "PREPARE_SHA256SUMS.txt"
PREFLIGHT_BODY_NAME = "PREFLIGHT_RECEIPT_BODY.json"
PREFLIGHT_SHA_INDEX_NAME = "PREFLIGHT_SHA256SUMS.txt"
PREFLIGHT_PENDING_COMMIT_NAME = ".PREFLIGHT_COMMITTED.pending"
PREFLIGHT_COMMITTED_NAME = "PREFLIGHT_COMMITTED.json"
PREFLIGHT_FATAL_FAIL_NAME = "PREFLIGHT_FATAL_FAIL.json"
FAILURE_SHA_INDEX_NAME = "FAILURE_SHA256SUMS.txt"
LEASE_PLACEHOLDER_UTC = "0000-00-00T00:00:00Z"
SUCCESS_ROOT_FILES = (
    PREPARED_NAME,
    EXECUTION_QA_REQUIRED_NAME,
    PREPARE_SHA_INDEX_NAME,
    PREFLIGHT_BODY_NAME,
    PREFLIGHT_SHA_INDEX_NAME,
    PREFLIGHT_COMMITTED_NAME,
)


class PreflightError(RuntimeError):
    """A result-blind package, authorization, runtime, or process gate failed."""


class ReceiptDirectoryPreparationError(PreflightError):
    """Receipt setup failed after this invocation created its no-clobber root."""

    def __init__(self, path: Path, cause: BaseException) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.path = path
        self.cause = cause


class VerifiedFile:
    """One open, hash-verified inode plus optional immutable byte snapshot.

    Paths owned by the invoking UID are not immutable merely because their mode
    is 0444.  Keeping the descriptor makes atomic replacement harmless; keeping
    the verified bytes makes subsequent parsing/compilation independent of any
    later same-inode write.  The path is checked against the original inode and
    digest again after all authorized consumption.
    """

    def __init__(
        self,
        *,
        path: Path,
        label: str,
        descriptor: int,
        metadata: os.stat_result,
        sha256: str,
        snapshot: bytes | None,
    ) -> None:
        self.path = path
        self.label = label
        self.descriptor = descriptor
        self.metadata = metadata
        self.sha256 = sha256
        self.snapshot = snapshot
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            os.close(self.descriptor)
            self.closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except (OSError, AttributeError):
            pass


class VerifiedDirectory:
    """One held directory inode whose pathname and exact child set stay bound."""

    def __init__(self, *, path: Path, label: str, descriptor: int) -> None:
        self.path = path
        self.label = label
        self.descriptor = descriptor
        self.metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(self.metadata.st_mode):
            os.close(descriptor)
            raise PreflightError(f"{label} is not a directory: {path}")
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            os.close(self.descriptor)
            self.closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except (OSError, AttributeError):
            pass


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_uid),
        int(metadata.st_gid),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        offset += len(block)


def _open_verified_file(
    path: Path,
    label: str,
    *,
    mode: int | None = None,
    expected_sha256: str | None = None,
    capture_snapshot: bool = False,
    require_nlink_one: bool = True,
) -> VerifiedFile:
    _reject_symlink_chain(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PreflightError(f"cannot open {label} without following symlinks: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PreflightError(f"{label} is not a regular file: {path}")
        if require_nlink_one and before.st_nlink != 1:
            raise PreflightError(f"{label} nlink must be 1, observed {before.st_nlink}")
        if mode is not None and stat.S_IMODE(before.st_mode) != mode:
            raise PreflightError(
                f"{label} mode must be {mode:04o}, observed {stat.S_IMODE(before.st_mode):04o}"
            )
        payload = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or len(payload) != before.st_size:
            raise PreflightError(f"{label} changed while its verified snapshot was captured")
        digest = _sha256_bytes(payload)
        if expected_sha256 is not None and digest != _normalized_sha(
            expected_sha256, f"expected {label} SHA"
        ):
            raise PreflightError(f"{label} SHA-256 mismatch")
        current = path.lstat()
        if _stat_identity(current) != _stat_identity(before):
            raise PreflightError(f"{label} path/inode identity changed during verification")
        return VerifiedFile(
            path=path,
            label=label,
            descriptor=descriptor,
            metadata=before,
            sha256=digest,
            snapshot=payload if capture_snapshot else None,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _open_verified_file_at(
    directory: VerifiedDirectory,
    name: str,
    label: str,
    *,
    expected_sha256: str,
    mode: int = FILE_MODE,
) -> VerifiedFile:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise PreflightError(f"unsafe {label} filename: {name!r}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory.descriptor)
    except OSError as exc:
        raise PreflightError(f"cannot open {label} from held attempt root: {exc}") from exc
    path = directory.path / name
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
        ):
            raise PreflightError(f"{label} must be regular mode {mode:04o} nlink=1")
        payload = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or len(payload) != before.st_size:
            raise PreflightError(f"{label} changed during held-descriptor snapshot")
        digest = _sha256_bytes(payload)
        if digest != _normalized_sha(expected_sha256, f"expected {label} SHA"):
            raise PreflightError(f"{label} SHA-256 mismatch")
        current = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if _stat_identity(current) != _stat_identity(before):
            raise PreflightError(f"{label} held-root pathname no longer names its inode")
        return VerifiedFile(
            path=path,
            label=label,
            descriptor=descriptor,
            metadata=before,
            sha256=digest,
            snapshot=payload,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _verified_bytes(file: VerifiedFile) -> bytes:
    if file.snapshot is None:
        raise PreflightError(f"{file.label} has no protected byte snapshot")
    if _sha256_bytes(file.snapshot) != file.sha256:
        raise PreflightError(f"{file.label} protected byte snapshot identity mismatch")
    return file.snapshot


def _verify_file_continuity(file: VerifiedFile) -> None:
    if file.closed:
        raise PreflightError(f"{file.label} descriptor closed before continuity audit")
    before = os.fstat(file.descriptor)
    payload = _read_descriptor(file.descriptor)
    after = os.fstat(file.descriptor)
    expected = _stat_identity(file.metadata)
    if (
        _stat_identity(before) != expected
        or _stat_identity(after) != expected
        or len(payload) != file.metadata.st_size
        or _sha256_bytes(payload) != file.sha256
    ):
        raise PreflightError(f"{file.label} held inode changed after verification")
    _reject_symlink_chain(file.path, file.label)
    try:
        current = file.path.lstat()
    except FileNotFoundError as exc:
        raise PreflightError(f"{file.label} path disappeared after verification") from exc
    if _stat_identity(current) != expected:
        raise PreflightError(f"{file.label} path no longer names the verified inode")


def _close_verified_files(files: Iterable[VerifiedFile]) -> None:
    seen: set[int] = set()
    for file in files:
        if file.descriptor not in seen:
            seen.add(file.descriptor)
            file.close()


def _attempt_directory_binding(directory: VerifiedDirectory) -> dict[str, Any]:
    metadata = os.fstat(directory.descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise PreflightError(f"{directory.label} held inode is no longer a directory")
    return {
        "path": str(directory.path),
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def _verify_directory_continuity(directory: VerifiedDirectory) -> None:
    if directory.closed:
        raise PreflightError(f"{directory.label} descriptor closed before continuity audit")
    current_fd = os.fstat(directory.descriptor)
    if _stat_identity(current_fd) != _stat_identity(directory.metadata):
        raise PreflightError(f"{directory.label} held inode changed after verification")
    _reject_symlink_chain(directory.path, directory.label)
    try:
        current_path = directory.path.lstat()
    except FileNotFoundError as exc:
        raise PreflightError(f"{directory.label} path disappeared after verification") from exc
    if _stat_identity(current_path) != _stat_identity(directory.metadata):
        raise PreflightError(f"{directory.label} path no longer names the held inode")


def _validate_attempt_invocation(
    invocation: Any,
    *,
    package_root: Path,
    attempt_root: Path,
    build_spec: Mapping[str, Any],
    builder_sha256: str,
) -> None:
    exact_keys = {
        "argv",
        "cwd",
        "output_dir",
        "failure_receipt_dir",
        "package_spec",
        "builder",
        "python",
        "runtime",
        "environment",
    }
    if type(invocation) is not dict or set(invocation) != exact_keys:
        raise PreflightError("package build-attempt invocation keyset is invalid")
    argv = invocation["argv"]
    if type(argv) is not list or not argv or any(type(value) is not str for value in argv):
        raise PreflightError("package build-attempt invocation argv is invalid")
    cwd = invocation["cwd"]
    if (
        type(cwd) is not dict
        or set(cwd) != {"lexical", "resolved", "device", "inode"}
        or type(cwd["lexical"]) is not str
        or type(cwd["resolved"]) is not str
        or type(cwd["device"]) is not int
        or type(cwd["inode"]) is not int
    ):
        raise PreflightError("package build-attempt invocation cwd is invalid")
    if (
        invocation["output_dir"] != str(package_root)
        or invocation["failure_receipt_dir"] != str(attempt_root)
    ):
        raise PreflightError("package build-attempt invocation output paths are invalid")
    _require_exact_json_equal(
        invocation["package_spec"],
        {
            "path": build_spec["path_at_build"],
            "expected_sha256": build_spec["sha256"],
        },
        "package build-attempt invocation build spec",
    )
    builder = invocation["builder"]
    if (
        type(builder) is not dict
        or set(builder) != {"path", "expected_sha256"}
        or type(builder["path"]) is not str
        or not Path(builder["path"]).is_absolute()
        or builder["expected_sha256"] != builder_sha256
    ):
        raise PreflightError("package build-attempt invocation builder is invalid")
    python = invocation["python"]
    if type(python) is not dict or set(python) != {
        "implementation",
        "version",
        "version_info",
        "executable_lexical",
        "executable_resolved",
        "executable_sha256",
        "flags",
    }:
        raise PreflightError("package build-attempt invocation Python keyset is invalid")
    if (
        any(
            type(python[key]) is not str
            for key in (
                "implementation",
                "version",
                "executable_lexical",
                "executable_resolved",
            )
        )
        or type(python["version_info"]) is not list
        or len(python["version_info"]) != 5
        or any(type(value) is not int for value in python["version_info"][:3])
        or type(python["version_info"][3]) is not str
        or type(python["version_info"][4]) is not int
        or type(python["flags"]) is not dict
        or not python["flags"]
        or any(type(key) is not str for key in python["flags"])
        or any(type(value) not in {int, bool} for value in python["flags"].values())
    ):
        raise PreflightError("package build-attempt invocation Python identity is invalid")
    _normalized_sha(python["executable_sha256"], "package builder Python executable")
    runtime = invocation["runtime"]
    runtime_keys = {
        "platform",
        "machine",
        "system",
        "release",
        "byteorder",
        "filesystem_encoding",
    }
    if (
        type(runtime) is not dict
        or set(runtime) != runtime_keys
        or any(type(runtime[key]) is not str for key in runtime_keys)
        or runtime["byteorder"] not in {"little", "big"}
    ):
        raise PreflightError("package build-attempt invocation runtime is invalid")
    environment = invocation["environment"]
    if type(environment) is not dict or set(environment) != {
        "raw_values_recorded",
        "key_count",
        "keys",
        "keyset_sha256",
        "key_value_map_sha256",
    }:
        raise PreflightError("package build-attempt invocation environment keyset is invalid")
    keys = environment["keys"]
    if (
        environment["raw_values_recorded"] is not False
        or type(environment["key_count"]) is not int
        or type(keys) is not list
        or any(type(key) is not str for key in keys)
        or keys != sorted(set(keys))
        or environment["key_count"] != len(keys)
        or environment["keyset_sha256"] != _canonical_json_sha(keys)
    ):
        raise PreflightError("package build-attempt invocation environment is invalid")
    _normalized_sha(
        environment["key_value_map_sha256"], "package builder environment map"
    )


def _directory_identity(metadata: os.stat_result) -> dict[str, Any]:
    return {
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
        "st_uid": int(metadata.st_uid),
        "st_gid": int(metadata.st_gid),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def _regular_file_identity(metadata: os.stat_result) -> dict[str, Any]:
    return {
        **_directory_identity(metadata),
        "nlink": int(metadata.st_nlink),
        "size_bytes": int(metadata.st_size),
    }


def _utc_z_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_all(descriptor: int, payload: bytes, *, offset: int = 0) -> None:
    written = 0
    while written < len(payload):
        count = os.pwrite(descriptor, payload[written:], offset + written)
        if count <= 0:
            raise PreflightError("short write in durable receipt transaction")
        written += count


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        offset += len(block)


def _open_dir_at(parent_fd: int, name: str) -> int:
    return os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )


def _write_file_at(
    directory_fd: int,
    name: str,
    payload: bytes,
    *,
    mode: int = FILE_MODE,
) -> dict[str, Any]:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise PreflightError(f"unsafe receipt filename: {name!r}")
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=directory_fd,
    )
    try:
        _write_all(descriptor, payload)
        os.ftruncate(descriptor, len(payload))
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PreflightError(f"receipt file is not regular nlink=1: {name}")
        return {
            "path": name,
            "sha256": _sha256_bytes(payload),
            "identity": _regular_file_identity(metadata),
        }
    finally:
        os.close(descriptor)


def _read_file_at(
    directory_fd: int,
    name: str,
    label: str,
    *,
    expected_mode: int | None = FILE_MODE,
) -> tuple[bytes, dict[str, Any]]:
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PreflightError(f"{label} must be regular nlink=1")
        if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
            raise PreflightError(
                f"{label} mode must be {expected_mode:04o}, observed "
                f"{stat.S_IMODE(before.st_mode):04o}"
            )
        raw = _read_all(descriptor)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise PreflightError(f"{label} changed during descriptor read")
        return raw, {
            "path": name,
            "sha256": _sha256_bytes(raw),
            "identity": _regular_file_identity(after),
        }
    finally:
        os.close(descriptor)


class _ReceiptTransaction:
    """Held receipt parent/root and external lease descriptors."""

    def __init__(self, path: Path, parent_fd: int, root_fd: int) -> None:
        self.path = path
        self.parent_path = path.parent
        self.name = path.name
        self.parent_fd = parent_fd
        self.root_fd = root_fd
        self.parent_identity = _directory_identity(os.fstat(parent_fd))
        self.root_identity = _directory_identity(os.fstat(root_fd))
        self.lease_name = f".{self.name}.controlled_real10k_20k_preflight_once_lease.json"
        self.lease_path = self.parent_path / self.lease_name
        self.lease_fd: int | None = None
        self.lease_initial_raw: bytes | None = None
        self.lease_initial_binding: dict[str, Any] | None = None
        self.lease_consumed_binding: dict[str, Any] | None = None
        self.prepared: dict[str, Any] | None = None
        self.prepared_bindings: dict[str, Any] | None = None
        self.execution_resources: list[VerifiedFile] = []
        self.closed = False

    def assert_directory_continuity(self) -> None:
        _require_exact_json_equal(
            _directory_identity(os.fstat(self.parent_fd)),
            self.parent_identity,
            "receipt parent held identity",
        )
        _require_exact_json_equal(
            _directory_identity(os.fstat(self.root_fd)),
            self.root_identity,
            "receipt root held identity",
        )
        lexical_parent = self.parent_path.lstat()
        lexical_root = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        _require_exact_json_equal(
            _directory_identity(lexical_parent),
            self.parent_identity,
            "receipt parent path identity",
        )
        _require_exact_json_equal(
            _directory_identity(lexical_root),
            self.root_identity,
            "receipt root path identity",
        )

    def assert_lease_continuity(self) -> None:
        if self.lease_fd is None:
            raise PreflightError("receipt transaction has no held external lease")
        held = os.fstat(self.lease_fd)
        lexical = os.stat(
            self.lease_name, dir_fd=self.parent_fd, follow_symlinks=False
        )
        if _stat_identity(held) != _stat_identity(lexical):
            raise PreflightError("external one-use lease path no longer names held inode")

    def close(self) -> None:
        if self.closed:
            return
        _close_verified_files(self.execution_resources)
        self.execution_resources.clear()
        if self.lease_fd is not None:
            os.close(self.lease_fd)
        os.close(self.root_fd)
        os.close(self.parent_fd)
        self.closed = True


class _ReceiptTransactionValidationError(PreflightError):
    def __init__(self, transaction: _ReceiptTransaction, cause: BaseException) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.transaction = transaction
        self.cause = cause


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_go_utc(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise PreflightError(f"{label} must be a JSON string")
    raw = value
    if not GO_UTC_PATTERN.fullmatch(raw):
        raise PreflightError(f"{label} must be strict UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PreflightError(f"{label} is not a valid UTC timestamp") from exc


def _parse_attempt_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", value
    ):
        raise PreflightError(f"{label} must be strict UTC YYYY-MM-DDTHH:MM:SS+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PreflightError(f"{label} is not a valid UTC timestamp") from exc
    if parsed.utcoffset() != timedelta(0) or parsed.microsecond != 0:
        raise PreflightError(f"{label} is not exact second-resolution UTC")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _normalized_sha(value: Any, label: str) -> str:
    if type(value) is not str or not _is_sha256(value):
        raise PreflightError(f"{label} is not a lowercase SHA-256")
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _canonical_json_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def _strict_json_loads(raw: str, label: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PreflightError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise PreflightError(f"{label} contains non-finite JSON constant {value}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except PreflightError:
        raise
    except json.JSONDecodeError as exc:
        raise PreflightError(f"cannot parse {label}: {exc}") from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeError) as exc:
        raise PreflightError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{label} must be a JSON object")
    return value


def _read_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(payload.decode("utf-8"), label)
    except UnicodeError as exc:
        raise PreflightError(f"cannot parse verified {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{label} must be a JSON object")
    return value


def _require_exact_json_equal(actual: Any, expected: Any, label: str) -> None:
    """Require recursive JSON equality without Python bool/int coercion."""

    def compare(left: Any, right: Any, location: str) -> None:
        if type(left) is not type(right):
            raise PreflightError(
                f"{label} exact JSON type mismatch at {location}: "
                f"{type(left).__name__} != {type(right).__name__}"
            )
        if isinstance(right, dict):
            if any(type(key) is not str for key in left) or any(
                type(key) is not str for key in right
            ):
                raise PreflightError(f"{label} has a non-string JSON key at {location}")
            if set(left) != set(right):
                raise PreflightError(f"{label} exact JSON keyset mismatch at {location}")
            for key in sorted(right):
                compare(left[key], right[key], f"{location}.{key}")
            return
        if isinstance(right, list):
            if len(left) != len(right):
                raise PreflightError(f"{label} exact JSON length mismatch at {location}")
            for index, (left_value, right_value) in enumerate(zip(left, right)):
                compare(left_value, right_value, f"{location}[{index}]")
            return
        if left != right:
            raise PreflightError(f"{label} exact JSON value mismatch at {location}")

    compare(actual, expected, "$")


def _absolute_path(raw: str, label: str, *, must_exist: bool) -> Path:
    if "\x00" in raw:
        raise PreflightError(f"{label} contains NUL")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise PreflightError(f"{label} must be absolute: {raw!r}")
    if ".." in path.parts:
        raise PreflightError(f"{label} contains path traversal: {raw!r}")
    if must_exist and not path.exists():
        raise PreflightError(f"{label} is missing: {path}")
    return path


def _reject_symlink_chain(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise PreflightError(f"{label} traverses a symlink: {current}")


def _regular_file(path: Path, label: str, *, mode: int | None = None) -> Path:
    _reject_symlink_chain(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise PreflightError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PreflightError(f"{label} is not a regular file: {path}")
    if metadata.st_nlink != 1:
        raise PreflightError(f"{label} nlink must be 1, observed {metadata.st_nlink}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise PreflightError(
            f"{label} mode must be {mode:04o}, observed {stat.S_IMODE(metadata.st_mode):04o}"
        )
    return path


def _safe_relative(raw: str, label: str) -> PurePosixPath:
    if not raw or "\x00" in raw or "\\" in raw:
        raise PreflightError(f"{label} is unsafe: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PreflightError(f"{label} contains path traversal: {raw!r}")
    return path


def _parse_sha_index_bytes(payload: bytes) -> dict[str, str]:
    records: dict[str, str] = {}
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise PreflightError(f"cannot decode verified SHA index: {exc}") from exc
    for number, raw_line in enumerate(lines, start=1):
        if not raw_line or "  " not in raw_line:
            raise PreflightError(f"SHA index line {number} is malformed")
        raw_sha, raw_relative = raw_line.split("  ", 1)
        digest = _normalized_sha(raw_sha, f"SHA index line {number}")
        relative = _safe_relative(raw_relative, f"SHA index line {number}").as_posix()
        if relative in records:
            raise PreflightError(f"SHA index has duplicate path: {relative}")
        records[relative] = digest
    if not records:
        raise PreflightError("SHA index is empty")
    return records


def _verified_file_binding(file: VerifiedFile) -> dict[str, Any]:
    return {
        "path": str(file.path),
        "sha256": file.sha256,
        "identity": _regular_file_identity(file.metadata),
    }


def _audit_package(
    package_dir: Path,
    *,
    expected_manifest_sha256: str,
    expected_index_sha256: str,
    expected_commit_sha256: str,
    build_attempt_body: Path,
    expected_build_attempt_body_sha256: str,
    build_attempt_committed: Path,
    expected_build_attempt_committed_sha256: str,
) -> dict[str, Any]:
    root = _absolute_path(str(package_dir), "--package-dir", must_exist=True)
    _reject_symlink_chain(root, "package root")
    if root.resolve(strict=True) != root or not root.is_dir():
        raise PreflightError("package-v4 root must be a canonical directory")
    root_metadata = root.lstat()
    if stat.S_IMODE(root_metadata.st_mode) != DIRECTORY_MODE:
        raise PreflightError("package-v4 root mode must be 0555")
    held: dict[str, VerifiedFile] = {}
    held_directories: list[VerifiedDirectory] = []

    def open_member(
        relative: str,
        label: str,
        *,
        expected_sha: str | None = None,
        snapshot: bool = False,
    ) -> VerifiedFile:
        if relative in held:
            existing = held[relative]
            if expected_sha is not None and existing.sha256 != _normalized_sha(
                expected_sha, f"{label} SHA-256"
            ):
                raise PreflightError(f"package-v4 repeated member SHA mismatch: {relative}")
            return existing
        safe = _safe_relative(relative, label)
        file = _open_verified_file(
            root.joinpath(*safe.parts),
            label,
            mode=FILE_MODE,
            expected_sha256=expected_sha,
            capture_snapshot=snapshot,
        )
        held[relative] = file
        return file

    manifest_file = open_member(
        MANIFEST_NAME,
        "package-v4 manifest",
        expected_sha=expected_manifest_sha256,
        snapshot=True,
    )
    index_file = open_member(
        SHA_INDEX_NAME,
        "package-v4 SHA index",
        expected_sha=expected_index_sha256,
        snapshot=True,
    )
    commit_file = open_member(
        PACKAGE_COMMIT_NAME,
        "package-v4 terminal commit",
        expected_sha=expected_commit_sha256,
        snapshot=True,
    )
    manifest = _read_json_bytes(_verified_bytes(manifest_file), "package-v4 manifest")
    exact_manifest_keys = {
        "schema",
        "package_version",
        "build_spec",
        "required_roles",
        "role_destinations",
        "role_identity",
        "artifacts",
        "runtime",
        "authorities",
        "execution_authorized",
        "result_accessed",
        "numerical_metrics_accessed",
    }
    if set(manifest) != exact_manifest_keys:
        raise PreflightError("package-v4 manifest keyset is not exact")
    if (
        manifest["schema"] != PACKAGE_SCHEMA
        or manifest["package_version"] != PACKAGE_VERSION
        or manifest["required_roles"] != sorted(PACKAGE_REQUIRED_ROLES)
        or manifest["role_destinations"] != PACKAGE_ROLE_DESTINATIONS
        or manifest["execution_authorized"] is not False
        or manifest["result_accessed"] is not False
        or manifest["numerical_metrics_accessed"] is not False
    ):
        raise PreflightError("package-v4 manifest status or role contract is invalid")
    _require_exact_json_equal(
        manifest["authorities"], PACKAGE_QA_AUTHORITIES, "package-v4 authorities"
    )
    build_spec = manifest["build_spec"]
    if not isinstance(build_spec, dict) or set(build_spec) != {
        "schema",
        "path_at_build",
        "sha256",
    }:
        raise PreflightError("package-v4 build-spec binding is invalid")
    _normalized_sha(build_spec["sha256"], "package-v4 build-spec SHA")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(PACKAGE_REQUIRED_ROLES):
        raise PreflightError("package-v4 artifact list cardinality is invalid")
    roles: dict[str, dict[str, Any]] = {}
    physical_records: dict[str, dict[str, Any]] = {}
    tree_member_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise PreflightError("package-v4 artifact must be an object")
        role = artifact.get("role")
        if type(role) is not str or role not in PACKAGE_REQUIRED_ROLES or role in roles:
            raise PreflightError(f"package-v4 artifact role is invalid: {role!r}")
        if artifact.get("path") != PACKAGE_ROLE_DESTINATIONS[role]:
            raise PreflightError(f"package-v4 role destination mismatch: {role}")
        if role == "runtime_dependency_closure_tree":
            exact_tree_keys = {
                "role",
                "kind",
                "path",
                "sha256",
                "inventory_sha256",
                "file_count",
                "directory_count",
                "size_bytes",
                "mode_octal",
                "source_path_at_build",
                "members",
            }
            if set(artifact) != exact_tree_keys or artifact["kind"] != "tree":
                raise PreflightError("package-v4 runtime tree artifact schema is invalid")
            members = artifact["members"]
            if not isinstance(members, list) or not members:
                raise PreflightError("package-v4 runtime tree has no members")
            normalized_members: list[dict[str, Any]] = []
            directories: set[str] = set()
            total_size = 0
            prefix = PurePosixPath(PACKAGE_ROLE_DESTINATIONS[role])
            for member in members:
                if not isinstance(member, dict) or set(member) != {
                    "path",
                    "sha256",
                    "size_bytes",
                    "mode_octal",
                    "nlink",
                }:
                    raise PreflightError("package-v4 runtime tree member schema is invalid")
                relative = _safe_relative(member["path"], "runtime dependency member")
                if relative == prefix or prefix not in relative.parents:
                    raise PreflightError("runtime dependency member escapes frozen tree prefix")
                relative_text = relative.as_posix()
                if relative_text in tree_member_paths:
                    raise PreflightError("runtime dependency member path is duplicated")
                digest = _normalized_sha(member["sha256"], "runtime dependency member")
                verified = open_member(
                    relative_text,
                    f"runtime dependency member {relative_text}",
                    expected_sha=digest,
                )
                if (
                    type(member["size_bytes"]) is not int
                    or member["size_bytes"] != verified.metadata.st_size
                    or member["mode_octal"] != "0444"
                    or type(member["nlink"]) is not int
                    or member["nlink"] != 1
                ):
                    raise PreflightError("runtime dependency member identity is invalid")
                unprefixed = relative.relative_to(prefix).as_posix()
                normalized_members.append({**member, "path": unprefixed})
                parent = PurePosixPath(unprefixed).parent
                while parent.parts:
                    directories.add(parent.as_posix())
                    parent = parent.parent
                total_size += member["size_bytes"]
                tree_member_paths.add(relative_text)
                physical_records[relative_text] = {
                    "path": relative_text,
                    "sha256": digest,
                }
            if (
                type(artifact["file_count"]) is not int
                or artifact["file_count"] != len(members)
                or type(artifact["directory_count"]) is not int
                or artifact["directory_count"] != len(directories)
                or type(artifact["size_bytes"]) is not int
                or artifact["size_bytes"] != total_size
                or artifact["mode_octal"] != "0555"
                or artifact["sha256"] != _canonical_json_sha(normalized_members)
            ):
                raise PreflightError("package-v4 runtime tree aggregate identity is invalid")
            roles[role] = dict(artifact)
            continue

        exact_file_keys = {
            "role",
            "kind",
            "path",
            "sha256",
            "size_bytes",
            "mode_octal",
            "nlink",
            "source_path_at_build",
        }
        if set(artifact) != exact_file_keys or artifact["kind"] != "file":
            raise PreflightError(f"package-v4 file artifact schema is invalid: {role}")
        digest = _normalized_sha(artifact["sha256"], f"package-v4 role {role}")
        verified = open_member(
            artifact["path"],
            f"package-v4 role {role}",
            expected_sha=digest,
            snapshot=(
                role in REQUIRED_CODE_ROLES
                or role in REQUIRED_NATIVE_TEST_ROLES
                or role == "process_singleton_contract_json"
            ),
        )
        if (
            type(artifact["size_bytes"]) is not int
            or artifact["size_bytes"] != verified.metadata.st_size
            or artifact["mode_octal"] != "0444"
            or type(artifact["nlink"]) is not int
            or artifact["nlink"] != 1
        ):
            raise PreflightError(f"package-v4 role identity is invalid: {role}")
        roles[role] = {**artifact, "absolute_path": str(verified.path), "verified_file": verified}
        physical_records[artifact["path"]] = {
            "path": artifact["path"],
            "sha256": digest,
        }
    if set(roles) != PACKAGE_REQUIRED_ROLES or [item["role"] for item in artifacts] != sorted(
        PACKAGE_REQUIRED_ROLES
    ):
        raise PreflightError("package-v4 artifact role set/order is not exact")
    role_identity = manifest["role_identity"]
    expected_role_identity = {
        role: {
            "kind": roles[role]["kind"],
            "path": roles[role]["path"],
            "sha256": roles[role]["sha256"],
        }
        for role in sorted(roles)
    }
    _require_exact_json_equal(
        role_identity, expected_role_identity, "package-v4 role identity"
    )
    if roles["runtime_dependency_closure_tree"]["inventory_sha256"] != role_identity[
        "runtime_dependency_closure_json"
    ]["sha256"]:
        raise PreflightError("package-v4 runtime inventory/tree binding is invalid")

    runtime = manifest["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {
        "entrypoints",
        "import_graph",
        "dependency_closure",
        "process_singleton_contract",
    }:
        raise PreflightError("package-v4 runtime declaration keyset is invalid")
    expected_entrypoints = {
        "preflight": PACKAGE_ROLE_DESTINATIONS["preflight_code"],
        "materialization": PACKAGE_ROLE_DESTINATIONS["materialization_gate_code"],
        "runner": PACKAGE_ROLE_DESTINATIONS["runner_code"],
        "trainer": PACKAGE_ROLE_DESTINATIONS["trainer_code"],
        "evaluator": PACKAGE_ROLE_DESTINATIONS["evaluator_code"],
        "native_smoke": PACKAGE_ROLE_DESTINATIONS["native_smoke_test"],
    }
    _require_exact_json_equal(
        runtime["entrypoints"], expected_entrypoints, "package-v4 runtime entrypoints"
    )
    singleton = runtime["process_singleton_contract"]
    expected_singleton = {
        "schema": "controlled_real10k_20k_process_singleton_contract_v1",
        "path": PACKAGE_ROLE_DESTINATIONS["process_singleton_contract_json"],
        "sha256": role_identity["process_singleton_contract_json"]["sha256"],
        "lock_path": PACKAGE_SINGLETON_LOCK_NAME,
        "lock_sha256": hashlib.sha256(b"").hexdigest(),
        "protected_entrypoints": _expected_process_singleton_contract()[
            "protected_entrypoints"
        ],
    }
    _require_exact_json_equal(
        singleton, expected_singleton, "package-v4 process singleton declaration"
    )
    singleton_contract_file = roles["process_singleton_contract_json"]["verified_file"]
    singleton_contract = _read_json_bytes(
        _verified_bytes(singleton_contract_file), "package process singleton contract"
    )
    _require_exact_json_equal(
        singleton_contract,
        _expected_process_singleton_contract(),
        "package process singleton contract",
    )
    lock_file = open_member(
        PACKAGE_SINGLETON_LOCK_NAME,
        "package process singleton lock",
        expected_sha=singleton["lock_sha256"],
        snapshot=True,
    )
    try:
        fcntl.flock(lock_file.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PreflightError("package process singleton lock is already held") from exc
    physical_records[PACKAGE_SINGLETON_LOCK_NAME] = {
        "path": PACKAGE_SINGLETON_LOCK_NAME,
        "sha256": lock_file.sha256,
    }

    receipt_file = open_member(RECEIPT_NAME, "package-v4 receipt", snapshot=True)
    qa_file = open_member(QA_REQUIRED_NAME, "package-v4 QA requirement", snapshot=True)
    physical_records[MANIFEST_NAME] = {"path": MANIFEST_NAME, "sha256": manifest_file.sha256}
    physical_records[RECEIPT_NAME] = {"path": RECEIPT_NAME, "sha256": receipt_file.sha256}
    physical_records[QA_REQUIRED_NAME] = {"path": QA_REQUIRED_NAME, "sha256": qa_file.sha256}
    index = _parse_sha_index_bytes(_verified_bytes(index_file))
    if set(index) != set(physical_records):
        raise PreflightError(
            "package-v4 SHA closure path set is invalid: "
            f"missing={sorted(set(physical_records)-set(index))} "
            f"extra={sorted(set(index)-set(physical_records))}"
        )
    for path, record in physical_records.items():
        if index[path] != record["sha256"]:
            raise PreflightError(f"package-v4 SHA index mismatch: {path}")
    exact_index_bytes = "".join(
        f"{physical_records[path]['sha256']}  {path}\n" for path in sorted(physical_records)
    ).encode("ascii")
    if _verified_bytes(index_file) != exact_index_bytes:
        raise PreflightError("package-v4 SHA index order/bytes are not exact")

    receipt = _read_json_bytes(_verified_bytes(receipt_file), "package-v4 receipt")
    expected_receipt = {
        "schema": PACKAGE_RECEIPT_SCHEMA,
        "status": "PASS_PREPARED_AWAITING_INDEPENDENT_QA",
        "package_version": PACKAGE_VERSION,
        "manifest": {"path": MANIFEST_NAME, "sha256": manifest_file.sha256},
        "independent_qa_required": {"path": QA_REQUIRED_NAME, "sha256": qa_file.sha256},
        "role_identity": role_identity,
        "authorities": PACKAGE_QA_AUTHORITIES,
        "execution_authorized": False,
        "result_accessed": False,
        "numerical_metrics_accessed": False,
    }
    _require_exact_json_equal(receipt, expected_receipt, "package-v4 receipt")
    qa = _read_json_bytes(_verified_bytes(qa_file), "package-v4 QA requirement")
    expected_qa = {
        "schema": QA_REQUIRED_SCHEMA,
        "verdict": "NO_GO_PENDING_EXTERNAL_CODE_QA",
        "package_manifest": {"path": MANIFEST_NAME, "sha256": manifest_file.sha256},
        "required_go_receipt": {
            "issuer": "independent_qa",
            "verdict": "GO",
            "exact_binding_keyset_required": True,
            "required_binding_keys": list(PACKAGE_REQUIRED_GO_BINDING_KEYS),
            "maximum_age_seconds": 21600,
            "future_clock_skew_seconds": 0,
            "one_use": True,
        },
        "required_native_test_roles": list(REQUIRED_NATIVE_TEST_ROLES),
        "required_role_identity": role_identity,
        "authorities": PACKAGE_QA_AUTHORITIES,
        "execution_authorized": False,
    }
    _require_exact_json_equal(qa, expected_qa, "package-v4 QA requirement")

    commit = _read_json_bytes(_verified_bytes(commit_file), "package-v4 terminal commit")
    if not isinstance(commit, dict) or set(commit) != {
        "schema",
        "status",
        "package_version",
        "manifest",
        "receipt",
        "independent_qa_required",
        "sha256sums",
        "required_external_pass_attempt",
        "creation_order_contract",
        "authorities",
        "execution_authorized",
    }:
        raise PreflightError("package-v5 terminal commit keyset is invalid")
    body_path = _absolute_path(
        str(build_attempt_body),
        "--package-build-attempt-body",
        must_exist=True,
    )
    committed_path = _absolute_path(
        str(build_attempt_committed),
        "--package-build-attempt-committed",
        must_exist=True,
    )
    if (
        body_path.name != PACKAGE_BUILD_ATTEMPT_BODY_NAME
        or committed_path.name != PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
        or body_path.parent != committed_path.parent
        or body_path.resolve(strict=True) != body_path
        or committed_path.resolve(strict=True) != committed_path
    ):
        raise PreflightError("package build-attempt body/committed paths are not exact")
    expected_commit = {
        "schema": PACKAGE_COMMIT_SCHEMA,
        "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        "package_version": PACKAGE_VERSION,
        "manifest": {"path": MANIFEST_NAME, "sha256": manifest_file.sha256},
        "receipt": {"path": RECEIPT_NAME, "sha256": receipt_file.sha256},
        "independent_qa_required": {"path": QA_REQUIRED_NAME, "sha256": qa_file.sha256},
        "sha256sums": {"path": SHA_INDEX_NAME, "sha256": index_file.sha256},
        "required_external_pass_attempt": {
            "body": {
                "path": str(body_path),
                "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
                "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            },
            "committed": {
                "path": str(committed_path),
                "schema": PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
                "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
            },
        },
        "creation_order_contract": {
            "this_member_created_last": True,
            "post_commit_package_file_creation_permitted": False,
        },
        "authorities": PACKAGE_QA_AUTHORITIES,
        "execution_authorized": False,
    }
    _require_exact_json_equal(commit, expected_commit, "package-v5 terminal commit")

    attempt_root_path = body_path.parent
    attempt_parent_path = attempt_root_path.parent
    _reject_symlink_chain(attempt_parent_path, "package build-attempt parent")
    _reject_symlink_chain(attempt_root_path, "package build-attempt root")
    if (
        attempt_parent_path.resolve(strict=True) != attempt_parent_path
        or attempt_root_path.resolve(strict=True) != attempt_root_path
    ):
        raise PreflightError("package build-attempt root/parent must be canonical")
    parent_descriptor = os.open(
        attempt_parent_path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    attempt_parent = VerifiedDirectory(
        path=attempt_parent_path,
        label="package build-attempt parent",
        descriptor=parent_descriptor,
    )
    held_directories.append(attempt_parent)
    attempt_root = VerifiedDirectory(
        path=attempt_root_path,
        label="package build-attempt root",
        descriptor=_open_dir_at(attempt_parent.descriptor, attempt_root_path.name),
    )
    held_directories.append(attempt_root)
    if stat.S_IMODE(attempt_root.metadata.st_mode) != DIRECTORY_MODE:
        raise PreflightError("package build-attempt root mode must be 0555")
    attempt_names = set(os.listdir(attempt_root.descriptor))
    exact_attempt_names = {
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
    }
    if attempt_names != exact_attempt_names:
        raise PreflightError(
            "package build-attempt success closure is not exact: "
            f"missing={sorted(exact_attempt_names-attempt_names)} "
            f"extra={sorted(attempt_names-exact_attempt_names)}"
        )
    body_file = _open_verified_file_at(
        attempt_root,
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        "package build-attempt PASS body",
        expected_sha256=expected_build_attempt_body_sha256,
    )
    committed_file = _open_verified_file_at(
        attempt_root,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
        "package build-attempt committed marker",
        expected_sha256=expected_build_attempt_committed_sha256,
    )
    held["@external_build_attempt_body"] = body_file
    held["@external_build_attempt_committed"] = committed_file

    external = _read_json_bytes(
        _verified_bytes(body_file), "package-v5 external build-attempt body"
    )
    if not isinstance(external, dict) or set(external) != {
        "schema",
        "status",
        "started_utc",
        "completed_utc",
        "invocation",
        "observed_identity",
        "package",
        "partial_output_preserved",
        "authorities",
        "execution_authorized",
    }:
        raise PreflightError("package-v5 external PASS body keyset is invalid")
    if (
        external["schema"] != PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA
        or external["status"] != "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
        or external["partial_output_preserved"] is not False
        or external["execution_authorized"] is not False
    ):
        raise PreflightError("package-v5 external PASS body status is invalid")
    started = _parse_attempt_utc(
        external["started_utc"], "package build-attempt started_utc"
    )
    completed = _parse_attempt_utc(
        external["completed_utc"], "package build-attempt completed_utc"
    )
    if completed < started:
        raise PreflightError("package build-attempt completed before it started")
    _validate_attempt_invocation(
        external["invocation"],
        package_root=root,
        attempt_root=attempt_root_path,
        build_spec=build_spec,
        builder_sha256=role_identity["package_builder_code"]["sha256"],
    )
    _require_exact_json_equal(
        external["authorities"], PACKAGE_QA_AUTHORITIES, "external package authorities"
    )
    observed_identity = external["observed_identity"]
    if not isinstance(observed_identity, dict) or set(observed_identity) != {
        "package_spec_sha256",
        "builder_sha256",
        "package_output_device",
        "package_output_inode",
    }:
        raise PreflightError("external package observed identity keyset is invalid")
    _require_exact_json_equal(
        observed_identity,
        {
            "package_spec_sha256": build_spec["sha256"],
            "builder_sha256": role_identity["package_builder_code"]["sha256"],
            "package_output_device": int(root_metadata.st_dev),
            "package_output_inode": int(root_metadata.st_ino),
        },
        "external package observed identity",
    )
    exact_files = set(physical_records) | {SHA_INDEX_NAME, PACKAGE_COMMIT_NAME}
    external_package = {
        "path": str(root),
        "manifest_sha256": manifest_file.sha256,
        "receipt_sha256": receipt_file.sha256,
        "independent_qa_required_sha256": qa_file.sha256,
        "sha256sums_sha256": index_file.sha256,
        "package_commit_sha256": commit_file.sha256,
        "file_count": len(exact_files),
    }
    _require_exact_json_equal(
        external["package"], external_package, "external package closure binding"
    )
    terminal = _read_json_bytes(
        _verified_bytes(committed_file), "package-v5 build-attempt committed marker"
    )
    terminal_keys = {
        "schema",
        "status",
        "committed_utc",
        "body",
        "package_commit",
        "package_root",
        "attempt_root",
        "attempt_parent",
        "publication",
        "authorities",
        "execution_authorized",
    }
    if not isinstance(terminal, dict) or set(terminal) != terminal_keys:
        raise PreflightError("package build-attempt committed keyset is invalid")
    if (
        terminal["schema"] != PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA
        or terminal["status"] != "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED"
        or terminal["execution_authorized"] is not False
    ):
        raise PreflightError("package build-attempt committed status is invalid")
    _parse_attempt_utc(
        terminal["committed_utc"], "package build-attempt committed_utc"
    )
    _require_exact_json_equal(
        terminal["body"],
        {
            "path": str(body_path),
            "sha256": body_file.sha256,
            "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        },
        "package build-attempt body terminal binding",
    )
    _require_exact_json_equal(
        terminal["package_commit"],
        {
            "path": str(commit_file.path),
            "sha256": commit_file.sha256,
            "schema": PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        },
        "package build-attempt package commit binding",
    )
    _require_exact_json_equal(
        terminal["package_root"],
        {
            "path": str(root),
            "st_dev": int(root_metadata.st_dev),
            "st_ino": int(root_metadata.st_ino),
            "mode_octal": f"{stat.S_IMODE(root_metadata.st_mode):04o}",
        },
        "package build-attempt package root identity",
    )
    _require_exact_json_equal(
        terminal["attempt_root"],
        _attempt_directory_binding(attempt_root),
        "package build-attempt root identity",
    )
    _require_exact_json_equal(
        terminal["attempt_parent"],
        _attempt_directory_binding(attempt_parent),
        "package build-attempt parent identity",
    )
    _require_exact_json_equal(
        terminal["publication"],
        {
            "body_file_fsync": True,
            "attempt_root_fsync": True,
            "attempt_parent_fsync": True,
            "attempt_root_frozen": True,
            "continuity_verified": True,
            "terminal_inode_reserved_create_once_before_freeze": True,
            "terminal_bytes_published_after_durability": True,
            "post_commit_attempt_file_creation_permitted": False,
        },
        "package build-attempt durability publication",
    )
    _require_exact_json_equal(
        terminal["authorities"],
        PACKAGE_QA_AUTHORITIES,
        "package build-attempt committed authorities",
    )
    _verify_directory_continuity(attempt_root)
    _verify_directory_continuity(attempt_parent)
    if set(os.listdir(attempt_root.descriptor)) != exact_attempt_names:
        raise PreflightError("package build-attempt closure changed during audit")
    for role, expected_sha in REQUIRED_SOURCE_ROLES.items():
        if role_identity[role]["sha256"] != expected_sha:
            raise PreflightError(f"frozen scientific source identity mismatch: {role}")
    for role, expected_sha in REQUIRED_PREREGISTRATION_ROLES.items():
        if role_identity[role]["sha256"] != expected_sha:
            raise PreflightError(f"frozen preregistration identity mismatch: {role}")
    if role_identity["trainer_code"]["sha256"] != FROZEN_TRAINER_SHA256:
        raise PreflightError("frozen trainer source identity mismatch")
    observed_files: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise PreflightError(f"package-v4 contains symlink: {relative}")
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE:
                raise PreflightError(f"package-v4 directory mode mismatch: {relative}")
        elif stat.S_ISREG(metadata.st_mode):
            observed_files.add(relative)
            if stat.S_IMODE(metadata.st_mode) != FILE_MODE or metadata.st_nlink != 1:
                raise PreflightError(f"package-v4 file mode/nlink mismatch: {relative}")
        else:
            raise PreflightError(f"package-v4 contains non-regular member: {relative}")
    if observed_files != exact_files:
        raise PreflightError(
            "package-v4 filesystem closure is not exact: "
            f"missing={sorted(exact_files-observed_files)} extra={sorted(observed_files-exact_files)}"
        )
    current_preflight = Path(__file__).resolve()
    packaged_preflight = roles["preflight_code"]["verified_file"]
    execution_file = (
        packaged_preflight
        if current_preflight == packaged_preflight.path
        else _open_verified_file(
            current_preflight,
            "executing preflight implementation",
            expected_sha256=packaged_preflight.sha256,
            capture_snapshot=True,
        )
    )
    return {
        "root": root,
        "root_stat_identity": _stat_identity(root_metadata),
        "manifest_path": manifest_file.path,
        "manifest_sha256": manifest_file.sha256,
        "index_path": index_file.path,
        "index_sha256": index_file.sha256,
        "receipt_path": receipt_file.path,
        "receipt_sha256": receipt_file.sha256,
        "qa_path": qa_file.path,
        "qa_sha256": qa_file.sha256,
        "commit_path": commit_file.path,
        "commit_sha256": commit_file.sha256,
        "build_attempt_body_path": body_file.path,
        "build_attempt_body_sha256": body_file.sha256,
        "build_attempt_committed_path": committed_file.path,
        "build_attempt_committed_sha256": committed_file.sha256,
        "held_by_relative": held,
        "held_directories": held_directories,
        "execution_file": execution_file,
        "roles": roles,
        "role_identity": role_identity,
        "role_sha256": {
            role: record["sha256"] for role, record in sorted(role_identity.items())
        },
        "code_role_sha256": {
            role: role_identity[role]["sha256"] for role in sorted(REQUIRED_CODE_ROLES)
        },
        "source_role_sha256": {
            role: role_identity[role]["sha256"] for role in sorted(REQUIRED_SOURCE_ROLES)
        },
        "preregistration_role_sha256": {
            role: role_identity[role]["sha256"]
            for role in sorted(REQUIRED_PREREGISTRATION_ROLES)
        },
        "runtime": runtime,
        "runtime_dependency_closure": runtime["dependency_closure"],
        "runtime_entrypoints": runtime["entrypoints"],
        "singleton_contract": singleton_contract,
        "singleton_contract_file": singleton_contract_file,
        "singleton_lock_file": lock_file,
        "exact_files": exact_files,
    }


def _verify_package_continuity(package: Mapping[str, Any]) -> None:
    root = package["root"]
    _reject_symlink_chain(root, "package root")
    try:
        root_metadata = root.lstat()
    except FileNotFoundError as exc:
        raise PreflightError("package root disappeared after verification") from exc
    if _stat_identity(root_metadata) != package["root_stat_identity"]:
        raise PreflightError("package root inode/stat identity changed during preflight")
    for verified in package["held_by_relative"].values():
        _verify_file_continuity(verified)
    for directory in package["held_directories"]:
        _verify_directory_continuity(directory)
    attempt_root = package["held_directories"][-1]
    if set(os.listdir(attempt_root.descriptor)) != {
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
    }:
        raise PreflightError("package build-attempt closure changed after verification")
    if package["execution_file"] not in package["held_by_relative"].values():
        _verify_file_continuity(package["execution_file"])
    observed_files: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise PreflightError(f"package contains symlink after consumption: {relative}")
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE:
                raise PreflightError(f"package directory mode changed: {relative}")
        elif stat.S_ISREG(metadata.st_mode):
            observed_files.add(relative)
            if stat.S_IMODE(metadata.st_mode) != FILE_MODE or metadata.st_nlink != 1:
                raise PreflightError(f"package file mode/nlink changed: {relative}")
        else:
            raise PreflightError(f"package gained non-regular entry: {relative}")
    if observed_files != package["exact_files"]:
        raise PreflightError("package filesystem closure changed during preflight")


def _boot_id() -> str:
    path = Path("/proc/sys/kernel/random/boot_id")
    try:
        value = path.read_text(encoding="ascii").strip().lower()
    except OSError as exc:
        raise PreflightError(f"cannot read Linux boot-id: {exc}") from exc
    if not BOOT_ID_PATTERN.fullmatch(value):
        raise PreflightError("Linux boot-id is malformed")
    return value


def _candidate_output_dirs(raw_paths: Sequence[str], package_root: Path, receipt_dir: Path) -> list[Path]:
    expected = [package_root.parent / name for name in REQUIRED_CANDIDATE_OUTPUT_NAMES]
    if len(raw_paths) != len(expected):
        raise PreflightError(
            "candidate output directory tuple must be exact and ordered: "
            f"expected={[str(path) for path in expected]} observed={list(raw_paths)}"
        )
    values: list[Path] = []
    for raw, exact_path in zip(raw_paths, expected):
        if raw != str(exact_path):
            raise PreflightError(
                "candidate output directory tuple must be exact and ordered: "
                f"expected={[str(path) for path in expected]} observed={list(raw_paths)}"
            )
        path = _absolute_path(raw, "--candidate-output-dir", must_exist=False)
        _reject_symlink_chain(path.parent, "candidate output parent")
        normalized = path.resolve(strict=False)
        if normalized in values:
            raise PreflightError(f"duplicate candidate output directory: {normalized}")
        if normalized == package_root or package_root in normalized.parents:
            raise PreflightError("candidate output directory overlaps the immutable package")
        if normalized == receipt_dir or receipt_dir in normalized.parents:
            raise PreflightError("candidate output directory overlaps the preflight receipt")
        values.append(normalized)
    if values != expected:
        raise PreflightError(
            "candidate output directory tuple must resolve to the frozen handoff paths"
        )
    return values


def _assert_candidate_dirs_absent(paths: Sequence[Path]) -> None:
    existing = [str(path) for path in paths if path.exists() or path.is_symlink()]
    if existing:
        raise PreflightError(f"candidate output directories already exist: {existing}")


def _proc_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scan_current_uid_processes(
    package: Mapping[str, Any], expected_python_path: Path
) -> dict[str, Any]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise PreflightError("Linux /proc is unavailable for current-UID singleton audit")
    uid = os.geteuid()
    runtime = package["runtime_dependency_closure"]
    expected_executable_sha = _normalized_sha(
        runtime["python"]["executable_sha256"], "process audit Python SHA-256"
    )
    expected_manifest_sha = package["role_identity"][
        "runtime_dependency_closure_json"
    ]["sha256"]
    expected_bootstrap_sha = package["role_identity"]["runtime_bootstrap_code"][
        "sha256"
    ]
    expected_pure_sha = runtime["pure_archive"]["sha256"]
    packaged_preflight = package["roles"]["preflight_code"]["verified_file"]
    sealed_role = {
        "materialization": "materialization_gate_code",
        "runner": "runner_code",
        "trainer": "trainer_code",
        "evaluator": "evaluator_code",
        "native_smoke": "native_smoke_test",
    }
    request_keys = {
        "schema",
        "entrypoint",
        "entrypoint_argv",
        "expected_bootstrap_sha256",
        "expected_manifest_sha256",
        "expected_pure_archive_sha256",
        "bootstrap_fd",
        "manifest_fd",
        "pure_archive_fd",
        "attestation_fd",
        "native_library_fds",
        "native_extension_fds",
    }
    matches: list[dict[str, Any]] = []
    for child in proc_root.iterdir():
        if not child.name.isdigit() or int(child.name) == os.getpid():
            continue
        try:
            status = (child / "status").read_text(encoding="utf-8", errors="strict")
            uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
            uid_fields = uid_line.split()
            if int(uid_fields[2]) != uid:
                continue
            raw = (child / "cmdline").read_bytes()
            argv_bytes = raw[:-1].split(b"\0") if raw.endswith(b"\0") else []
            argv = [part.decode("utf-8", errors="surrogateescape") for part in argv_bytes]
        except (
            FileNotFoundError,
            PermissionError,
            ProcessLookupError,
            StopIteration,
            ValueError,
            IndexError,
            UnicodeError,
            OSError,
        ):
            continue
        sealed_candidate = (
            len(argv) == 9
            and argv[0] == str(expected_python_path)
            and argv[1:7]
            == ["-I", "-B", "-S", "/proc/self/fd/200", "--request-fd", "201"]
            and argv[7] == "--entrypoint"
            and argv[8] in sealed_role
        )
        raw_preflight_candidate = (
            bool(argv)
            and argv[0] == str(expected_python_path)
            and str(packaged_preflight.path) in argv
        )
        if not sealed_candidate and not raw_preflight_candidate:
            continue
        observed: dict[str, Any] = {}
        request: dict[str, Any] | None = None
        role = "identity_invalid_controlled_candidate"
        identity_valid = False
        try:
            executable_sha = _proc_file_sha256(child / "exe")
            observed["executable_sha256"] = executable_sha
            if sealed_candidate:
                request_payload = (child / "fd" / "201").read_bytes()
                request = _strict_json_loads(
                    request_payload.decode("utf-8"),
                    f"process {child.name} sealed request",
                )
                observed.update(
                    {
                        "bootstrap_fd_200_sha256": _proc_file_sha256(
                            child / "fd" / "200"
                        ),
                        "request_fd_201_sha256": hashlib.sha256(
                            request_payload
                        ).hexdigest(),
                        "manifest_fd_202_sha256": _proc_file_sha256(
                            child / "fd" / "202"
                        ),
                        "pure_archive_fd_203_sha256": _proc_file_sha256(
                            child / "fd" / "203"
                        ),
                    }
                )
                identity_valid = (
                    type(request) is dict
                    and set(request) == request_keys
                    and request["schema"]
                    == "controlled_real10k_20k_runtime_launch_request_v1"
                    and request["entrypoint"] == argv[8]
                    and request["expected_bootstrap_sha256"]
                    == expected_bootstrap_sha
                    and request["expected_manifest_sha256"] == expected_manifest_sha
                    and request["expected_pure_archive_sha256"] == expected_pure_sha
                    and request["bootstrap_fd"] == 200
                    and request["manifest_fd"] == 202
                    and request["pure_archive_fd"] == 203
                    and request["attestation_fd"] == 204
                    and executable_sha == expected_executable_sha
                    and observed["bootstrap_fd_200_sha256"]
                    == expected_bootstrap_sha
                    and observed["manifest_fd_202_sha256"] == expected_manifest_sha
                    and observed["pure_archive_fd_203_sha256"] == expected_pure_sha
                )
                if identity_valid:
                    role = sealed_role[argv[8]]
            else:
                matching_fd: Path | None = None
                for descriptor_path in (child / "fd").iterdir():
                    try:
                        descriptor_stat = descriptor_path.stat()
                    except OSError:
                        continue
                    if (
                        descriptor_stat.st_dev == packaged_preflight.metadata.st_dev
                        and descriptor_stat.st_ino == packaged_preflight.metadata.st_ino
                    ):
                        matching_fd = descriptor_path
                        break
                if matching_fd is not None:
                    observed["raw_preflight_held_fd"] = matching_fd.name
                    observed["raw_preflight_script_sha256"] = _proc_file_sha256(
                        matching_fd
                    )
                    identity_valid = (
                        executable_sha == expected_executable_sha
                        and observed["raw_preflight_script_sha256"]
                        == packaged_preflight.sha256
                    )
                    if identity_valid:
                        role = "preflight_code"
        except (OSError, UnicodeError, PreflightError):
            identity_valid = False
        matches.append(
            {
                "pid": int(child.name),
                "role": role,
                "argv": argv,
                "argv_bytes_sha256": hashlib.sha256(raw).hexdigest(),
                "identity_valid": identity_valid,
                "sealed_request": request,
                "observed_descriptor_identity": observed,
            }
        )
    matches.sort(key=lambda record: record["pid"])
    return {
        "schema": "controlled_real10k_20k_preflight_process_audit_v2",
        "uid": uid,
        "current_pid": os.getpid(),
        "substring_matching_used": False,
        "exact_argv_executable_and_descriptor_identity_required": True,
        "matches": matches,
        "match_count": len(matches),
    }


def _descriptor_path(descriptor: int) -> str:
    linux_path = f"/proc/self/fd/{descriptor}"
    if Path("/proc/self/fd").is_dir():
        return linux_path
    fallback = f"/dev/fd/{descriptor}"
    if Path("/dev/fd").is_dir():
        return fallback
    raise PreflightError("descriptor filesystem is unavailable for isolated execution")


def _linux_descriptor_execution_available() -> bool:
    return Path("/proc/self/fd").is_dir()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise PreflightError("protected snapshot write made no progress")
        offset += written


def _protected_snapshot(file: VerifiedFile, *, executable: bool = False) -> int:
    """Copy verified bytes into an anonymous, sealed Linux snapshot.

    Linux/MARS uses memfd seals, so even the same UID cannot rewrite bytes
    between verification and child consumption.  The non-Linux branch exists
    only for local author tests; production host identity requires Linux.
    """

    if file.snapshot is None:
        _verify_file_continuity(file)
        payload = _read_descriptor(file.descriptor)
    else:
        payload = _verified_bytes(file)
    descriptor: int
    if hasattr(os, "memfd_create") and _linux_descriptor_execution_available():
        flags = int(getattr(os, "MFD_CLOEXEC", 0)) | int(
            getattr(os, "MFD_ALLOW_SEALING", 0)
        )
        descriptor = os.memfd_create("controlled-preflight-snapshot", flags=flags)
        try:
            _write_all(descriptor, payload)
            os.fchmod(descriptor, 0o500 if executable else 0o400)
            os.fsync(descriptor)
            required_seals = (
                int(getattr(fcntl, "F_SEAL_WRITE", 0x0008))
                | int(getattr(fcntl, "F_SEAL_GROW", 0x0004))
                | int(getattr(fcntl, "F_SEAL_SHRINK", 0x0002))
                | int(getattr(fcntl, "F_SEAL_SEAL", 0x0001))
            )
            fcntl.fcntl(
                descriptor,
                int(getattr(fcntl, "F_ADD_SEALS", 1033)),
                required_seals,
            )
            observed_seals = fcntl.fcntl(
                descriptor, int(getattr(fcntl, "F_GET_SEALS", 1034))
            )
            if observed_seals & required_seals != required_seals:
                raise PreflightError("protected Linux snapshot lacks required write seals")
        except BaseException:
            os.close(descriptor)
            raise
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix="controlled-preflight-snapshot-")
        try:
            os.unlink(temporary_name)
            _write_all(descriptor, payload)
            os.fchmod(descriptor, 0o500 if executable else 0o400)
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
    if _sha256_bytes(_read_descriptor(descriptor)) != file.sha256:
        os.close(descriptor)
        raise PreflightError(f"{file.label} protected snapshot SHA-256 mismatch")
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor


def _run_verified_python(
    python: VerifiedFile,
    arguments: Sequence[str],
    *,
    pass_fds: Sequence[int],
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    inherited = tuple(dict.fromkeys((python.descriptor, *pass_fds)))
    argv = [str(python.path), *arguments]
    executable: str | None = None
    if _linux_descriptor_execution_available():
        # Kernel execution consumes the already verified inode.  argv[0] stays
        # the canonical venv path so CPython retains the frozen venv prefix.
        executable = f"/proc/self/fd/{python.descriptor}"
    if executable is not None:
        kwargs["executable"] = executable
    return subprocess.run(argv, pass_fds=inherited, **kwargs)


def _minimal_runtime_environment(*, native_smoke: bool = False) -> dict[str, str]:
    environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    if native_smoke:
        environment["CONTROLLED_REAL10K_20K_PREFLIGHT_ONLY"] = "1"
    return environment


def _runtime_executable(raw: str, expected_sha: str) -> VerifiedFile:
    path = _absolute_path(raw, "--python-executable", must_exist=True)
    _reject_symlink_chain(path, "Python executable")
    if path.resolve(strict=True) != path:
        raise PreflightError("Python executable must be a canonical non-symlink path")
    verified = _open_verified_file(
        path,
        "Python executable",
        expected_sha256=expected_sha,
        capture_snapshot=False,
        require_nlink_one=False,
    )
    metadata = verified.metadata
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        verified.close()
        raise PreflightError("Python executable is not an executable regular file")
    return verified


def _load_verified_runtime_bootstrap(package: Mapping[str, Any]) -> Any:
    """Execute only the already-verified bootstrap snapshot in this process."""

    verified = package["roles"]["runtime_bootstrap_code"]["verified_file"]
    payload = _verified_bytes(verified)
    module_name = f"_controlled_runtime_bootstrap_{verified.sha256}"
    spec = importlib.util.spec_from_loader(
        module_name, loader=None, origin=str(verified.path)
    )
    if spec is None:
        raise PreflightError("cannot create verified runtime-bootstrap module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        exec(compile(payload.decode("utf-8"), str(verified.path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    if not callable(getattr(module, "prepare_sealed_runtime_launch", None)):
        raise PreflightError("verified runtime bootstrap lacks sealed-launch API")
    return module


def _validate_module_origins(
    value: Any, manifest: Mapping[str, Any], label: str
) -> None:
    if type(value) is not dict or not value:
        raise PreflightError(f"{label} is not a non-empty exact object")
    member_sha = {
        record["module"]: record["sha256"]
        for record in manifest["members"]
        if record["module"] is not None
    }
    extension_sha = {
        record["module"]: record["sha256"]
        for record in manifest["native_extensions"]
    }
    for module_name, record in value.items():
        if type(module_name) is not str or type(record) is not dict or set(record) != {
            "kind",
            "origin",
            "sha256",
        }:
            raise PreflightError(f"{label} member schema is not exact")
        if record["kind"] == "sealed_pure_zip":
            expected_sha = member_sha.get(module_name)
            origin_ok = type(record["origin"]) is str and record["origin"].startswith(
                "descriptor-zip:/proc/self/fd/203!/"
            )
        elif record["kind"] == "sealed_native_extension":
            expected_sha = extension_sha.get(module_name)
            origin_ok = type(record["origin"]) is str and record["origin"].startswith(
                "/proc/self/fd/"
            )
        else:
            expected_sha = None
            origin_ok = False
        if expected_sha is None or record["sha256"] != expected_sha or not origin_ok:
            raise PreflightError(f"{label} member is not manifest/descriptor bound")


def _validate_runtime_attestations(
    payload: bytes,
    *,
    package: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise PreflightError("sealed runtime attestation is not UTF-8") from exc
    lines = text.splitlines()
    if len(lines) != 2 or not text.endswith("\n"):
        raise PreflightError("sealed runtime must emit exactly startup and terminal attestations")
    startup = _strict_json_loads(lines[0], "sealed runtime startup attestation")
    terminal = _strict_json_loads(lines[1], "sealed runtime terminal attestation")
    startup_keys = {
        "schema",
        "status",
        "entrypoint",
        "entrypoint_sha256",
        "manifest_sha256",
        "pure_archive_sha256",
        "bootstrap_sha256",
        "python",
        "python_flags",
        "numpy_version",
        "module_origins",
        "native_library_sha256",
        "native_extension_sha256",
        "system_library_allowlist",
        "site_initialization_disabled",
        "external_package_fallback_allowed",
    }
    terminal_keys = {
        "schema",
        "status",
        "entrypoint",
        "exit_code",
        "manifest_sha256",
        "pure_archive_sha256",
        "bootstrap_sha256",
        "module_origins",
        "system_library_allowlist",
        "external_package_fallback_allowed",
    }
    if type(startup) is not dict or set(startup) != startup_keys:
        raise PreflightError("sealed runtime startup attestation keyset is not exact")
    if type(terminal) is not dict or set(terminal) != terminal_keys:
        raise PreflightError("sealed runtime terminal attestation keyset is not exact")
    expected_state = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "native_smoke",
        "manifest_sha256": package["role_identity"][
            "runtime_dependency_closure_json"
        ]["sha256"],
        "pure_archive_sha256": manifest["pure_archive"]["sha256"],
        "bootstrap_sha256": package["role_identity"]["runtime_bootstrap_code"][
            "sha256"
        ],
    }
    _require_exact_json_equal(
        {key: startup[key] for key in expected_state},
        expected_state,
        "sealed runtime startup identity",
    )
    _require_exact_json_equal(
        {key: terminal[key] for key in expected_state},
        expected_state,
        "sealed runtime terminal identity",
    )
    if (
        startup["status"] != "PASS_DESCRIPTOR_CLOSED_STARTUP"
        or startup["entrypoint_sha256"]
        != package["role_identity"]["native_smoke_test"]["sha256"]
        or startup["python"]
        != {
            key: manifest["python"][key]
            for key in ("implementation", "version", "abi_tag", "platform")
        }
        or startup["python_flags"]
        != {"isolated": 1, "no_site": 1, "dont_write_bytecode": True}
        or startup["numpy_version"] != manifest["numpy"]["version"]
        or startup["native_library_sha256"]
        != {
            record["soname"]: record["sha256"]
            for record in manifest["native_libraries"]
        }
        or startup["native_extension_sha256"]
        != {
            record["module"]: record["sha256"]
            for record in manifest["native_extensions"]
        }
        or startup["system_library_allowlist"]
        != manifest["system_library_allowlist"]
        or startup["site_initialization_disabled"] is not True
        or startup["external_package_fallback_allowed"] is not False
        or terminal["status"] != "PASS_DESCRIPTOR_CLOSED_TERMINAL"
        or type(terminal["exit_code"]) is not int
        or terminal["exit_code"] != 0
        or terminal["system_library_allowlist"]
        != manifest["system_library_allowlist"]
        or terminal["external_package_fallback_allowed"] is not False
    ):
        raise PreflightError("sealed runtime startup/terminal attestation is not PASS-exact")
    _validate_module_origins(startup["module_origins"], manifest, "startup module origins")
    _validate_module_origins(terminal["module_origins"], manifest, "terminal module origins")
    return startup, terminal


def _runtime_probe(
    python: VerifiedFile,
    package: Mapping[str, Any],
    native_tests: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the runtime identity from the one descriptor-sealed native smoke."""

    audit = (
        dict(native_tests)
        if native_tests is not None
        else _run_native_tests(python, package, REQUIRED_NATIVE_TEST_ROLES, 60)
    )
    runtime = audit.get("runtime_identity")
    if type(runtime) is not dict:
        raise PreflightError("native smoke lacks its descriptor-sealed runtime identity")
    return dict(runtime)


def _run_native_tests(
    python: VerifiedFile,
    package: Mapping[str, Any],
    roles: Sequence[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    if tuple(roles) != REQUIRED_NATIVE_TEST_ROLES:
        raise PreflightError("exactly the one reviewed native-smoke role is required")
    native_file = package["roles"]["native_smoke_test"]["verified_file"]
    bootstrap_file = package["roles"]["runtime_bootstrap_code"]["verified_file"]
    runtime_manifest_file = package["roles"][
        "runtime_dependency_closure_json"
    ]["verified_file"]
    tree_root = package["root"] / PACKAGE_ROLE_DESTINATIONS[
        "runtime_dependency_closure_tree"
    ]
    bootstrap = _load_verified_runtime_bootstrap(package)

    snapshot_fds: dict[str, int] = {}

    def ensure_snapshot(relative: str) -> int:
        if relative not in snapshot_fds:
            snapshot_fds[relative] = _protected_snapshot(
                package["held_by_relative"][relative]
            )
        return snapshot_fds[relative]

    def descriptor_record(relative: str) -> dict[str, str]:
        verified = package["held_by_relative"][relative]
        return {
            "descriptor_path": _descriptor_path(ensure_snapshot(relative)),
            "display_path": str(verified.path),
            "sha256": verified.sha256,
        }

    indexed_relatives = sorted(
        set(package["exact_files"]) - {SHA_INDEX_NAME, PACKAGE_COMMIT_NAME}
    )
    role_records: dict[str, dict[str, Any]] = {}
    for role in sorted(package["roles"]):
        artifact = package["roles"][role]
        if artifact["kind"] == "file":
            role_records[role] = {
                "kind": "file",
                **descriptor_record(artifact["path"]),
            }
        else:
            members = []
            for member in artifact["members"]:
                member_record = descriptor_record(member["path"])
                members.append(
                    {
                        "path": member["path"],
                        **member_record,
                        "size_bytes": member["size_bytes"],
                    }
                )
            role_records[role] = {
                "kind": "tree",
                "display_path": str(tree_root),
                "sha256": artifact["sha256"],
                "inventory_sha256": artifact["inventory_sha256"],
                "members": members,
            }
    request = {
        "schema": NATIVE_SMOKE_REQUEST_SCHEMA,
        "manifest": descriptor_record(MANIFEST_NAME),
        "receipt": descriptor_record(RECEIPT_NAME),
        "independent_qa_required": descriptor_record(QA_REQUIRED_NAME),
        "sha_index": descriptor_record(SHA_INDEX_NAME),
        "package_commit": descriptor_record(PACKAGE_COMMIT_NAME),
        "package_build_attempt_body": descriptor_record(
            "@external_build_attempt_body"
        ),
        "package_build_attempt_committed": descriptor_record(
            "@external_build_attempt_committed"
        ),
        "process_singleton_lock": descriptor_record(PACKAGE_SINGLETON_LOCK_NAME),
        "indexed_files": {
            relative: descriptor_record(relative) for relative in indexed_relatives
        },
        "roles": role_records,
        "runtime_manifest_sha256": runtime_manifest_file.sha256,
    }

    attestation_fd, attestation_path_raw = tempfile.mkstemp(
        prefix=".controlled-native-smoke-attestation-",
        dir=str(package["root"].parent),
    )
    attestation_path = Path(attestation_path_raw)
    result: subprocess.CompletedProcess[bytes]
    started = time.monotonic()
    launch = None
    try:
        os.fchmod(attestation_fd, 0o600)
        metadata = os.fstat(attestation_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size != 0
            or metadata.st_nlink != 1
        ):
            raise PreflightError("native-smoke attestation file identity is invalid")
        os.fsync(attestation_fd)
        launch = bootstrap.prepare_sealed_runtime_launch(
            manifest_path=runtime_manifest_file.path,
            expected_manifest_sha256=runtime_manifest_file.sha256,
            tree_root=tree_root,
            bootstrap_path=bootstrap_file.path,
            expected_bootstrap_sha256=bootstrap_file.sha256,
            entrypoint="native_smoke",
            entrypoint_argv=[str(native_file.path)],
            attestation_output_fd=attestation_fd,
        )
        with launch:
            result = _run_verified_python(
                python,
                ["-I", "-B", "-S", *launch.process_argv_suffix],
                pass_fds=(*launch.pass_fds, *snapshot_fds.values()),
                input=_json_bytes(request),
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
                cwd=str(package["root"].parent),
                env=_minimal_runtime_environment(native_smoke=True),
            )
        attestation_payload = _read_descriptor(attestation_fd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"sealed native smoke failed to execute: {exc}") from exc
    finally:
        for descriptor in snapshot_fds.values():
            os.close(descriptor)
        os.close(attestation_fd)
        try:
            attestation_path.unlink()
        except FileNotFoundError:
            pass
    elapsed = time.monotonic() - started
    audit: dict[str, Any] = {
        "requested": True,
        "roles": list(roles),
        "returncode": int(result.returncode),
        "stdout_sha256": _sha256_bytes(result.stdout),
        "stdout_size_bytes": len(result.stdout),
        "stderr_sha256": _sha256_bytes(result.stderr),
        "stderr_size_bytes": len(result.stderr),
        "elapsed_seconds": elapsed,
        "attestation_sha256": _sha256_bytes(attestation_payload),
        "attestation_size_bytes": len(attestation_payload),
        "attestation_ephemeral_no_clobber_file": True,
    }
    if result.returncode != 0:
        raise PreflightError(
            f"sealed native smoke failed: returncode={result.returncode} "
            f"stdout_sha256={audit['stdout_sha256']} stderr_sha256={audit['stderr_sha256']}"
        )
    if result.stderr != b"":
        raise PreflightError(
            f"sealed native smoke emitted unexpected stderr: sha256={audit['stderr_sha256']}"
        )
    try:
        observed = _strict_json_loads(
            result.stdout.decode("utf-8"), "sealed native smoke result"
        )
    except (UnicodeError, PreflightError) as exc:
        raise PreflightError("sealed native smoke did not emit one exact JSON object") from exc
    if launch is None:
        raise AssertionError("sealed runtime launch was not created")
    expected_state = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "native_smoke",
        "manifest_sha256": runtime_manifest_file.sha256,
        "pure_archive_sha256": launch.manifest["pure_archive"]["sha256"],
        "bootstrap_sha256": bootstrap_file.sha256,
    }
    expected_result = {
        "schema": NATIVE_SMOKE_RESULT_SCHEMA,
        "status": "PASS",
        "test_id": NATIVE_SMOKE_TEST_ID,
        "manifest_sha256": package["manifest_sha256"],
        "sha_index_sha256": package["index_sha256"],
        "package_commit_sha256": package["commit_sha256"],
        "package_build_attempt_body_sha256": package[
            "build_attempt_body_sha256"
        ],
        "package_build_attempt_committed_sha256": package[
            "build_attempt_committed_sha256"
        ],
        "runtime_manifest_sha256": runtime_manifest_file.sha256,
        "role_count": len(PACKAGE_REQUIRED_ROLES),
        "compiled_python_role_count": sum(
            role in REQUIRED_CODE_ROLES or role in REQUIRED_NATIVE_TEST_ROLES
            for role in package["roles"]
        ),
        "consumed_role_sha256": dict(package["role_sha256"]),
        "checks": {
            "isolated_python_I_B_S": True,
            "exact_package_v5_role_destinations": True,
            "descriptor_snapshots_only": True,
            "package_commit_and_external_attempt_body_committed_bound": True,
            "package_sha_closure": True,
            "runtime_dependency_closure_bound": True,
            "process_singleton_contract_bound": True,
            "sealed_materialization_runner_trainer_evaluator_imported": True,
            "numpy_shared_splitter_descriptor_bound": True,
            "active_descriptor_runtime_exact": True,
            "result_blind": True,
            "no_training_metrics_emx_or_signal": True,
        },
        "runtime": expected_state,
    }
    _require_exact_json_equal(
        observed, expected_result, "sealed native-smoke PASS payload"
    )
    startup, terminal = _validate_runtime_attestations(
        attestation_payload, package=package, manifest=launch.manifest
    )
    runtime_identity = {
        "schema": "controlled_real10k_20k_preflight_runtime_identity_v2",
        "python_executable_path": str(python.path),
        "python_executable_sha256": python.sha256,
        "python_version": startup["python"]["version"],
        "numpy_version": startup["numpy_version"],
        "active_runtime": expected_state,
        "startup_attestation": startup,
        "terminal_attestation": terminal,
        "compiled_role_count": expected_result["compiled_python_role_count"],
        "consumed_code_role_sha256": dict(package["code_role_sha256"]),
        "native_smoke_result_sha256": audit["stdout_sha256"],
        "native_smoke_attestation_sha256": audit["attestation_sha256"],
        "descriptor_closed": True,
        "raw_runtime_fallback_authorized": False,
    }
    audit.update(
        {
            "protocol_schema": NATIVE_SMOKE_RESULT_SCHEMA,
            "test_id": NATIVE_SMOKE_TEST_ID,
            "executed_test_count": 1,
            "exact_structured_pass": True,
            "isolated_python_flags": ["-I", "-B", "-S"],
            "environment_keyset": sorted(_minimal_runtime_environment(native_smoke=True)),
            "runtime_identity": runtime_identity,
        }
    )
    return audit


def _receipt_authorities() -> dict[str, bool]:
    return {
        "direct_data_materialization_authorized": False,
        "training_authorized": False,
        "common_test_access_authorized": False,
        "numerical_metric_access_authorized": False,
        "fresh_emx_authorized": False,
        "process_signal_authorized": False,
    }


def _canonical_receipt_target(raw: str) -> Path:
    path = _absolute_path(raw, "--receipt-dir", must_exist=False)
    if path.name in {"", ".", ".."}:
        raise PreflightError("preflight receipt directory has an unsafe basename")
    _reject_symlink_chain(path.parent, "receipt parent")
    if not path.parent.is_dir() or path.parent.resolve(strict=True) != path.parent:
        raise PreflightError("preflight receipt parent must be an existing canonical directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"no-clobber preflight receipt directory exists: {path}")
    return path


def _create_receipt_transaction(raw: str) -> _ReceiptTransaction:
    path = _canonical_receipt_target(raw)
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent_fd)
        root_fd = _open_dir_at(parent_fd, path.name)
    except BaseException:
        os.close(parent_fd)
        raise
    transaction = _ReceiptTransaction(path, parent_fd, root_fd)
    transaction.assert_directory_continuity()
    return transaction


def _lease_payload(transaction: _ReceiptTransaction, nonce: str) -> dict[str, Any]:
    return {
        "schema": PREFLIGHT_LEASE_SCHEMA,
        "state": "PREPARED",
        "challenge_nonce": nonce,
        "receipt_root": {
            "path": str(transaction.path),
            "identity": transaction.root_identity,
        },
        "created_utc": _utc_z_now(),
        "consumed_utc": LEASE_PLACEHOLDER_UTC,
        "single_use": True,
        "retry_authorized": False,
        "authorities": _receipt_authorities(),
    }


def _create_external_lease(
    transaction: _ReceiptTransaction, nonce: str
) -> dict[str, Any]:
    descriptor = os.open(
        transaction.lease_name,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=transaction.parent_fd,
    )
    transaction.lease_fd = descriptor
    payload = _json_bytes(_lease_payload(transaction, nonce))
    _write_all(descriptor, payload)
    os.ftruncate(descriptor, len(payload))
    os.fchmod(descriptor, 0o600)
    os.fsync(descriptor)
    metadata = os.fstat(descriptor)
    binding = {
        "path": str(transaction.lease_path),
        "sha256": _sha256_bytes(payload),
        "identity": _regular_file_identity(metadata),
        "schema": PREFLIGHT_LEASE_SCHEMA,
        "state": "PREPARED",
    }
    transaction.lease_initial_raw = payload
    transaction.lease_initial_binding = binding
    transaction.assert_lease_continuity()
    os.fsync(transaction.parent_fd)
    return binding


def _prepare_index_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        f"{record['sha256']}  {record['path']}\n" for record in records
    ).encode("ascii")


def _receipt_transaction_go_binding(
    transaction: _ReceiptTransaction,
) -> dict[str, Any]:
    if transaction.prepared_bindings is None or transaction.lease_initial_binding is None:
        raise PreflightError("prepared receipt transaction bindings are unavailable")
    return {
        "schema": "controlled_real10k_20k_mars_preflight_transaction_binding_v3",
        "receipt_root": {
            "path": str(transaction.path),
            "identity": transaction.root_identity,
        },
        "receipt_parent": {
            "path": str(transaction.parent_path),
            "identity": transaction.parent_identity,
        },
        "prepared_artifacts": transaction.prepared_bindings,
        "external_one_use_lease": transaction.lease_initial_binding,
        "success_terminal_marker": PREFLIGHT_COMMITTED_NAME,
        "failure_terminal_marker": PREFLIGHT_FATAL_FAIL_NAME,
    }


def _expected_execution_go_requirement() -> dict[str, Any]:
    return {
        "schema": CODE_GO_SCHEMA,
        "scope": CODE_GO_SCOPE,
        "top_level_keys": sorted(CODE_GO_TOP_LEVEL_KEYS),
        "binding_keys": list(PACKAGE_REQUIRED_GO_BINDING_KEYS),
        "status": "PASS",
        "verdict": "EXACT_CODE_GO",
        "review": {"independent": True, "result_blind": True},
        "zero_findings": {"p0": 0, "p1": 0},
        "authorities": EXPECTED_AUTHORITIES,
        "freshness": {
            "strict_utc": True,
            "issued_lte_now_lt_expires": True,
            "maximum_lifetime_seconds": 86400,
            "nonce_pattern": "^[0-9a-f]{32}$",
        },
        "recursive_exact_json_types": True,
        "duplicate_keys_rejected": True,
        "nonfinite_numbers_rejected": True,
        "bind_receipt_root_and_parent_inode": True,
        "bind_external_lease_inode_and_sha256": True,
    }


def _prepare_phase(
    transaction: _ReceiptTransaction, args: argparse.Namespace
) -> dict[str, Any]:
    os.fsync(transaction.root_fd)
    os.fsync(transaction.parent_fd)
    nonce = os.urandom(16).hex()
    if not GO_NONCE_PATTERN.fullmatch(nonce):
        raise AssertionError("receipt challenge nonce generation failed")
    lease_binding = _create_external_lease(transaction, nonce)
    execution_contract = _compute_expected_base_go_bindings(args, transaction)
    prepared_payload = {
        "schema": PREPARED_SCHEMA,
        "status": "PREPARED_AWAITING_INDEPENDENT_EXACT_CODE_GO",
        "phase": "PREPARE",
        "generated_utc": _utc_z_now(),
        "challenge_nonce": nonce,
        "receipt_root": {
            "path": str(transaction.path),
            "identity": transaction.root_identity,
        },
        "receipt_parent": {
            "path": str(transaction.parent_path),
            "identity": transaction.parent_identity,
        },
        "external_one_use_lease": lease_binding,
        "execution_contract": execution_contract,
        "required_execute_schema": CODE_GO_SCHEMA,
        "required_execute_scope": CODE_GO_SCOPE,
        "authorities": _receipt_authorities(),
        "next_legal_action": "INDEPENDENT_RESULT_BLIND_REVIEW_AND_EXACT_CODE_GO_ONLY",
    }
    prepared_record = _write_file_at(
        transaction.root_fd, PREPARED_NAME, _json_bytes(prepared_payload)
    )
    qa_payload = {
        "schema": EXECUTION_QA_REQUIRED_SCHEMA,
        "status": "INDEPENDENT_QA_REQUIRED",
        "verdict": "NO_GO_PENDING_EXACT_CODE_GO_V3",
        "challenge_nonce": nonce,
        "prepared_receipt": prepared_record,
        "external_one_use_lease": lease_binding,
        "required_go": _expected_execution_go_requirement(),
        "authorities": _receipt_authorities(),
        "next_legal_action": "EXTERNAL_INDEPENDENT_QA_ONLY",
    }
    qa_record = _write_file_at(
        transaction.root_fd,
        EXECUTION_QA_REQUIRED_NAME,
        _json_bytes(qa_payload),
    )
    prepare_index_record = _write_file_at(
        transaction.root_fd,
        PREPARE_SHA_INDEX_NAME,
        _prepare_index_bytes((prepared_record, qa_record)),
    )
    os.fsync(transaction.root_fd)
    os.fsync(transaction.parent_fd)
    transaction.assert_directory_continuity()
    transaction.assert_lease_continuity()
    if set(os.listdir(transaction.root_fd)) != {
        PREPARED_NAME,
        EXECUTION_QA_REQUIRED_NAME,
        PREPARE_SHA_INDEX_NAME,
    }:
        raise PreflightError("PREPARE receipt root closure is not exact")
    transaction.prepared = prepared_payload
    transaction.prepared_bindings = {
        "prepared_receipt": prepared_record,
        "execution_qa_required": qa_record,
        "prepare_sha256sums": prepare_index_record,
    }
    return {
        "receipt_dir": transaction.path,
        "prepared": transaction.path / PREPARED_NAME,
        "execution_qa_required": transaction.path / EXECUTION_QA_REQUIRED_NAME,
        "prepare_sha_index": transaction.path / PREPARE_SHA_INDEX_NAME,
        "lease": transaction.lease_path,
        "transaction_binding": _receipt_transaction_go_binding(transaction),
    }


def _expected_prepared_keyset() -> set[str]:
    return {
        "schema",
        "status",
        "phase",
        "generated_utc",
        "challenge_nonce",
        "receipt_root",
        "receipt_parent",
        "external_one_use_lease",
        "execution_contract",
        "required_execute_schema",
        "required_execute_scope",
        "authorities",
        "next_legal_action",
    }


def _open_execution_transaction(raw: str) -> _ReceiptTransaction:
    path = _absolute_path(raw, "--receipt-dir", must_exist=True)
    _reject_symlink_chain(path.parent, "receipt parent")
    if path.parent.resolve(strict=True) != path.parent:
        raise PreflightError("receipt parent is not canonical")
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        root_fd = _open_dir_at(parent_fd, path.name)
    except BaseException:
        os.close(parent_fd)
        raise
    transaction = _ReceiptTransaction(path, parent_fd, root_fd)
    try:
        observed_names = set(os.listdir(root_fd))
        if PREFLIGHT_COMMITTED_NAME in observed_names:
            raise FileExistsError("preflight transaction is already COMMITTED")
        if PREFLIGHT_FATAL_FAIL_NAME in observed_names:
            raise FileExistsError("preflight transaction is already terminal FAIL_NO_GO")
        expected_names = {
            PREPARED_NAME,
            EXECUTION_QA_REQUIRED_NAME,
            PREPARE_SHA_INDEX_NAME,
        }
        if observed_names != expected_names:
            raise PreflightError(
                "EXECUTE requires exact PREPARE closure: "
                f"missing={sorted(expected_names-observed_names)} "
                f"extra={sorted(observed_names-expected_names)}"
            )
        prepared_raw, prepared_record = _read_file_at(
            root_fd, PREPARED_NAME, "prepared preflight receipt"
        )
        qa_raw, qa_record = _read_file_at(
            root_fd,
            EXECUTION_QA_REQUIRED_NAME,
            "preflight execution QA requirement",
        )
        index_raw, index_record = _read_file_at(
            root_fd, PREPARE_SHA_INDEX_NAME, "PREPARE SHA-256 index"
        )
        expected_index = _prepare_index_bytes((prepared_record, qa_record))
        if index_raw != expected_index:
            raise PreflightError("PREPARE SHA-256 index is not the exact closure")
        prepared = _read_json_bytes(prepared_raw, "prepared preflight receipt")
        if set(prepared) != _expected_prepared_keyset():
            raise PreflightError("prepared preflight receipt keyset is not exact")
        if (
            prepared["schema"] != PREPARED_SCHEMA
            or prepared["status"] != "PREPARED_AWAITING_INDEPENDENT_EXACT_CODE_GO"
            or prepared["phase"] != "PREPARE"
            or prepared["required_execute_schema"] != CODE_GO_SCHEMA
            or prepared["required_execute_scope"] != CODE_GO_SCOPE
            or prepared["next_legal_action"]
            != "INDEPENDENT_RESULT_BLIND_REVIEW_AND_EXACT_CODE_GO_ONLY"
        ):
            raise PreflightError("prepared preflight receipt contract is invalid")
        _require_exact_json_equal(
            prepared["authorities"], _receipt_authorities(), "prepared authorities"
        )
        if type(prepared["challenge_nonce"]) is not str or not GO_NONCE_PATTERN.fullmatch(
            prepared["challenge_nonce"]
        ):
            raise PreflightError("prepared challenge nonce is invalid")
        _require_exact_json_equal(
            prepared["receipt_root"],
            {"path": str(path), "identity": transaction.root_identity},
            "prepared receipt-root binding",
        )
        _require_exact_json_equal(
            prepared["receipt_parent"],
            {"path": str(path.parent), "identity": transaction.parent_identity},
            "prepared receipt-parent binding",
        )
        qa = _read_json_bytes(qa_raw, "preflight execution QA requirement")
        if set(qa) != {
            "schema",
            "status",
            "verdict",
            "challenge_nonce",
            "prepared_receipt",
            "external_one_use_lease",
            "required_go",
            "authorities",
            "next_legal_action",
        }:
            raise PreflightError("execution QA requirement keyset is not exact")
        if (
            qa["schema"] != EXECUTION_QA_REQUIRED_SCHEMA
            or qa["status"] != "INDEPENDENT_QA_REQUIRED"
            or qa["verdict"] != "NO_GO_PENDING_EXACT_CODE_GO_V3"
            or qa["challenge_nonce"] != prepared["challenge_nonce"]
            or qa["next_legal_action"] != "EXTERNAL_INDEPENDENT_QA_ONLY"
        ):
            raise PreflightError("execution QA requirement contract is invalid")
        _require_exact_json_equal(
            qa["prepared_receipt"], prepared_record, "QA prepared-receipt binding"
        )
        _require_exact_json_equal(
            qa["external_one_use_lease"],
            prepared["external_one_use_lease"],
            "QA external-lease binding",
        )
        _require_exact_json_equal(
            qa["authorities"], _receipt_authorities(), "QA authorities"
        )
        _require_exact_json_equal(
            qa["required_go"],
            _expected_execution_go_requirement(),
            "QA exact external GO requirement",
        )
        lease_binding = prepared["external_one_use_lease"]
        if not isinstance(lease_binding, dict) or lease_binding.get("path") != str(
            transaction.lease_path
        ):
            raise PreflightError("prepared external lease path is not exact")
        lease_fd = os.open(
            transaction.lease_name,
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        transaction.lease_fd = lease_fd
        try:
            fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PreflightError("external one-use lease is already held") from exc
        lease_raw = _read_all(lease_fd)
        lease_meta = os.fstat(lease_fd)
        observed_lease_binding = {
            "path": str(transaction.lease_path),
            "sha256": _sha256_bytes(lease_raw),
            "identity": _regular_file_identity(lease_meta),
            "schema": PREFLIGHT_LEASE_SCHEMA,
            "state": "PREPARED",
        }
        _require_exact_json_equal(
            observed_lease_binding, lease_binding, "prepared external lease live binding"
        )
        lease_payload = _read_json_bytes(lease_raw, "external one-use lease")
        _require_exact_json_equal(
            lease_payload,
            {
                **_lease_payload(transaction, prepared["challenge_nonce"]),
                "created_utc": lease_payload.get("created_utc"),
            },
            "external one-use lease payload",
        )
        _parse_go_utc(lease_payload["created_utc"], "lease created_utc")
        transaction.prepared = prepared
        transaction.prepared_bindings = {
            "prepared_receipt": prepared_record,
            "execution_qa_required": qa_record,
            "prepare_sha256sums": index_record,
        }
        transaction.lease_initial_raw = lease_raw
        transaction.lease_initial_binding = observed_lease_binding
        transaction.assert_directory_continuity()
        transaction.assert_lease_continuity()
        return transaction
    except FileExistsError:
        transaction.close()
        raise
    except BaseException as exc:
        raise _ReceiptTransactionValidationError(transaction, exc) from exc


def _consume_external_lease(transaction: _ReceiptTransaction) -> dict[str, Any]:
    if transaction.lease_fd is None or transaction.lease_initial_raw is None:
        raise PreflightError("cannot consume unavailable external one-use lease")
    transaction.assert_directory_continuity()
    transaction.assert_lease_continuity()
    current = _read_all(transaction.lease_fd)
    if current != transaction.lease_initial_raw:
        raise PreflightError("external one-use lease changed before consumption")
    payload = _read_json_bytes(current, "external one-use lease before consumption")
    if payload.get("state") != "PREPARED" or payload.get("consumed_utc") != LEASE_PLACEHOLDER_UTC:
        raise PreflightError("external one-use lease is not in PREPARED state")
    consumed = dict(payload)
    consumed["state"] = "CONSUMED"
    consumed["consumed_utc"] = _utc_z_now()
    raw = _json_bytes(consumed)
    if len(raw) != len(current):
        raise AssertionError("external lease state transition is not equal-width")
    _write_all(transaction.lease_fd, raw)
    os.ftruncate(transaction.lease_fd, len(raw))
    os.fchmod(transaction.lease_fd, FILE_MODE)
    os.fsync(transaction.lease_fd)
    transaction.assert_lease_continuity()
    os.fsync(transaction.parent_fd)
    metadata = os.fstat(transaction.lease_fd)
    binding = {
        "path": str(transaction.lease_path),
        "sha256": _sha256_bytes(raw),
        "identity": _regular_file_identity(metadata),
        "schema": PREFLIGHT_LEASE_SCHEMA,
        "state": "CONSUMED",
    }
    transaction.lease_consumed_binding = binding
    return binding


def _expected_go_bindings(
    *,
    package: Mapping[str, Any],
    python_sha256: str,
    hostname: str,
    uid: int,
    boot_id: str,
    candidate_output_dirs: Sequence[Path],
    native_test_roles: Sequence[str],
    receipt_dir: Path,
    receipt_transaction: Mapping[str, Any],
) -> dict[str, Any]:
    implementation = package["execution_file"]
    transaction = dict(receipt_transaction)
    bindings = {
        "candidate_output_dirs": [str(path) for path in candidate_output_dirs],
        "code_role_identity": {
            role: package["role_identity"][role] for role in sorted(REQUIRED_CODE_ROLES)
        },
        "host_expected": {"hostname": hostname, "uid": uid, "boot_id": boot_id},
        "native_test_roles": list(native_test_roles),
        "package_build_attempt_body": _verified_file_binding(
            package["held_by_relative"]["@external_build_attempt_body"]
        ),
        "package_build_attempt_committed": _verified_file_binding(
            package["held_by_relative"]["@external_build_attempt_committed"]
        ),
        "package_commit": _verified_file_binding(
            package["held_by_relative"][PACKAGE_COMMIT_NAME]
        ),
        "package_independent_qa_required": _verified_file_binding(
            package["held_by_relative"][QA_REQUIRED_NAME]
        ),
        "package_manifest": {
            **_verified_file_binding(package["held_by_relative"][MANIFEST_NAME]),
            "package_root": str(package["root"]),
        },
        "package_receipt": _verified_file_binding(
            package["held_by_relative"][RECEIPT_NAME]
        ),
        "package_role_identity": package["role_identity"],
        "package_sha_index": {
            **_verified_file_binding(package["held_by_relative"][SHA_INDEX_NAME]),
            "self_hash_included": False,
        },
        "preflight_implementation": {
            "path": str(implementation.path),
            "sha256": implementation.sha256,
            "identity": _regular_file_identity(implementation.metadata),
        },
        "preflight_one_use_lease": transaction.get("external_one_use_lease", {}),
        "preflight_receipt_root": {
            "receipt_root": transaction.get("receipt_root", {}),
            "receipt_parent": transaction.get("receipt_parent", {}),
            "prepared_artifacts": transaction.get("prepared_artifacts", {}),
        },
        "preflight_terminal_commit": {
            "path": str(receipt_dir / PREFLIGHT_COMMITTED_NAME),
            "schema": PREFLIGHT_COMMITTED_SCHEMA,
            "status": "COMMITTED_PASS_PREFLIGHT_ONLY",
            "failure_marker": str(receipt_dir / PREFLIGHT_FATAL_FAIL_NAME),
        },
        "preregistration_role_identity": {
            role: package["role_identity"][role]
            for role in sorted(REQUIRED_PREREGISTRATION_ROLES)
        },
        "process_singleton_contract": {
            "file": _verified_file_binding(package["singleton_contract_file"]),
            "schema": package["singleton_contract"]["schema"],
            "canonical_json_sha256": _canonical_json_sha(package["singleton_contract"]),
            "protected_entrypoints": package["singleton_contract"]["protected_entrypoints"],
        },
        "process_singleton_lock": {
            **_verified_file_binding(package["singleton_lock_file"]),
            "operation": "LOCK_EX|LOCK_NB",
            "held_for_full_execute_lifetime": True,
        },
        "runtime_dependency_closure": {
            "declaration": package["runtime_dependency_closure"],
            "inventory_role_identity": package["role_identity"][
                "runtime_dependency_closure_json"
            ],
            "tree_role_identity": package["role_identity"][
                "runtime_dependency_closure_tree"
            ],
        },
        "runtime_entrypoints": package["runtime_entrypoints"],
        "runtime_expected": {
            "launch_protocol": "descriptor_bootstrap_only",
            "entrypoint": "native_smoke",
            "python_isolation_flags": ["-I", "-B", "-S"],
            "python_executable_sha256": python_sha256,
            "python": package["runtime_dependency_closure"]["python"],
            "numpy": package["runtime_dependency_closure"]["numpy"],
            "runtime_manifest_sha256": package["role_identity"][
                "runtime_dependency_closure_json"
            ]["sha256"],
            "runtime_tree_sha256": package["role_identity"][
                "runtime_dependency_closure_tree"
            ]["sha256"],
            "bootstrap_sha256": package["role_identity"]["runtime_bootstrap_code"][
                "sha256"
            ],
            "pure_archive": package["runtime_dependency_closure"]["pure_archive"],
            "startup_and_terminal_attestation_required": True,
            "raw_runtime_fallback_authorized": False,
        },
        "source_role_identity": {
            role: package["role_identity"][role]
            for role in sorted(REQUIRED_SOURCE_ROLES)
        },
    }
    if tuple(sorted(bindings)) != tuple(sorted(PACKAGE_REQUIRED_GO_BINDING_KEYS)):
        raise AssertionError("preflight GO binding keyset drifted from package-v4 QA")
    return bindings


def _compute_expected_base_go_bindings(
    args: argparse.Namespace, transaction: _ReceiptTransaction
) -> dict[str, Any]:
    resources: list[VerifiedFile] = []
    try:
        package = _audit_package(
            Path(args.package_dir),
            expected_manifest_sha256=args.expected_manifest_sha256,
            expected_index_sha256=args.expected_sha_index_sha256,
            expected_commit_sha256=args.expected_package_commit_sha256,
            build_attempt_body=Path(args.package_build_attempt_body),
            expected_build_attempt_body_sha256=(
                args.expected_package_build_attempt_body_sha256
            ),
            build_attempt_committed=Path(args.package_build_attempt_committed),
            expected_build_attempt_committed_sha256=(
                args.expected_package_build_attempt_committed_sha256
            ),
        )
        resources.extend(package["held_by_relative"].values())
        resources.extend(package["held_directories"])
        if package["execution_file"] not in package["held_by_relative"].values():
            resources.append(package["execution_file"])
        python_sha = _normalized_sha(
            args.expected_python_executable_sha256, "expected Python SHA"
        )
        if package["runtime_dependency_closure"]["python"][
            "executable_sha256"
        ] != python_sha:
            raise PreflightError("package runtime/Python executable SHA binding mismatch")
        native_test_roles = tuple(args.native_test_role or ())
        if native_test_roles != REQUIRED_NATIVE_TEST_ROLES:
            raise PreflightError(
                "native test role set/order must be exact and non-empty"
            )
        declared_boot = str(args.expected_boot_id).strip().lower()
        candidate_dirs = _candidate_output_dirs(
            args.candidate_output_dir, package["root"], transaction.path
        )
        bindings = _expected_go_bindings(
            package=package,
            python_sha256=python_sha,
            hostname=args.expected_hostname,
            uid=int(args.expected_uid),
            boot_id=declared_boot,
            candidate_output_dirs=candidate_dirs,
            native_test_roles=native_test_roles,
            receipt_dir=transaction.path,
            receipt_transaction={},
        )
        for key in (
            "preflight_one_use_lease",
            "preflight_receipt_root",
            "preflight_terminal_commit",
        ):
            bindings.pop(key)
        return bindings
    finally:
        _close_verified_files(resources)


EXPECTED_AUTHORITIES = {
    "native_preflight_authorized": True,
    "reviewed_native_tests_authorized": True,
    "direct_data_materialization_authorized": False,
    "training_authorized": False,
    "common_test_access_authorized": False,
    "numerical_metric_access_authorized": False,
    "fresh_emx_authorized": False,
    "process_signal_authorized": False,
}


def _validate_code_go(
    path: Path, expected_sha: str, bindings: Mapping[str, Any]
) -> tuple[dict[str, Any], VerifiedFile]:
    go_file = _open_verified_file(
        path,
        "external CODE_GO receipt",
        expected_sha256=expected_sha,
        capture_snapshot=True,
    )
    try:
        go = _read_json_bytes(_verified_bytes(go_file), "external CODE_GO receipt")
        if set(go) != CODE_GO_TOP_LEVEL_KEYS:
            raise PreflightError("external CODE_GO receipt top-level keyset is not exact")
        try:
            _require_exact_json_equal(
                go["schema"], CODE_GO_SCHEMA, "external CODE_GO schema"
            )
            _require_exact_json_equal(go["status"], "PASS", "external CODE_GO status")
            _require_exact_json_equal(
                go["verdict"], "EXACT_CODE_GO", "external CODE_GO verdict"
            )
            _require_exact_json_equal(
                go["scope"], CODE_GO_SCOPE, "external CODE_GO scope"
            )
            _require_exact_json_equal(
                go["review"],
                {"independent": True, "result_blind": True},
                "external CODE_GO review",
            )
            _require_exact_json_equal(
                go["findings"],
                {"p0": 0, "p1": 0},
                "external CODE_GO findings",
            )
            _require_exact_json_equal(
                go["bindings"], bindings, "external CODE_GO bindings"
            )
            _require_exact_json_equal(
                go["authorities"],
                EXPECTED_AUTHORITIES,
                "external CODE_GO authorities",
            )
        except PreflightError as exc:
            raise PreflightError(
                f"external CODE_GO receipt does not exactly authorize this preflight: {exc}"
            ) from exc
        issued = _parse_go_utc(go["issued_utc"], "external CODE_GO issued_utc")
        expires = _parse_go_utc(go["expires_utc"], "external CODE_GO expires_utc")
        nonce = go["nonce"]
        if type(nonce) is not str or not GO_NONCE_PATTERN.fullmatch(nonce):
            raise PreflightError("external CODE_GO nonce must be exactly 32 lowercase hexadecimal chars")
        now = _now_utc()
        if issued > now:
            raise PreflightError("external CODE_GO is not active because issued_utc is in the future")
        if now >= expires:
            raise PreflightError("external CODE_GO is stale or expired")
        if expires <= issued or expires - issued > MAX_CODE_GO_LIFETIME:
            raise PreflightError("external CODE_GO lifetime must be positive and at most 24 hours")
        return go, go_file
    except BaseException:
        go_file.close()
        raise


def _host_identity(expected_hostname: str, expected_uid: int, expected_boot_id: str) -> dict[str, Any]:
    declared_boot = str(expected_boot_id).strip().lower()
    if not BOOT_ID_PATTERN.fullmatch(declared_boot):
        raise PreflightError("expected boot-id is malformed")
    actual = {
        "hostname": socket.gethostname(),
        "uid": os.getuid(),
        "boot_id": _boot_id(),
    }
    expected = {
        "hostname": str(expected_hostname),
        "uid": int(expected_uid),
        "boot_id": declared_boot,
    }
    if actual != expected:
        raise PreflightError(f"host/uid/boot-id mismatch: actual={actual} expected={expected}")
    return actual


def _require_runtime_identity(
    observed: Mapping[str, Any],
    *,
    expected_python_path: Path,
    expected_python_sha: str,
    expected_active_runtime: Mapping[str, Any],
    expected_compile_count: int,
    expected_code_role_sha256: Mapping[str, str],
) -> None:
    exact_keys = {
        "schema",
        "python_executable_path",
        "python_executable_sha256",
        "python_version",
        "numpy_version",
        "active_runtime",
        "startup_attestation",
        "terminal_attestation",
        "compiled_role_count",
        "consumed_code_role_sha256",
        "native_smoke_result_sha256",
        "native_smoke_attestation_sha256",
        "descriptor_closed",
        "raw_runtime_fallback_authorized",
    }
    if type(observed) is not dict or set(observed) != exact_keys:
        raise PreflightError("descriptor-sealed runtime identity keyset is not exact")
    expected_simple = {
        "schema": "controlled_real10k_20k_preflight_runtime_identity_v2",
        "python_executable_path": str(expected_python_path),
        "python_executable_sha256": expected_python_sha,
        "python_version": EXPECTED_PYTHON_VERSION,
        "numpy_version": EXPECTED_NUMPY_VERSION,
        "active_runtime": dict(expected_active_runtime),
        "compiled_role_count": expected_compile_count,
        "consumed_code_role_sha256": dict(expected_code_role_sha256),
        "descriptor_closed": True,
        "raw_runtime_fallback_authorized": False,
    }
    for key, expected in expected_simple.items():
        _require_exact_json_equal(
            observed[key], expected, f"descriptor-sealed runtime identity {key}"
        )
    for key in ("native_smoke_result_sha256", "native_smoke_attestation_sha256"):
        _normalized_sha(observed[key], f"descriptor-sealed runtime identity {key}")
    startup = observed["startup_attestation"]
    terminal = observed["terminal_attestation"]
    if (
        type(startup) is not dict
        or type(terminal) is not dict
        or startup.get("status") != "PASS_DESCRIPTOR_CLOSED_STARTUP"
        or terminal.get("status") != "PASS_DESCRIPTOR_CLOSED_TERMINAL"
        or type(terminal.get("exit_code")) is not int
        or terminal["exit_code"] != 0
    ):
        raise PreflightError("descriptor-sealed runtime terminal closure is not PASS-exact")
    for field, expected in expected_active_runtime.items():
        if startup.get(field) != expected or terminal.get(field) != expected:
            raise PreflightError(
                f"descriptor-sealed runtime attestation mismatch for {field}"
            )


def _load_snapshot() -> dict[str, float]:
    values = tuple(float(value) for value in os.getloadavg())
    if len(values) != 3 or any(not math.isfinite(value) or value < 0.0 for value in values):
        raise PreflightError("host load average is invalid")
    return {"load1": values[0], "load5": values[1], "load15": values[2]}


def _run_preflight_open(
    args: argparse.Namespace,
    receipt_dir: Path,
    transaction: _ReceiptTransaction,
    resources: list[VerifiedFile],
) -> dict[str, Any]:
    package = _audit_package(
        Path(args.package_dir),
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_index_sha256=args.expected_sha_index_sha256,
        expected_commit_sha256=args.expected_package_commit_sha256,
        build_attempt_body=Path(args.package_build_attempt_body),
        expected_build_attempt_body_sha256=(
            args.expected_package_build_attempt_body_sha256
        ),
        build_attempt_committed=Path(args.package_build_attempt_committed),
        expected_build_attempt_committed_sha256=(
            args.expected_package_build_attempt_committed_sha256
        ),
    )
    resources.extend(package["held_by_relative"].values())
    resources.extend(package["held_directories"])
    if package["execution_file"] not in package["held_by_relative"].values():
        resources.append(package["execution_file"])
    python_sha = _normalized_sha(args.expected_python_executable_sha256, "expected Python SHA")
    if package["runtime_dependency_closure"]["python"][
        "executable_sha256"
    ] != python_sha:
        raise PreflightError("package runtime/Python executable SHA binding mismatch")
    declared_boot = str(args.expected_boot_id).strip().lower()
    candidate_dirs = _candidate_output_dirs(
        args.candidate_output_dir, package["root"], receipt_dir
    )
    native_test_roles = tuple(args.native_test_role or ())
    if native_test_roles != REQUIRED_NATIVE_TEST_ROLES:
        raise PreflightError(
            "native test role set/order must be exact and non-empty: "
            f"expected={list(REQUIRED_NATIVE_TEST_ROLES)} observed={list(native_test_roles)}"
        )
    bindings = _expected_go_bindings(
        package=package,
        python_sha256=python_sha,
        hostname=args.expected_hostname,
        uid=int(args.expected_uid),
        boot_id=declared_boot,
        candidate_output_dirs=candidate_dirs,
        native_test_roles=native_test_roles,
        receipt_dir=receipt_dir,
        receipt_transaction=_receipt_transaction_go_binding(transaction),
    )
    prepared_base = dict(bindings)
    for key in (
        "preflight_one_use_lease",
        "preflight_receipt_root",
        "preflight_terminal_commit",
    ):
        prepared_base.pop(key)
    if transaction.prepared is None:
        raise PreflightError("held PREPARE receipt is unavailable")
    _require_exact_json_equal(
        transaction.prepared["execution_contract"],
        prepared_base,
        "PREPARE/EXECUTE contract",
    )
    go_path = _absolute_path(args.code_go_receipt, "--code-go-receipt", must_exist=True)
    code_go, go_file = _validate_code_go(
        go_path, args.expected_code_go_receipt_sha256, bindings
    )
    resources.append(go_file)
    consumed_lease = _consume_external_lease(transaction)

    host = _host_identity(args.expected_hostname, int(args.expected_uid), declared_boot)
    _assert_candidate_dirs_absent(candidate_dirs)
    python = _runtime_executable(args.python_executable, python_sha)
    resources.append(python)
    pre_processes = _scan_current_uid_processes(package, python.path)
    if pre_processes["match_count"] != 0:
        raise PreflightError(f"current UID has duplicate controlled processes: {pre_processes}")
    native_tests = _run_native_tests(
        python,
        package,
        native_test_roles,
        int(args.native_test_timeout_seconds),
    )
    runtime = _runtime_probe(python, package, native_tests)
    expected_active_runtime = {
        "schema": RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "native_smoke",
        "manifest_sha256": package["role_identity"][
            "runtime_dependency_closure_json"
        ]["sha256"],
        "pure_archive_sha256": package["runtime_dependency_closure"]["pure_archive"][
            "sha256"
        ],
        "bootstrap_sha256": package["role_identity"]["runtime_bootstrap_code"][
            "sha256"
        ],
    }
    _require_runtime_identity(
        runtime,
        expected_python_path=python.path,
        expected_python_sha=python_sha,
        expected_active_runtime=expected_active_runtime,
        expected_compile_count=sum(
            role in REQUIRED_CODE_ROLES or role in REQUIRED_NATIVE_TEST_ROLES
            for role in package["roles"]
        ),
        expected_code_role_sha256=package["code_role_sha256"],
    )
    _verify_package_continuity(package)
    _verify_file_continuity(go_file)
    _verify_file_continuity(python)
    _assert_candidate_dirs_absent(candidate_dirs)
    post_processes = _scan_current_uid_processes(package, python.path)
    if post_processes["match_count"] != 0:
        raise PreflightError(f"current UID controlled process appeared during preflight: {post_processes}")
    load = _load_snapshot()
    return {
        "package": {
            "root": str(package["root"]),
            "manifest_sha256": package["manifest_sha256"],
            "sha_index_sha256": package["index_sha256"],
            "receipt_sha256": package["receipt_sha256"],
            "independent_qa_required_sha256": package["qa_sha256"],
            "commit_sha256": package["commit_sha256"],
            "build_attempt_body_path": package["build_attempt_body_path"].as_posix(),
            "build_attempt_body_sha256": package["build_attempt_body_sha256"],
            "build_attempt_committed_path": package[
                "build_attempt_committed_path"
            ].as_posix(),
            "build_attempt_committed_sha256": package[
                "build_attempt_committed_sha256"
            ],
            "role_sha256": package["role_sha256"],
            "role_identity": package["role_identity"],
            "runtime_dependency_closure": package["runtime_dependency_closure"],
            "runtime_entrypoints": package["runtime_entrypoints"],
        },
        "external_code_go": {
            "path": str(go_path),
            "sha256": go_file.sha256,
            "scope": CODE_GO_SCOPE,
            "issued_utc": code_go["issued_utc"],
            "expires_utc": code_go["expires_utc"],
            "nonce": code_go["nonce"],
            "bound_preflight_receipt_dir": str(receipt_dir),
        },
        "receipt_transaction": {
            "prepared_binding": _receipt_transaction_go_binding(transaction),
            "consumed_external_one_use_lease": consumed_lease,
        },
        "host_identity": host,
        "runtime_identity": runtime,
        "process_singleton": {
            "contract": _verified_file_binding(package["singleton_contract_file"]),
            "contract_payload": package["singleton_contract"],
            "lock": _verified_file_binding(package["singleton_lock_file"]),
            "lock_operation": "LOCK_EX|LOCK_NB",
            "lock_held_for_full_execute_lifetime": True,
            "protected_entrypoints": package["singleton_contract"][
                "protected_entrypoints"
            ],
            "proc_audit_contract": package["singleton_contract"]["proc_audit"],
            "before": pre_processes,
            "after": post_processes,
            "all_counts_zero": True,
            "current_uid_only": True,
        },
        "candidate_output_dirs": [str(path) for path in candidate_dirs],
        "candidate_output_dirs_absent_before_and_after": True,
        "native_tests": native_tests,
        "host_load_snapshot": {**load, "gate_applied": False, "record_only": True},
        "checks": {
            "package_exact_regular_file_closure": True,
            "package_build_attempt_body_and_committed_terminal_exact": True,
            "external_code_go_exact": True,
            "external_code_go_fresh": True,
            "external_code_go_single_use_receipt_dir_bound": True,
            "frozen_source_identities_exact": True,
            "frozen_preregistration_identities_exact": True,
            "host_uid_boot_id_exact": True,
            "python_3_12_13_exact": True,
            "numpy_2_5_0_exact": True,
            "descriptor_sealed_numpy_and_runtime_exact": True,
            "native_compile_and_import_pass": True,
            "candidate_outputs_absent": True,
            "current_uid_exact_controlled_entrypoint_count_zero": True,
            "no_training_builder_runner_or_trainer_spawned": True,
            "no_process_signals_sent": True,
            "no_training_test_metrics_or_fresh_emx_access": True,
        },
    }


def run_preflight(
    args: argparse.Namespace,
    receipt_dir: Path,
    transaction: _ReceiptTransaction,
) -> dict[str, Any]:
    resources: list[VerifiedFile] = []
    try:
        details = _run_preflight_open(args, receipt_dir, transaction, resources)
    except BaseException:
        _close_verified_files(resources)
        raise
    transaction.execution_resources.extend(resources)
    return details


def _lease_abort_binding(transaction: _ReceiptTransaction) -> dict[str, Any] | None:
    if transaction.lease_fd is None:
        return None
    try:
        raw = _read_all(transaction.lease_fd)
        try:
            payload = _read_json_bytes(raw, "external lease during failure closure")
        except PreflightError:
            payload = None
        if isinstance(payload, dict) and payload.get("state") == "PREPARED":
            aborted = dict(payload)
            aborted["state"] = "REVOKED_"
            aborted["consumed_utc"] = _utc_z_now()
            replacement = _json_bytes(aborted)
            _write_all(transaction.lease_fd, replacement)
            os.ftruncate(transaction.lease_fd, len(replacement))
            raw = replacement
            payload = aborted
        os.fchmod(transaction.lease_fd, FILE_MODE)
        os.fsync(transaction.lease_fd)
        os.fsync(transaction.parent_fd)
        metadata = os.fstat(transaction.lease_fd)
        transaction.assert_lease_continuity()
        return {
            "path": str(transaction.lease_path),
            "sha256": _sha256_bytes(raw),
            "identity": _regular_file_identity(metadata),
            "schema": PREFLIGHT_LEASE_SCHEMA,
            "state": payload.get("state") if isinstance(payload, dict) else "UNPARSEABLE",
        }
    except BaseException:
        return None


def _freeze_receipt_root(transaction: _ReceiptTransaction) -> None:
    os.fchmod(transaction.root_fd, 0o700)
    for name in sorted(os.listdir(transaction.root_fd)):
        metadata = os.stat(name, dir_fd=transaction.root_fd, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PreflightError(f"terminal receipt contains non-regular entry: {name}")
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=transaction.root_fd,
        )
        try:
            os.fchmod(descriptor, FILE_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    os.fsync(transaction.root_fd)
    os.fchmod(transaction.root_fd, DIRECTORY_MODE)
    os.fsync(transaction.root_fd)
    os.fsync(transaction.parent_fd)


def _terminalize_failure(
    transaction: _ReceiptTransaction,
    exc: BaseException,
    *,
    phase: str,
    started_utc: str,
) -> Path:
    os.fchmod(transaction.root_fd, 0o700)
    existing = set(os.listdir(transaction.root_fd))
    if PREFLIGHT_FATAL_FAIL_NAME in existing:
        _freeze_receipt_root(transaction)
        return transaction.path / PREFLIGHT_FATAL_FAIL_NAME
    lease_binding = _lease_abort_binding(transaction)
    failure = {
        "schema": PREFLIGHT_FAILURE_SCHEMA,
        "status": "FAIL_NO_GO",
        "phase": phase,
        "started_utc": started_utc,
        "failed_utc": _utc_z_now(),
        "reason": f"{type(exc).__name__}: {exc}",
        "receipt_root": {
            "path": str(transaction.path),
            "prepared_identity": transaction.root_identity,
            "terminal_identity": {
                **transaction.root_identity,
                "mode_octal": f"{DIRECTORY_MODE:04o}",
            },
        },
        "receipt_parent": {
            "path": str(transaction.parent_path),
            "identity": transaction.parent_identity,
        },
        "external_one_use_lease": lease_binding,
        "commit_marker_present": PREFLIGHT_COMMITTED_NAME in existing,
        "failure_precedence_absolute": True,
        "preserved_entries_before_failure": sorted(existing),
        "retry_authorized": False,
        "preflight_pass": False,
        "authorities": _receipt_authorities(),
        "next_legal_action": "PRESERVE_FAILURE_AND_REQUIRE_NEW_NO_CLOBBER_PREPARE",
    }
    _write_file_at(
        transaction.root_fd, PREFLIGHT_FATAL_FAIL_NAME, _json_bytes(failure)
    )
    indexed: list[dict[str, Any]] = []
    for name in sorted(os.listdir(transaction.root_fd)):
        if name == FAILURE_SHA_INDEX_NAME:
            continue
        _raw, record = _read_file_at(
            transaction.root_fd, name, f"failure closure artifact {name}", expected_mode=None
        )
        indexed.append(record)
    _write_file_at(
        transaction.root_fd,
        FAILURE_SHA_INDEX_NAME,
        _prepare_index_bytes(indexed),
    )
    os.fsync(transaction.root_fd)
    os.fsync(transaction.parent_fd)
    _freeze_receipt_root(transaction)
    return transaction.path / PREFLIGHT_FATAL_FAIL_NAME


def _publish_committed_success(
    transaction: _ReceiptTransaction,
    details: Mapping[str, Any],
    *,
    started_utc: str,
) -> Path:
    if transaction.lease_consumed_binding is None:
        raise PreflightError("success publication requires a consumed external lease")
    transaction.assert_directory_continuity()
    transaction.assert_lease_continuity()
    body = {
        "schema": PREFLIGHT_SCHEMA,
        "status": "PASS_BODY_AWAITING_DURABLE_COMMIT",
        "started_utc": started_utc,
        "body_generated_utc": _utc_z_now(),
        **dict(details),
        "preflight_pass": False,
        "committed_terminal_marker_required": PREFLIGHT_COMMITTED_NAME,
        "authorities": _receipt_authorities(),
        "next_legal_action": "NO_ACTION_UNTIL_DURABLE_COMMITTED_MARKER_IS_VERIFIED",
    }
    body_record = _write_file_at(
        transaction.root_fd, PREFLIGHT_BODY_NAME, _json_bytes(body)
    )
    if transaction.prepared_bindings is None:
        raise PreflightError("prepared artifact bindings are unavailable at commit")
    indexed_records = [
        transaction.prepared_bindings["prepared_receipt"],
        transaction.prepared_bindings["execution_qa_required"],
        transaction.prepared_bindings["prepare_sha256sums"],
        body_record,
    ]
    success_index_record = _write_file_at(
        transaction.root_fd,
        PREFLIGHT_SHA_INDEX_NAME,
        _prepare_index_bytes(indexed_records),
    )
    terminal_root_identity = {
        **transaction.root_identity,
        "mode_octal": f"{DIRECTORY_MODE:04o}",
    }
    committed = {
        "schema": PREFLIGHT_COMMITTED_SCHEMA,
        "status": "COMMITTED_PASS_PREFLIGHT_ONLY",
        "committed_utc": _utc_z_now(),
        "preflight_pass": True,
        "receipt_root": {
            "path": str(transaction.path),
            "prepared_identity": transaction.root_identity,
            "committed_identity": terminal_root_identity,
        },
        "receipt_parent": {
            "path": str(transaction.parent_path),
            "identity": transaction.parent_identity,
        },
        "prepared_artifacts": transaction.prepared_bindings,
        "receipt_body": body_record,
        "sha256_index": success_index_record,
        "external_code_go": {
            "path": details["external_code_go"]["path"],
            "sha256": details["external_code_go"]["sha256"],
            "schema": CODE_GO_SCHEMA,
            "scope": CODE_GO_SCOPE,
        },
        "consumed_external_one_use_lease": transaction.lease_consumed_binding,
        "process_singleton": details["process_singleton"],
        "exact_root_filenames": list(SUCCESS_ROOT_FILES),
        "failure_marker_absent_at_commit": True,
        "failure_marker_has_absolute_precedence": True,
        "body_is_not_authority": True,
        "authorities": _receipt_authorities(),
        "next_legal_action": (
            "SEPARATE_RESULT_BLIND_MATERIALIZATION_RECEIPT_AND_EXACT_AUTHORIZATION_REQUIRED"
        ),
    }
    _write_file_at(
        transaction.root_fd,
        PREFLIGHT_PENDING_COMMIT_NAME,
        _json_bytes(committed),
    )
    os.fsync(transaction.root_fd)
    os.rename(
        PREFLIGHT_PENDING_COMMIT_NAME,
        PREFLIGHT_COMMITTED_NAME,
        src_dir_fd=transaction.root_fd,
        dst_dir_fd=transaction.root_fd,
    )
    os.fsync(transaction.root_fd)
    os.fsync(transaction.parent_fd)
    _freeze_receipt_root(transaction)
    observed_names = set(os.listdir(transaction.root_fd))
    if observed_names != set(SUCCESS_ROOT_FILES):
        raise PreflightError(
            "committed receipt exact closure mismatch: "
            f"missing={sorted(set(SUCCESS_ROOT_FILES)-observed_names)} "
            f"extra={sorted(observed_names-set(SUCCESS_ROOT_FILES))}"
        )
    current_root = _directory_identity(os.fstat(transaction.root_fd))
    _require_exact_json_equal(
        current_root, terminal_root_identity, "committed receipt root identity"
    )
    transaction.assert_lease_continuity()
    return transaction.path / PREFLIGHT_COMMITTED_NAME


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("PREPARE", "EXECUTE"), required=True)
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-sha-index-sha256", required=True)
    parser.add_argument("--expected-package-commit-sha256", required=True)
    parser.add_argument("--package-build-attempt-body", required=True)
    parser.add_argument(
        "--expected-package-build-attempt-body-sha256", required=True
    )
    parser.add_argument("--package-build-attempt-committed", required=True)
    parser.add_argument(
        "--expected-package-build-attempt-committed-sha256", required=True
    )
    parser.add_argument("--code-go-receipt")
    parser.add_argument("--expected-code-go-receipt-sha256")
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--expected-python-executable-sha256", required=True)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--expected-uid", type=int, required=True)
    parser.add_argument("--expected-boot-id", required=True)
    parser.add_argument("--candidate-output-dir", action="append", required=True)
    parser.add_argument("--native-test-role", action="append", default=[])
    parser.add_argument("--native-test-timeout-seconds", type=int, default=60)
    parser.add_argument("--receipt-dir", required=True)
    args = parser.parse_args(argv)
    if not 1 <= int(args.native_test_timeout_seconds) <= 60:
        parser.error("--native-test-timeout-seconds must be in [1, 60]")
    code_go_values = (
        args.code_go_receipt,
        args.expected_code_go_receipt_sha256,
    )
    if args.phase == "PREPARE" and any(value is not None for value in code_go_values):
        parser.error("PREPARE forbids external CODE_GO arguments")
    if args.phase == "EXECUTE" and any(value in {None, ""} for value in code_go_values):
        parser.error("EXECUTE requires both external CODE_GO arguments")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = _utc_z_now()
    transaction: _ReceiptTransaction | None = None
    if args.phase == "PREPARE":
        try:
            transaction = _create_receipt_transaction(args.receipt_dir)
            prepared = _prepare_phase(transaction, args)
        except BaseException as exc:
            if transaction is not None:
                try:
                    receipt = _terminalize_failure(
                        transaction, exc, phase="PREPARE", started_utc=started
                    )
                    print(f"status=FAIL_NO_GO\nreceipt={receipt}", file=sys.stderr)
                finally:
                    transaction.close()
            else:
                print(f"status=FAIL_NO_GO\nreason={type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        transaction.close()
        print("status=PREPARED_AWAITING_INDEPENDENT_EXACT_CODE_GO")
        print(f"receipt_dir={prepared['receipt_dir']}")
        print(f"prepared_sha256={_sha256(prepared['prepared'])}")
        print(f"execution_qa_required_sha256={_sha256(prepared['execution_qa_required'])}")
        print(f"prepare_sha256sums_sha256={_sha256(prepared['prepare_sha_index'])}")
        print(f"external_lease_sha256={_sha256(prepared['lease'])}")
        return 0

    try:
        transaction = _open_execution_transaction(args.receipt_dir)
    except FileExistsError as exc:
        print(f"status=FAIL_NO_GO\nreason={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except _ReceiptTransactionValidationError as wrapped:
        transaction = wrapped.transaction
        try:
            receipt = _terminalize_failure(
                transaction, wrapped.cause, phase="EXECUTE_OPEN", started_utc=started
            )
            print(f"status=FAIL_NO_GO\nreceipt={receipt}", file=sys.stderr)
        finally:
            transaction.close()
        return 2
    except BaseException as exc:
        print(f"status=FAIL_NO_GO\nreason={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    try:
        details = run_preflight(args, transaction.path, transaction)
        committed = _publish_committed_success(
            transaction, details, started_utc=started
        )
    except BaseException as exc:
        receipt = _terminalize_failure(
            transaction, exc, phase="EXECUTE", started_utc=started
        )
        print(f"status=FAIL_NO_GO\nreceipt={receipt}", file=sys.stderr)
        return 2
    finally:
        transaction.close()
    print(f"status=COMMITTED_PASS_PREFLIGHT_ONLY\nreceipt={committed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
