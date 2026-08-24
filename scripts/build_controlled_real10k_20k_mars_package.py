#!/usr/bin/env python3
"""Build one immutable, result-blind package-v5 MARS handoff.

The builder accepts one hash-pinned JSON build specification. Semantic roles
have hard-coded destinations; callers cannot choose package paths. Every
source is consumed through a no-follow descriptor, and the output root stays
open by descriptor from create-once reservation through the final audit.

This program grants no execution, training, evaluation, metric, EMX, or signal
authority. ``PACKAGE_COMMIT.json`` is the last package member, but downstream
consumers must also require the external PASS build-attempt receipt.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import platform
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


PACKAGE_VERSION = "v5"
SCHEMA = "controlled_real10k_20k_mars_package_v2"
PACKAGE_RECEIPT_SCHEMA = "controlled_real10k_20k_mars_package_receipt_v2"
QA_REQUIRED_SCHEMA = "controlled_real10k_20k_mars_package_independent_qa_required_v3"
BUILD_SPEC_SCHEMA = "controlled_real10k_20k_mars_package_build_spec_v1"
BUILD_ATTEMPT_BODY_SCHEMA = "controlled_real10k_20k_mars_package_build_attempt_body_v3"
BUILD_ATTEMPT_COMMIT_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
BUILD_ATTEMPT_FAILURE_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_failed_v1"
)
BUILD_ATTEMPT_AMBIGUOUS_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_ambiguous_no_go_v1"
)
# Transitional source-level alias.  Consumers must bind BODY + COMMITTED, never
# accept this body schema by itself as terminal authority.
BUILD_ATTEMPT_SCHEMA = BUILD_ATTEMPT_BODY_SCHEMA
PACKAGE_COMMIT_SCHEMA = "controlled_real10k_20k_mars_package_commit_v2"
RUNTIME_CLOSURE_SCHEMA = "controlled_real10k_20k_runtime_closure_v1"
PROCESS_SINGLETON_CONTRACT_SCHEMA = "controlled_real10k_20k_process_singleton_contract_v1"

ROLE_DESTINATIONS: dict[str, str] = {
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
REQUIRED_ROLES = frozenset(ROLE_DESTINATIONS)
TREE_ROLE = "runtime_dependency_closure_tree"
FILE_ROLES = frozenset(REQUIRED_ROLES - {TREE_ROLE})
PYTHON_CODE_ROLES = frozenset(
    {
        "package_builder_code",
        "runtime_bootstrap_code",
        "preflight_code",
        "materialization_builder_code",
        "materialization_gate_code",
        "runner_code",
        "trainer_code",
        "evaluator_code",
        "runtime_package_init_code",
        "shared_contract_code",
        "splitter_code",
        "native_smoke_test",
    }
)

MANIFEST_NAME = "MANIFEST.json"
RECEIPT_NAME = "RECEIPT.json"
SHA_INDEX_NAME = "SHA256SUMS.txt"
QA_REQUIRED_NAME = "INDEPENDENT_QA_REQUIRED.json"
COMMIT_NAME = "PACKAGE_COMMIT.json"
SINGLETON_LOCK_NAME = "CONTROLLED_SINGLETON.lock"
BUILD_ATTEMPT_RECEIPT_NAME = "PACKAGE_BUILD_ATTEMPT_RECEIPT.json"
BUILD_ATTEMPT_COMMITTED_NAME = "PACKAGE_BUILD_ATTEMPT_COMMITTED.json"
BUILD_ATTEMPT_FAILED_NAME = "PACKAGE_BUILD_ATTEMPT_FAILED.json"
BUILD_ATTEMPT_AMBIGUOUS_NAME = "PACKAGE_BUILD_ATTEMPT_AMBIGUOUS_NO_GO.json"
FILE_MODE = 0o444
DIRECTORY_MODE = 0o555
WORKING_DIRECTORY_MODE = 0o755

ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_BASENAMES = frozenset({"sitecustomize.py", "usercustomize.py"})
FORBIDDEN_SUFFIXES = frozenset({".pyc", ".pyo", ".pth"})
TRUSTED_SYSTEM_LIBRARY_ALLOWLIST = (
    "ld-linux-x86-64.so.2",
    "libc.so.6",
    "libdl.so.2",
    "libgcc_s.so.1",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
    "libstdc++.so.6",
)

# This exact declaration closes QA4-IF001. The package-v5 preflight validator
# must use the same keyset.
REQUIRED_GO_BINDING_KEYS = (
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

PACKAGE_AUTHORITIES = {
    "native_linux_test_execution": False,
    "data_materialization": False,
    "training": False,
    "common_test_access": False,
    "numerical_metric_access": False,
    "fresh_emx": False,
    "process_signal": False,
}

PROCESS_PROTECTED_ROLES = (
    "evaluator_code",
    "materialization_builder_code",
    "materialization_gate_code",
    "native_smoke_test",
    "preflight_code",
    "runner_code",
    "runtime_bootstrap_code",
    "trainer_code",
)
PROCESS_CONTROLLER_ROLES = frozenset(
    {"evaluator_code", "materialization_gate_code", "preflight_code", "runner_code"}
)


def _expected_process_singleton_contract() -> dict[str, Any]:
    execution_identities: dict[str, tuple[str, str | None]] = {
        "evaluator_code": ("sealed_runtime_entrypoint", "evaluator"),
        "materialization_builder_code": ("sealed_in_process_member", "materialization"),
        "materialization_gate_code": ("sealed_runtime_entrypoint", "materialization"),
        "native_smoke_test": ("sealed_runtime_entrypoint", "native_smoke"),
        "preflight_code": ("raw_hash_bound_script", None),
        "runner_code": ("sealed_runtime_entrypoint", "runner"),
        "runtime_bootstrap_code": ("sealed_bootstrap_fd", None),
        "trainer_code": ("sealed_runtime_entrypoint", "trainer"),
    }
    return {
        "schema": PROCESS_SINGLETON_CONTRACT_SCHEMA,
        "lock": {
            "relative_path": SINGLETON_LOCK_NAME,
            "basename": SINGLETON_LOCK_NAME,
            "sha256": _sha256_bytes(b""),
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
                "path": ROLE_DESTINATIONS[role],
                "controller": role in PROCESS_CONTROLLER_ROLES,
                "execution_identity": execution_identities[role][0],
                "runtime_entrypoint": execution_identities[role][1],
            }
            for role in PROCESS_PROTECTED_ROLES
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

RUNTIME_ENTRYPOINTS = {
    "preflight": ROLE_DESTINATIONS["preflight_code"],
    "materialization": ROLE_DESTINATIONS["materialization_gate_code"],
    "runner": ROLE_DESTINATIONS["runner_code"],
    "trainer": ROLE_DESTINATIONS["trainer_code"],
    "evaluator": ROLE_DESTINATIONS["evaluator_code"],
    "native_smoke": ROLE_DESTINATIONS["native_smoke_test"],
}

IMPORT_GRAPH = {
    "materialization_builder_code": [
        "runtime_dependency_closure_tree",
        "shared_contract_code",
        "splitter_code",
    ],
    "materialization_gate_code": [
        "materialization_builder_code",
        "runtime_dependency_closure_tree",
        "shared_contract_code",
        "splitter_code",
    ],
    "runner_code": ["runtime_dependency_closure_tree", "shared_contract_code"],
    "trainer_code": ["runtime_dependency_closure_tree", "splitter_code"],
    "evaluator_code": [
        "runtime_dependency_closure_tree",
        "runtime_package_init_code",
        "shared_contract_code",
        "trainer_code",
    ],
    "splitter_code": ["runtime_dependency_closure_tree"],
}


class PackageError(RuntimeError):
    """The explicit package identity or immutable closure is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _canonical_json_sha(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _strict_json_loads(payload: bytes, label: str) -> Any:
    """Parse one UTF-8 JSON value without aliases hidden by Python equality.

    Duplicate object names and the non-standard NaN/Infinity spellings make a
    purportedly immutable contract parser-dependent.  Reject them at the
    boundary so every later exact-key/type check sees one unambiguous value.
    """

    def reject_constant(token: str) -> Any:
        raise ValueError(f"non-finite JSON constant is forbidden: {token}")

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON object name is forbidden: {key}")
            value[key] = item
        return value

    try:
        text = payload.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PackageError(f"{label} JSON is invalid: {error}") from error


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA_PATTERN.fullmatch(value) is not None


def _normalized_sha(value: Any, label: str) -> str:
    if not _is_sha256(value):
        raise PackageError(f"{label} is not an exact lowercase SHA-256")
    return str(value)


def _safe_relative(raw: Any, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise PackageError(f"{label} is not a safe relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PackageError(f"{label} contains path traversal")
    return path


def _forbidden_runtime_path(path: PurePosixPath) -> bool:
    lowered = tuple(part.lower() for part in path.parts)
    return (
        "__pycache__" in lowered
        or path.name.lower() in FORBIDDEN_BASENAMES
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
    )


def _absolute_path(raw: Any, label: str, *, must_exist: bool) -> Path:
    if not isinstance(raw, str) or "\x00" in raw:
        raise PackageError(f"{label} must be an absolute string path")
    path = Path(raw).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise PackageError(f"{label} must be absolute and traversal-free: {raw!r}")
    if must_exist and not path.exists():
        raise PackageError(f"{label} is missing: {path}")
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
            raise PackageError(f"{label} traverses a symlink: {current}")


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


def _read_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        offset += len(block)


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


@dataclass
class HeldFile:
    path: Path
    descriptor: int
    metadata: os.stat_result
    sha256: str
    label: str

    @classmethod
    def open(
        cls, raw: Any, label: str, *, expected_sha256: str | None = None
    ) -> "HeldFile":
        path = _absolute_path(raw, label, must_exist=True)
        _reject_symlink_chain(path, label)
        if path.resolve(strict=True) != path:
            raise PackageError(f"{label} is not a canonical path: {path}")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise PackageError(f"{label} must be a regular nlink=1 file")
            digest = _sha256_fd(descriptor)
            after = os.fstat(descriptor)
            if _stat_identity(before) != _stat_identity(after):
                raise PackageError(f"{label} changed while being hashed")
            if _stat_identity(path.lstat()) != _stat_identity(before):
                raise PackageError(f"{label} pathname and held inode differ")
            if expected_sha256 is not None and digest != _normalized_sha(
                expected_sha256, f"{label} expected SHA-256"
            ):
                raise PackageError(f"{label} SHA-256 mismatch")
            return cls(path, descriptor, before, digest, label)
        except BaseException:
            os.close(descriptor)
            raise

    @property
    def size_bytes(self) -> int:
        return int(self.metadata.st_size)

    def bytes(self) -> bytes:
        self.assert_continuity()
        payload = _read_fd(self.descriptor)
        if len(payload) != self.size_bytes or _sha256_bytes(payload) != self.sha256:
            raise PackageError(f"{self.label} held bytes changed")
        return payload

    def assert_continuity(self) -> None:
        current = os.fstat(self.descriptor)
        if _stat_identity(current) != _stat_identity(self.metadata):
            raise PackageError(f"{self.label} held inode changed")
        _reject_symlink_chain(self.path, self.label)
        if _stat_identity(self.path.lstat()) != _stat_identity(self.metadata):
            raise PackageError(f"{self.label} pathname identity changed")

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


@dataclass
class HeldDirectory:
    path: Path
    descriptor: int
    metadata: os.stat_result
    label: str

    @classmethod
    def open(cls, raw: Any, label: str) -> "HeldDirectory":
        path = _absolute_path(raw, label, must_exist=True)
        _reject_symlink_chain(path, label)
        if path.resolve(strict=True) != path:
            raise PackageError(f"{label} is not a canonical path")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISDIR(before.st_mode) or _stat_identity(path.lstat()) != _stat_identity(
            before
        ):
            os.close(descriptor)
            raise PackageError(f"{label} pathname and held directory differ")
        return cls(path, descriptor, before, label)

    def assert_continuity(self) -> None:
        current = os.fstat(self.descriptor)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (self.metadata.st_dev, self.metadata.st_ino)
        ):
            raise PackageError(f"{self.label} held directory changed")
        _reject_symlink_chain(self.path, self.label)
        pathname = self.path.lstat()
        if (
            not stat.S_ISDIR(pathname.st_mode)
            or (pathname.st_dev, pathname.st_ino)
            != (self.metadata.st_dev, self.metadata.st_ino)
        ):
            raise PackageError(f"{self.label} pathname identity changed")

    def assert_unchanged(self) -> None:
        self.assert_continuity()
        if _stat_identity(os.fstat(self.descriptor)) != _stat_identity(self.metadata):
            raise PackageError(f"{self.label} metadata changed")

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def _open_child_directory(parent_descriptor: int, name: str) -> int:
    return os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )


def _mkdirs_at(root_descriptor: int, relative: PurePosixPath) -> None:
    descriptor = os.dup(root_descriptor)
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, WORKING_DIRECTORY_MODE, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileExistsError:
                pass
            child = _open_child_directory(descriptor, part)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise PackageError(f"output component is not a directory: {relative}")
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _parent_descriptor_at(root_descriptor: int, relative: PurePosixPath) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in relative.parent.parts:
            child = _open_child_directory(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_file_at(
    root_descriptor: int,
    relative: PurePosixPath,
    payload: bytes,
    *,
    mode: int = FILE_MODE,
) -> dict[str, Any]:
    _mkdirs_at(root_descriptor, relative.parent)
    parent_descriptor = _parent_descriptor_at(root_descriptor, relative)
    descriptor = -1
    try:
        descriptor = os.open(
            relative.name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while freezing package member")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size != len(payload)
        ):
            raise PackageError(f"output member identity mismatch: {relative}")
        if _sha256_fd(descriptor) != _sha256_bytes(payload):
            raise PackageError(f"output member content mismatch: {relative}")
        os.fsync(parent_descriptor)
        return {
            "path": relative.as_posix(),
            "sha256": _sha256_bytes(payload),
            "size_bytes": len(payload),
            "mode_octal": "0444",
            "nlink": 1,
        }
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _reserve_directory(path: Path, label: str) -> tuple[HeldDirectory, HeldDirectory]:
    path = _absolute_path(str(path), label, must_exist=False)
    _reject_symlink_chain(path.parent, f"{label} parent")
    if path.parent.resolve(strict=True) != path.parent:
        raise PackageError(f"{label} parent is not canonical")
    parent = HeldDirectory.open(str(path.parent), f"{label} parent")
    try:
        try:
            os.mkdir(path.name, WORKING_DIRECTORY_MODE, dir_fd=parent.descriptor)
        except FileExistsError as error:
            raise FileExistsError(f"{label} exists; no-clobber: {path}") from error
        os.fsync(parent.descriptor)
        child_descriptor = _open_child_directory(parent.descriptor, path.name)
        metadata = os.fstat(child_descriptor)
        child = HeldDirectory(path, child_descriptor, metadata, label)
        child.assert_continuity()
        return parent, child
    except BaseException:
        parent.close()
        raise


def _walk_descriptor(
    descriptor: int,
    prefix: PurePosixPath = PurePosixPath(),
) -> tuple[list[tuple[PurePosixPath, os.stat_result, int]], list[PurePosixPath]]:
    files: list[tuple[PurePosixPath, os.stat_result, int]] = []
    directories: list[PurePosixPath] = []
    for name in sorted(os.listdir(descriptor)):
        if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
            raise PackageError("unsafe directory entry")
        relative = prefix / name
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise PackageError(f"symlink rejected: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(relative)
            child = _open_child_directory(descriptor, name)
            try:
                child_files, child_directories = _walk_descriptor(child, relative)
                files.extend(child_files)
                directories.extend(child_directories)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise PackageError(f"hard-linked file rejected: {relative}")
            file_descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            after = os.fstat(file_descriptor)
            if _stat_identity(after) != _stat_identity(metadata):
                os.close(file_descriptor)
                raise PackageError(f"directory entry changed while opening: {relative}")
            files.append((relative, metadata, file_descriptor))
        else:
            raise PackageError(f"special file rejected: {relative}")
    return files, directories


def _close_walk_files(files: Iterable[tuple[PurePosixPath, os.stat_result, int]]) -> None:
    for _, _, descriptor in files:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _assert_source_tree_continuity(
    tree_root: HeldDirectory,
    original_files: Sequence[tuple[PurePosixPath, os.stat_result, int]],
    frozen_records: Sequence[Mapping[str, Any]],
) -> None:
    tree_root.assert_unchanged()
    current_files, current_directories = _walk_descriptor(tree_root.descriptor)
    try:
        original = {item[0].as_posix(): item for item in original_files}
        current = {item[0].as_posix(): item for item in current_files}
        if set(original) != set(current):
            raise PackageError("runtime dependency source tree file set changed")
        expected_directories: set[str] = set()
        for record in frozen_records:
            parent = PurePosixPath(str(record["path"])).parent
            while parent.parts:
                expected_directories.add(parent.as_posix())
                parent = parent.parent
        if {item.as_posix() for item in current_directories} != expected_directories:
            raise PackageError("runtime dependency source tree directory set changed")
        for path in sorted(original):
            _, original_metadata, original_descriptor = original[path]
            _, current_metadata, current_descriptor = current[path]
            if (
                _stat_identity(os.fstat(original_descriptor))
                != _stat_identity(original_metadata)
                or _stat_identity(current_metadata) != _stat_identity(original_metadata)
                or _stat_identity(os.fstat(current_descriptor))
                != _stat_identity(original_metadata)
                or _sha256_fd(current_descriptor) != _sha256_fd(original_descriptor)
            ):
                raise PackageError(f"runtime dependency source identity changed: {path}")
    finally:
        _close_walk_files(current_files)


def _freeze_directories(root_descriptor: int) -> None:
    files, directories = _walk_descriptor(root_descriptor)
    _close_walk_files(files)
    for relative in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        descriptor = _parent_descriptor_at(root_descriptor, relative)
        try:
            os.chmod(relative.name, DIRECTORY_MODE, dir_fd=descriptor, follow_symlinks=False)
            child = _open_child_directory(descriptor, relative.name)
            try:
                os.fsync(child)
            finally:
                os.close(child)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    os.fchmod(root_descriptor, DIRECTORY_MODE)
    os.fsync(root_descriptor)


def _audit_frozen_tree(root_descriptor: int) -> dict[str, dict[str, Any]]:
    files, directories = _walk_descriptor(root_descriptor)
    try:
        observed: dict[str, dict[str, Any]] = {}
        for relative, metadata, descriptor in files:
            if stat.S_IMODE(metadata.st_mode) != FILE_MODE or metadata.st_nlink != 1:
                raise PackageError(f"frozen file mode/link mismatch: {relative}")
            observed[relative.as_posix()] = {
                "sha256": _sha256_fd(descriptor),
                "size_bytes": int(metadata.st_size),
                "mode_octal": "0444",
                "nlink": 1,
            }
        for relative in directories:
            parent = _parent_descriptor_at(root_descriptor, relative)
            try:
                metadata = os.stat(
                    relative.name, dir_fd=parent, follow_symlinks=False
                )
            finally:
                os.close(parent)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE:
                raise PackageError(f"frozen directory mode mismatch: {relative}")
        if stat.S_IMODE(os.fstat(root_descriptor).st_mode) != DIRECTORY_MODE:
            raise PackageError("frozen package root mode mismatch")
        return observed
    finally:
        _close_walk_files(files)


def _exact_keys(value: Any, keys: Iterable[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PackageError(f"{label} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        raise PackageError(
            f"{label} keyset mismatch: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _exact_string_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
        or value != sorted(value)
    ):
        raise PackageError(f"{label} must be a sorted unique string list")
    return value


def _validate_process_singleton_contract(payload: bytes) -> Mapping[str, Any]:
    try:
        observed = _strict_json_loads(payload, "process singleton contract")
        observed_canonical = json.dumps(
            observed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (ValueError, TypeError) as error:
        raise PackageError(f"process singleton contract JSON is invalid: {error}") from error
    expected = _expected_process_singleton_contract()
    expected_canonical = json.dumps(
        expected,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if observed_canonical != expected_canonical:
        raise PackageError(
            "process singleton contract does not exactly match the frozen v1 contract"
        )
    return observed


def _validate_zip_exact(
    payload: bytes, members: Sequence[Mapping[str, Any]]
) -> None:
    if payload[-22:-18] != b"PK\x05\x06":
        raise PackageError("runtime pure ZIP has a comment, trailing bytes, or no exact EOCD")
    expected_buffer = io.BytesIO()
    observed_payloads: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            if archive.comment != b"":
                raise PackageError("runtime pure ZIP archive comment must be empty")
            infos = archive.infolist()
            expected_names = [str(item["path"]) for item in members]
            if [item.filename for item in infos] != expected_names:
                raise PackageError("runtime pure ZIP member order/set mismatch")
            for info, member in zip(infos, members):
                name = info.filename
                if not name.isascii() or name.endswith("/"):
                    raise PackageError("runtime pure ZIP paths must be ASCII regular files")
                if (
                    info.date_time != (1980, 1, 1, 0, 0, 0)
                    or info.create_system != 3
                    or info.create_version != 20
                    or info.extract_version != 20
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.external_attr != ((stat.S_IFREG | 0o444) << 16)
                    or info.internal_attr != 0
                    or info.flag_bits != 0
                    or info.extra != b""
                    or info.comment != b""
                ):
                    raise PackageError(f"runtime pure ZIP metadata mismatch: {name}")
                body = archive.read(info)
                if len(body) != member["size_bytes"] or _sha256_bytes(body) != member["sha256"]:
                    raise PackageError(f"runtime pure ZIP member identity mismatch: {name}")
                observed_payloads[name] = body
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise PackageError(f"runtime pure ZIP is invalid: {error}") from error

    with zipfile.ZipFile(expected_buffer, "w") as expected_archive:
        expected_archive.comment = b""
        for member in members:
            info = zipfile.ZipInfo(str(member["path"]), (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            expected_archive.writestr(info, observed_payloads[str(member["path"])])
    if expected_buffer.getvalue() != payload:
        raise PackageError("runtime pure ZIP is not the exact deterministic encoding")


def _validate_member_record(value: Any, index: int) -> Mapping[str, Any]:
    record = _exact_keys(
        value,
        {"path", "sha256", "size_bytes", "kind", "module", "is_package", "role"},
        f"runtime closure members[{index}]",
    )
    path = _safe_relative(record["path"], f"runtime closure members[{index}].path")
    if not path.as_posix().isascii() or _forbidden_runtime_path(path):
        raise PackageError(f"runtime closure member path is forbidden: {path}")
    _normalized_sha(record["sha256"], f"runtime closure members[{index}].sha256")
    if type(record["size_bytes"]) is not int or record["size_bytes"] < 0:
        raise PackageError("runtime closure member size must be a nonnegative integer")
    if record["kind"] not in {"python_source", "data"}:
        raise PackageError("runtime closure member kind is invalid")
    if record["module"] is not None and (
        not isinstance(record["module"], str) or not record["module"]
    ):
        raise PackageError("runtime closure member module is invalid")
    if type(record["is_package"]) is not bool:
        raise PackageError("runtime closure member is_package must be boolean")
    if record["role"] not in {
        "package_init_code",
        "runtime_bootstrap_code",
        "shared_contract_code",
        "splitter_code",
        "materialization_builder_code",
        "materialization_gate_code",
        "runner_code",
        "trainer_code",
        "evaluator_code",
        "native_smoke_test",
        "numpy_pure",
    }:
        raise PackageError("runtime closure member role is invalid")
    return record


def _validate_runtime_closure(
    closure_payload: bytes,
    held_roles: Mapping[str, HeldFile],
    tree_files: Mapping[str, tuple[os.stat_result, int]],
) -> tuple[Mapping[str, Any], list[dict[str, Any]], str]:
    closure = _strict_json_loads(closure_payload, "runtime closure")
    closure = _exact_keys(
        closure,
        {
            "schema",
            "bootstrap",
            "python",
            "numpy",
            "pure_archive",
            "members",
            "native_extensions",
            "native_libraries",
            "system_library_allowlist",
            "entrypoints",
        },
        "runtime closure",
    )
    if closure["schema"] != RUNTIME_CLOSURE_SCHEMA:
        raise PackageError("runtime closure schema mismatch")

    bootstrap = _exact_keys(
        closure["bootstrap"], {"module", "sha256", "size_bytes"}, "runtime closure bootstrap"
    )
    if bootstrap["module"] != (
        "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap"
    ):
        raise PackageError("runtime closure bootstrap module mismatch")
    bootstrap_source = held_roles["runtime_bootstrap_code"]
    if (
        bootstrap_source.size_bytes < 1
        or bootstrap["sha256"] != bootstrap_source.sha256
        or bootstrap["size_bytes"] != bootstrap_source.size_bytes
    ):
        raise PackageError("runtime closure bootstrap does not bind the bootstrap source")

    python_record = _exact_keys(
        closure["python"],
        {"implementation", "version", "abi_tag", "platform", "executable_sha256"},
        "runtime closure python",
    )
    if python_record["implementation"] != "CPython" or python_record["version"] != "3.12.13":
        raise PackageError("runtime closure Python implementation/version mismatch")
    for key in ("abi_tag", "platform"):
        if not isinstance(python_record[key], str) or not python_record[key]:
            raise PackageError(f"runtime closure python.{key} is invalid")
    _normalized_sha(python_record["executable_sha256"], "runtime closure Python executable")

    numpy_record = _exact_keys(closure["numpy"], {"version"}, "runtime closure numpy")
    if numpy_record["version"] != "2.5.0":
        raise PackageError("runtime closure NumPy version mismatch")

    pure_archive = _exact_keys(
        closure["pure_archive"],
        {"path", "sha256", "size_bytes", "format", "compression"},
        "runtime closure pure_archive",
    )
    if (
        pure_archive["path"] != "pure/RUNTIME_PURE.zip"
        or pure_archive["format"] != "zip"
        or pure_archive["compression"] != "ZIP_STORED"
        or type(pure_archive["size_bytes"]) is not int
        or pure_archive["size_bytes"] < 1
    ):
        raise PackageError("runtime closure pure archive contract mismatch")
    _normalized_sha(pure_archive["sha256"], "runtime closure pure archive")

    raw_members = closure["members"]
    if not isinstance(raw_members, list) or not raw_members:
        raise PackageError("runtime closure members must be a nonempty list")
    members = [_validate_member_record(item, index) for index, item in enumerate(raw_members)]
    member_paths = [str(item["path"]) for item in members]
    if member_paths != sorted(member_paths) or len(member_paths) != len(set(member_paths)):
        raise PackageError("runtime closure members must be path-sorted and unique")
    module_names = [item["module"] for item in members if item["module"] is not None]
    if len(module_names) != len(set(module_names)):
        raise PackageError("runtime closure pure module names must be unique")
    for item in members:
        if item["kind"] == "python_source" and not str(item["path"]).endswith(".py"):
            raise PackageError("runtime Python source member must end in .py")
        if item["kind"] == "data" and (
            item["module"] is not None or item["is_package"] is not False
        ):
            raise PackageError("runtime data member has module/package semantics")

    required_members: dict[str, tuple[str, str, bool, str | None]] = {
        "runtime_package_init_code": (
            "rfic_transformer_inverse_design/__init__.py",
            "package_init_code",
            True,
            "rfic_transformer_inverse_design",
        ),
        "runtime_bootstrap_code": (
            "rfic_transformer_inverse_design/controlled_real10k_20k_runtime_bootstrap.py",
            "runtime_bootstrap_code",
            False,
            "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap",
        ),
        "shared_contract_code": (
            "rfic_transformer_inverse_design/controlled_real10k_20k_contract.py",
            "shared_contract_code",
            False,
            "rfic_transformer_inverse_design.controlled_real10k_20k_contract",
        ),
        "splitter_code": (
            "rfic_transformer_inverse_design/model_splitting.py",
            "splitter_code",
            False,
            "rfic_transformer_inverse_design.model_splitting",
        ),
        "materialization_builder_code": (
            "controlled_entrypoints/build_controlled_real10k_20k_nested.py",
            "materialization_builder_code",
            False,
            None,
        ),
        "materialization_gate_code": (
            "controlled_entrypoints/run_controlled_real10k_20k_materialization.py",
            "materialization_gate_code",
            False,
            None,
        ),
        "runner_code": (
            "controlled_entrypoints/run_controlled_real10k_20k_paired.py",
            "runner_code",
            False,
            None,
        ),
        "evaluator_code": (
            "controlled_entrypoints/evaluate_controlled_real10k_20k_common.py",
            "evaluator_code",
            False,
            None,
        ),
        "native_smoke_test": (
            "controlled_entrypoints/controlled_real10k_20k_mars_native_smoke.py",
            "native_smoke_test",
            False,
            None,
        ),
        "trainer_code": (
            "controlled_entrypoints/train_physical_feature_tandem_inverse.py",
            "trainer_code",
            False,
            None,
        ),
    }
    members_by_path = {str(item["path"]): item for item in members}
    for _, (_, closure_role, _, _) in required_members.items():
        if sum(item["role"] == closure_role for item in members) != 1:
            raise PackageError(f"runtime closure role is not exactly singular: {closure_role}")
    for source_role, (member_path, closure_role, is_package, module) in required_members.items():
        member = members_by_path.get(member_path)
        source = held_roles[source_role]
        if (
            member is None
            or member["role"] != closure_role
            or member["sha256"] != source.sha256
            or member["size_bytes"] != source.size_bytes
            or member["kind"] != "python_source"
            or member["is_package"] is not is_package
            or member["module"] != module
        ):
            raise PackageError(f"runtime closure member does not bind role: {source_role}")
    if held_roles["runtime_package_init_code"].size_bytes != 0:
        raise PackageError("runtime package initializer must be exactly zero bytes")
    if any(item["role"] == "numpy_pure" for item in members) is False:
        raise PackageError("runtime closure has no vendored NumPy pure member")

    raw_extensions = closure["native_extensions"]
    if not isinstance(raw_extensions, list):
        raise PackageError("runtime closure native_extensions must be a list")
    extensions: list[Mapping[str, Any]] = []
    for index, value in enumerate(raw_extensions):
        record = _exact_keys(
            value,
            {"module", "path", "basename", "sha256", "size_bytes", "init_symbol", "dt_needed"},
            f"runtime closure native_extensions[{index}]",
        )
        path = _safe_relative(record["path"], f"native extension path {index}")
        if _forbidden_runtime_path(path) or not path.as_posix().isascii():
            raise PackageError("runtime native extension path is forbidden")
        if (
            not isinstance(record["module"], str)
            or not record["module"]
            or record["basename"] != path.name
            or path.as_posix()
            != f"native/extensions/{record['module']}/{record['basename']}"
            or type(record["size_bytes"]) is not int
            or record["size_bytes"] < 1
            or not isinstance(record["init_symbol"], str)
            or record["init_symbol"]
            != "PyInit_" + str(record["module"]).rsplit(".", 1)[-1]
        ):
            raise PackageError("runtime native extension record mismatch")
        _normalized_sha(record["sha256"], "native extension SHA-256")
        _exact_string_list(record["dt_needed"], "native extension dt_needed")
        extensions.append(record)
    if [item["module"] for item in extensions] != sorted(item["module"] for item in extensions):
        raise PackageError("runtime native_extensions must be module-sorted")
    if len({item["module"] for item in extensions}) != len(extensions):
        raise PackageError("runtime native extension modules must be unique")

    raw_libraries = closure["native_libraries"]
    if not isinstance(raw_libraries, list):
        raise PackageError("runtime closure native_libraries must be a list")
    libraries: list[Mapping[str, Any]] = []
    for index, value in enumerate(raw_libraries):
        record = _exact_keys(
            value,
            {"soname", "path", "basename", "sha256", "size_bytes", "dt_needed", "load_order"},
            f"runtime closure native_libraries[{index}]",
        )
        path = _safe_relative(record["path"], f"native library path {index}")
        if _forbidden_runtime_path(path) or not path.as_posix().isascii():
            raise PackageError("runtime native library path is forbidden")
        if (
            not isinstance(record["soname"], str)
            or not record["soname"]
            or record["basename"] != path.name
            or path.as_posix()
            != f"native/libraries/{record['soname']}/{record['basename']}"
            or type(record["size_bytes"]) is not int
            or record["size_bytes"] < 1
            or type(record["load_order"]) is not int
            or record["load_order"] < 0
        ):
            raise PackageError("runtime native library record mismatch")
        _normalized_sha(record["sha256"], "native library SHA-256")
        _exact_string_list(record["dt_needed"], "native library dt_needed")
        libraries.append(record)
    if [(item["load_order"], item["soname"]) for item in libraries] != sorted(
        (item["load_order"], item["soname"]) for item in libraries
    ):
        raise PackageError("runtime native_libraries must be load-order/SONAME sorted")
    if [item["load_order"] for item in libraries] != list(range(len(libraries))):
        raise PackageError("runtime native library load_order must be consecutive from zero")
    if len({item["soname"] for item in libraries}) != len(libraries):
        raise PackageError("runtime native library SONAMEs must be unique")

    allowlist = _exact_string_list(
        closure["system_library_allowlist"], "runtime system_library_allowlist"
    )
    if allowlist != list(TRUSTED_SYSTEM_LIBRARY_ALLOWLIST):
        raise PackageError("runtime system library allowlist is not the frozen host boundary")
    vendored_sonames = {item["soname"] for item in libraries}
    if set(module_names) & {item["module"] for item in extensions}:
        raise PackageError("runtime pure/native module identities overlap")
    if vendored_sonames & set(allowlist):
        raise PackageError("vendored and system library identities overlap")
    for record in [*extensions, *libraries]:
        unresolved = set(record["dt_needed"]) - vendored_sonames - set(allowlist)
        if unresolved:
            raise PackageError(f"runtime native dependency is not closed: {sorted(unresolved)}")
    load_order_by_soname = {item["soname"]: item["load_order"] for item in libraries}
    for record in libraries:
        for needed in record["dt_needed"]:
            if needed in load_order_by_soname and load_order_by_soname[needed] >= record["load_order"]:
                raise PackageError("runtime native library dependency order is not topological")

    entrypoint_roles = {
        "materialization": "materialization_gate_code",
        "runner": "runner_code",
        "trainer": "trainer_code",
        "evaluator": "evaluator_code",
        "native_smoke": "native_smoke_test",
    }
    entrypoints = _exact_keys(closure["entrypoints"], entrypoint_roles, "entrypoints")
    for key, role in entrypoint_roles.items():
        record = _exact_keys(
            entrypoints[key], {"member", "sha256", "display_path", "role"}, f"entrypoint {key}"
        )
        expected_member = required_members[role][0]
        bound_member = members_by_path.get(expected_member)
        if (
            record["member"] != expected_member
            or record["sha256"] != held_roles[role].sha256
            or record["role"] != role
            or not isinstance(record["display_path"], str)
            or not record["display_path"].startswith(
                "runtime/project/tests/" if key == "native_smoke" else "runtime/project/scripts/"
            )
            or bound_member is None
            or bound_member["module"] is not None
            or bound_member["is_package"] is not False
        ):
            raise PackageError(f"runtime closure {key} entrypoint mismatch")

    expected_tree_paths = {str(pure_archive["path"])} | {
        str(item["path"]) for item in extensions
    } | {str(item["path"]) for item in libraries}
    if set(tree_files) != expected_tree_paths:
        raise PackageError(
            "runtime dependency tree file set mismatch: "
            f"missing={sorted(expected_tree_paths - set(tree_files))} "
            f"extra={sorted(set(tree_files) - expected_tree_paths)}"
        )
    tree_records: list[dict[str, Any]] = []
    indexed_records = [pure_archive, *extensions, *libraries]
    by_path = {str(item["path"]): item for item in indexed_records}
    for path in sorted(tree_files):
        metadata, descriptor = tree_files[path]
        record = by_path[path]
        actual_sha = _sha256_fd(descriptor)
        if record["sha256"] != actual_sha or record["size_bytes"] != metadata.st_size:
            raise PackageError(f"runtime dependency tree identity mismatch: {path}")
        tree_records.append(
            {
                "path": path,
                "sha256": actual_sha,
                "size_bytes": int(metadata.st_size),
                "mode_octal": "0444",
                "nlink": 1,
            }
        )
    pure_payload = _read_fd(tree_files[str(pure_archive["path"])][1])
    _validate_zip_exact(pure_payload, members)
    tree_sha = _canonical_json_sha(tree_records)
    return closure, tree_records, tree_sha


def _load_build_spec(
    spec_file: HeldFile, held_builder: HeldFile
) -> tuple[Mapping[str, Any], dict[str, HeldFile], HeldDirectory, list[tuple[PurePosixPath, os.stat_result, int]], Mapping[str, Any], list[dict[str, Any]], str]:
    spec = _strict_json_loads(spec_file.bytes(), "package build spec")
    spec = _exact_keys(spec, {"schema", "package_version", "roles"}, "package build spec")
    if spec["schema"] != BUILD_SPEC_SCHEMA or spec["package_version"] != PACKAGE_VERSION:
        raise PackageError("package build spec schema/version mismatch")
    if not isinstance(spec["roles"], dict):
        raise PackageError("package build spec roles must be an object")
    actual_roles = set(spec["roles"])
    if actual_roles != REQUIRED_ROLES:
        raise PackageError(
            "package build spec role set must be exact: "
            f"missing={sorted(REQUIRED_ROLES - actual_roles)} "
            f"extra={sorted(actual_roles - REQUIRED_ROLES)}"
        )

    held_roles: dict[str, HeldFile] = {}
    tree_root: HeldDirectory | None = None
    tree_walk: list[tuple[PurePosixPath, os.stat_result, int]] = []
    try:
        seen_paths: set[str] = set()
        seen_shas: set[str] = set()
        for role in sorted(FILE_ROLES):
            if ROLE_PATTERN.fullmatch(role) is None:
                raise PackageError(f"invalid frozen role: {role}")
            entry = _exact_keys(
                spec["roles"][role], {"kind", "source_path", "sha256"}, f"role {role}"
            )
            if entry["kind"] != "file":
                raise PackageError(f"role {role} must be kind=file")
            held = HeldFile.open(entry["source_path"], f"role {role}", expected_sha256=entry["sha256"])
            canonical = str(held.path)
            if canonical in seen_paths:
                held.close()
                raise PackageError(f"duplicate artifact source path: {canonical}")
            if held.sha256 in seen_shas:
                held.close()
                raise PackageError(f"duplicate artifact SHA-256: {held.sha256}")
            seen_paths.add(canonical)
            seen_shas.add(held.sha256)
            held_roles[role] = held
            if role in PYTHON_CODE_ROLES:
                try:
                    ast.parse(held.bytes(), filename=str(held.path))
                except (SyntaxError, UnicodeDecodeError) as error:
                    raise PackageError(f"Python source role {role} is invalid: {error}") from error

        if held_roles["package_builder_code"].sha256 != held_builder.sha256:
            raise PackageError("package_builder_code does not exactly bind the executing builder")
        if held_roles["package_builder_code"].path != held_builder.path:
            raise PackageError("package_builder_code path does not bind the executing builder")
        _validate_process_singleton_contract(
            held_roles["process_singleton_contract_json"].bytes()
        )

        tree_entry = _exact_keys(
            spec["roles"][TREE_ROLE],
            {"kind", "source_root", "inventory_path", "inventory_sha256"},
            f"role {TREE_ROLE}",
        )
        if tree_entry["kind"] != "tree":
            raise PackageError(f"role {TREE_ROLE} must be kind=tree")
        inventory = held_roles["runtime_dependency_closure_json"]
        if (
            tree_entry["inventory_path"] != str(inventory.path)
            or tree_entry["inventory_sha256"] != inventory.sha256
        ):
            raise PackageError("runtime dependency tree inventory binding mismatch")
        tree_root = HeldDirectory.open(tree_entry["source_root"], f"role {TREE_ROLE}")
        tree_walk, tree_directories = _walk_descriptor(tree_root.descriptor)
        tree_files = {
            item[0].as_posix(): (item[1], item[2])
            for item in tree_walk
        }
        if len(tree_files) != len(tree_walk):
            raise PackageError("runtime dependency tree contains duplicate paths")
        for relative in [*tree_files.keys(), *(item.as_posix() for item in tree_directories)]:
            safe = _safe_relative(relative, "runtime dependency tree path")
            if not safe.as_posix().isascii() or _forbidden_runtime_path(safe):
                raise PackageError(f"runtime dependency tree path is forbidden: {safe}")
        closure, tree_records, tree_sha = _validate_runtime_closure(
            inventory.bytes(), held_roles, tree_files
        )
        expected_tree_directories: set[str] = set()
        for record in tree_records:
            parent = PurePosixPath(record["path"]).parent
            while parent.parts:
                expected_tree_directories.add(parent.as_posix())
                parent = parent.parent
        observed_tree_directories = {item.as_posix() for item in tree_directories}
        if observed_tree_directories != expected_tree_directories:
            raise PackageError(
                "runtime dependency tree directory set mismatch: "
                f"missing={sorted(expected_tree_directories - observed_tree_directories)} "
                f"extra={sorted(observed_tree_directories - expected_tree_directories)}"
            )
        return spec, held_roles, tree_root, tree_walk, closure, tree_records, tree_sha
    except BaseException:
        for held in held_roles.values():
            held.close()
        _close_walk_files(tree_walk)
        if tree_root is not None:
            tree_root.close()
        raise


def _sha256_path_relaxed(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _invocation_context(
    *,
    argv: Sequence[str],
    out_dir: Path,
    failure_receipt_dir: Path,
    package_spec_path: Path,
    expected_package_spec_sha256: str,
    expected_builder_sha256: str,
) -> dict[str, Any]:
    executable_lexical = Path(sys.executable)
    executable_resolved = executable_lexical.resolve(strict=True)
    environment = {key: os.environ[key] for key in sorted(os.environ)}
    flag_names = (
        "debug",
        "inspect",
        "interactive",
        "optimize",
        "dont_write_bytecode",
        "no_user_site",
        "no_site",
        "ignore_environment",
        "verbose",
        "bytes_warning",
        "quiet",
        "hash_randomization",
        "isolated",
        "dev_mode",
        "utf8_mode",
        "safe_path",
        "int_max_str_digits",
    )
    flags = {
        name: getattr(sys.flags, name)
        for name in flag_names
        if hasattr(sys.flags, name)
    }
    cwd = Path.cwd()
    cwd_metadata = cwd.stat()
    return {
        "argv": [str(item) for item in argv],
        "cwd": {
            "lexical": os.getcwd(),
            "resolved": str(cwd.resolve(strict=True)),
            "device": int(cwd_metadata.st_dev),
            "inode": int(cwd_metadata.st_ino),
        },
        "output_dir": str(out_dir),
        "failure_receipt_dir": str(failure_receipt_dir),
        "package_spec": {
            "path": str(package_spec_path),
            "expected_sha256": expected_package_spec_sha256,
        },
        "builder": {
            "path": str(Path(__file__).resolve(strict=True)),
            "expected_sha256": expected_builder_sha256,
        },
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "version_info": list(sys.version_info[:5]),
            "executable_lexical": str(executable_lexical),
            "executable_resolved": str(executable_resolved),
            "executable_sha256": _sha256_path_relaxed(executable_resolved),
            "flags": flags,
        },
        "runtime": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "system": platform.system(),
            "release": platform.release(),
            "byteorder": sys.byteorder,
            "filesystem_encoding": sys.getfilesystemencoding(),
        },
        "environment": {
            "raw_values_recorded": False,
            "key_count": len(environment),
            "keys": list(environment),
            "keyset_sha256": _canonical_json_sha(list(environment)),
            "key_value_map_sha256": _canonical_json_sha(environment),
        },
    }


def _snapshot_tree(root_descriptor: int) -> dict[str, Any]:
    files, directories = _walk_descriptor(root_descriptor)
    try:
        entries: list[dict[str, Any]] = []
        for relative in sorted(directories):
            entries.append({"path": relative.as_posix(), "type": "directory"})
        for relative, metadata, descriptor in files:
            entries.append(
                {
                    "path": relative.as_posix(),
                    "type": "regular",
                    "sha256": _sha256_fd(descriptor),
                    "size_bytes": int(metadata.st_size),
                    "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
                    "nlink": int(metadata.st_nlink),
                }
            )
        entries.sort(key=lambda item: (item["path"], item["type"]))
        return {
            "automatic_cleanup_performed": False,
            "entries": entries,
            "entry_count": len(entries),
        }
    finally:
        _close_walk_files(files)


def _directory_binding(directory: HeldDirectory) -> dict[str, Any]:
    metadata = os.fstat(directory.descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise PackageError(f"{directory.label} is no longer a directory")
    return {
        "path": str(directory.path),
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def _reserve_attempt_terminal_inode(attempt_root: HeldDirectory) -> int:
    """Reserve the terminal pathname once while the attempt root is writable.

    POSIX does not permit creating a directory entry after the root is frozen
    to 0555.  The zero-length 0600 inode is therefore reserved with O_EXCL and
    its directory entry made durable before the freeze.  It is not terminal
    authority: reviewed schema bytes and mode 0444 are published only after
    the PASS body and both directory barriers/continuity checks succeed.
    """

    descriptor = os.open(
        BUILD_ATTEMPT_COMMITTED_NAME,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=attempt_root.descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            raise PackageError("attempt terminal inode reservation is invalid")
        os.fsync(attempt_root.descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _publish_reserved_attempt_terminal(
    descriptor: int, payload: bytes
) -> dict[str, Any]:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_size != 0
    ):
        raise PackageError("attempt terminal reserved inode changed before publication")
    view = memoryview(payload)
    offset = 0
    while view:
        written = os.pwrite(descriptor, view, offset)
        if written <= 0:
            raise OSError("short write while publishing attempt terminal marker")
        view = view[written:]
        offset += written
    os.ftruncate(descriptor, len(payload))
    # Make content durable while the inode is still visibly non-authoritative,
    # then publish the reviewed 0444 identity and durably persist that metadata.
    os.fsync(descriptor)
    os.fchmod(descriptor, FILE_MODE)
    os.fsync(descriptor)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != FILE_MODE
        or metadata.st_size != len(payload)
        or _sha256_fd(descriptor) != _sha256_bytes(payload)
    ):
        raise PackageError("attempt terminal marker identity is invalid")
    return {
        "path": BUILD_ATTEMPT_COMMITTED_NAME,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "mode_octal": "0444",
        "nlink": 1,
    }


def _finalize_pass_attempt(
    attempt_parent: HeldDirectory,
    attempt_root: HeldDirectory,
    output_root: HeldDirectory,
    body: Mapping[str, Any],
    package_commit_record: Mapping[str, Any],
    state: dict[str, bool],
) -> tuple[Path, Path]:
    state["pass_finalization_started"] = True
    terminal_descriptor = _reserve_attempt_terminal_inode(attempt_root)
    state["terminal_inode_reserved"] = True
    try:
        body_payload = _json_bytes(body)
        body_record = _write_file_at(
            attempt_root.descriptor,
            PurePosixPath(BUILD_ATTEMPT_RECEIPT_NAME),
            body_payload,
        )
        state["pass_body_written"] = True

        # The body entry/file are already fsync'd by _write_file_at.  These
        # explicit barriers bind the held root and its already-durable parent
        # before the root becomes immutable.
        os.fsync(attempt_root.descriptor)
        os.fsync(attempt_parent.descriptor)
        os.fchmod(attempt_root.descriptor, DIRECTORY_MODE)
        os.fsync(attempt_root.descriptor)
        os.fsync(attempt_parent.descriptor)
        attempt_root.assert_continuity()
        attempt_parent.assert_continuity()

        package_root_binding = _directory_binding(output_root)
        if package_root_binding["mode_octal"] != "0555":
            raise PackageError("package root is not frozen before attempt commit")
        attempt_root_binding = _directory_binding(attempt_root)
        if attempt_root_binding["mode_octal"] != "0555":
            raise PackageError("attempt root is not frozen before attempt commit")
        marker = {
            "schema": BUILD_ATTEMPT_COMMIT_SCHEMA,
            "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
            "committed_utc": _utc_now(),
            "body": {
                "path": str(attempt_root.path / BUILD_ATTEMPT_RECEIPT_NAME),
                "sha256": body_record["sha256"],
                "schema": BUILD_ATTEMPT_BODY_SCHEMA,
                "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            },
            "package_commit": {
                "path": str(output_root.path / COMMIT_NAME),
                "sha256": package_commit_record["sha256"],
                "schema": PACKAGE_COMMIT_SCHEMA,
                "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
            },
            "package_root": package_root_binding,
            "attempt_root": attempt_root_binding,
            "attempt_parent": _directory_binding(attempt_parent),
            "publication": {
                "body_file_fsync": True,
                "attempt_root_fsync": True,
                "attempt_parent_fsync": True,
                "attempt_root_frozen": True,
                "continuity_verified": True,
                "terminal_inode_reserved_create_once_before_freeze": True,
                "terminal_bytes_published_after_durability": True,
                "post_commit_attempt_file_creation_permitted": False,
            },
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
        }
        _publish_reserved_attempt_terminal(terminal_descriptor, _json_bytes(marker))
        state["terminal_marker_published"] = True
    finally:
        try:
            os.close(terminal_descriptor)
        except OSError:
            # No close error may turn an already durable terminal marker into a
            # process-level failure with contradictory surviving authority.
            pass
    return (
        attempt_root.path / BUILD_ATTEMPT_RECEIPT_NAME,
        attempt_root.path / BUILD_ATTEMPT_COMMITTED_NAME,
    )


def _publish_attempt_problem(
    attempt_parent: HeldDirectory,
    attempt_root: HeldDirectory,
    *,
    name: str,
    payload: Mapping[str, Any],
) -> Path:
    # A late PASS-publication fault may occur after the attempt root was frozen.
    # Re-open only this failure transaction, publish a distinct create-once
    # NO-GO file, and freeze it again.  The PASS body pathname is never reused.
    if stat.S_IMODE(os.fstat(attempt_root.descriptor).st_mode) != WORKING_DIRECTORY_MODE:
        os.fchmod(attempt_root.descriptor, WORKING_DIRECTORY_MODE)
        os.fsync(attempt_root.descriptor)
    _write_file_at(attempt_root.descriptor, PurePosixPath(name), _json_bytes(payload))
    os.fchmod(attempt_root.descriptor, DIRECTORY_MODE)
    os.fsync(attempt_root.descriptor)
    os.fsync(attempt_parent.descriptor)
    attempt_root.assert_continuity()
    attempt_parent.assert_continuity()
    return attempt_root.path / name


def _copy_payload(
    output_descriptor: int,
    held_roles: Mapping[str, HeldFile],
    tree_walk: Sequence[tuple[PurePosixPath, os.stat_result, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts: list[dict[str, Any]] = []
    physical_records: list[dict[str, Any]] = []
    for role in sorted(FILE_ROLES):
        held = held_roles[role]
        held.assert_continuity()
        destination = _safe_relative(ROLE_DESTINATIONS[role], f"destination for {role}")
        record = _write_file_at(output_descriptor, destination, held.bytes())
        physical_records.append(dict(record))
        artifacts.append(
            {
                "role": role,
                "kind": "file",
                **record,
                "source_path_at_build": str(held.path),
            }
        )

    tree_prefix = _safe_relative(ROLE_DESTINATIONS[TREE_ROLE], "tree destination")
    for relative, metadata, descriptor in sorted(tree_walk, key=lambda item: item[0]):
        payload = _read_fd(descriptor)
        if len(payload) != metadata.st_size or _sha256_bytes(payload) != _sha256_fd(descriptor):
            raise PackageError(f"runtime tree source changed: {relative}")
        record = _write_file_at(output_descriptor, tree_prefix / relative, payload)
        physical_records.append(dict(record))
    return artifacts, physical_records


def _durable_package_barrier(
    output_parent: HeldDirectory, output_root: HeldDirectory
) -> None:
    os.fsync(output_root.descriptor)
    os.fsync(output_parent.descriptor)


def _write_sha_index(records: Sequence[Mapping[str, Any]]) -> bytes:
    paths = [str(item["path"]) for item in records]
    if len(paths) != len(set(paths)):
        raise PackageError("SHA index inputs contain duplicate paths")
    return "".join(
        f"{item['sha256']}  {item['path']}\n"
        for item in sorted(records, key=lambda value: str(value["path"]))
    ).encode("ascii")


def build_package(
    out_dir: Path,
    package_spec_path: Path,
    failure_receipt_dir: Path,
    *,
    expected_package_spec_sha256: str,
    expected_builder_sha256: str,
    invocation_argv: Sequence[str] | None = None,
) -> dict[str, Path]:
    """Build, audit, and freeze one package without overwriting any path."""

    out_dir = _absolute_path(str(out_dir), "output directory", must_exist=False)
    failure_receipt_dir = _absolute_path(
        str(failure_receipt_dir), "failure receipt directory", must_exist=False
    )
    package_spec_path = _absolute_path(
        str(package_spec_path), "package build spec", must_exist=True
    )
    expected_package_spec_sha256 = _normalized_sha(
        expected_package_spec_sha256, "expected package build spec SHA-256"
    )
    expected_builder_sha256 = _normalized_sha(
        expected_builder_sha256, "expected builder SHA-256"
    )
    attempt_parent, attempt_root = _reserve_directory(
        failure_receipt_dir, "failure receipt directory"
    )
    invocation = _invocation_context(
        argv=list(sys.argv if invocation_argv is None else invocation_argv),
        out_dir=out_dir,
        failure_receipt_dir=failure_receipt_dir,
        package_spec_path=package_spec_path,
        expected_package_spec_sha256=expected_package_spec_sha256,
        expected_builder_sha256=expected_builder_sha256,
    )
    started_utc = _utc_now()
    builder_source: HeldFile | None = None
    spec_source: HeldFile | None = None
    held_roles: dict[str, HeldFile] = {}
    tree_root: HeldDirectory | None = None
    tree_walk: list[tuple[PurePosixPath, os.stat_result, int]] = []
    output_parent: HeldDirectory | None = None
    output_root: HeldDirectory | None = None
    output_created = False
    try:
        builder_source = HeldFile.open(
            str(Path(__file__).resolve(strict=True)),
            "executing package builder",
            expected_sha256=expected_builder_sha256,
        )
        spec_source = HeldFile.open(
            str(package_spec_path),
            "package build spec",
            expected_sha256=expected_package_spec_sha256,
        )
        (
            spec,
            held_roles,
            tree_root,
            tree_walk,
            closure,
            tree_records,
            tree_sha,
        ) = _load_build_spec(spec_source, builder_source)

        output_parent, output_root = _reserve_directory(out_dir, "package output directory")
        output_created = True
        artifacts, physical_records = _copy_payload(
            output_root.descriptor, held_roles, tree_walk
        )

        tree_prefix = ROLE_DESTINATIONS[TREE_ROLE]
        tree_directory_paths: set[str] = set()
        for tree_record in tree_records:
            parent = PurePosixPath(tree_record["path"]).parent
            while parent.parts:
                tree_directory_paths.add(parent.as_posix())
                parent = parent.parent
        tree_artifact = {
            "role": TREE_ROLE,
            "kind": "tree",
            "path": tree_prefix,
            "sha256": tree_sha,
            "inventory_sha256": held_roles["runtime_dependency_closure_json"].sha256,
            "file_count": len(tree_records),
            "directory_count": len(tree_directory_paths),
            "size_bytes": sum(int(item["size_bytes"]) for item in tree_records),
            "mode_octal": "0555",
            "source_path_at_build": str(tree_root.path),
            "members": [
                {**item, "path": f"{tree_prefix}/{item['path']}"}
                for item in tree_records
            ],
        }
        artifacts.append(tree_artifact)
        artifacts.sort(key=lambda item: item["role"])

        singleton_record = _write_file_at(
            output_root.descriptor, PurePosixPath(SINGLETON_LOCK_NAME), b""
        )
        physical_records.append(singleton_record)
        role_identity = {
            item["role"]: {
                "kind": item["kind"],
                "path": item["path"],
                "sha256": item["sha256"],
            }
            for item in artifacts
        }
        manifest = {
            "schema": SCHEMA,
            "package_version": PACKAGE_VERSION,
            "build_spec": {
                "schema": spec["schema"],
                "path_at_build": str(spec_source.path),
                "sha256": spec_source.sha256,
            },
            "required_roles": sorted(REQUIRED_ROLES),
            "role_destinations": dict(sorted(ROLE_DESTINATIONS.items())),
            "role_identity": role_identity,
            "artifacts": artifacts,
            "runtime": {
                "entrypoints": RUNTIME_ENTRYPOINTS,
                "import_graph": IMPORT_GRAPH,
                "dependency_closure": {
                    "schema": closure["schema"],
                    "inventory_path": ROLE_DESTINATIONS["runtime_dependency_closure_json"],
                    "inventory_sha256": held_roles["runtime_dependency_closure_json"].sha256,
                    "tree_path": ROLE_DESTINATIONS[TREE_ROLE],
                    "tree_sha256": tree_sha,
                    "pure_archive": closure["pure_archive"],
                    "python": closure["python"],
                    "numpy": closure["numpy"],
                },
                "process_singleton_contract": {
                    "schema": PROCESS_SINGLETON_CONTRACT_SCHEMA,
                    "path": ROLE_DESTINATIONS["process_singleton_contract_json"],
                    "sha256": held_roles["process_singleton_contract_json"].sha256,
                    "lock_path": SINGLETON_LOCK_NAME,
                    "lock_sha256": singleton_record["sha256"],
                    "protected_entrypoints": _expected_process_singleton_contract()[
                        "protected_entrypoints"
                    ],
                },
            },
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
            "result_accessed": False,
            "numerical_metrics_accessed": False,
        }
        manifest_record = _write_file_at(
            output_root.descriptor, PurePosixPath(MANIFEST_NAME), _json_bytes(manifest)
        )
        physical_records.append(manifest_record)

        qa_required = {
            "schema": QA_REQUIRED_SCHEMA,
            "verdict": "NO_GO_PENDING_EXTERNAL_CODE_QA",
            "package_manifest": {
                "path": MANIFEST_NAME,
                "sha256": manifest_record["sha256"],
            },
            "required_go_receipt": {
                "issuer": "independent_qa",
                "verdict": "GO",
                "exact_binding_keyset_required": True,
                "required_binding_keys": list(REQUIRED_GO_BINDING_KEYS),
                "maximum_age_seconds": 21600,
                "future_clock_skew_seconds": 0,
                "one_use": True,
            },
            "required_native_test_roles": ["native_smoke_test"],
            "required_role_identity": role_identity,
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
        }
        qa_record = _write_file_at(
            output_root.descriptor,
            PurePosixPath(QA_REQUIRED_NAME),
            _json_bytes(qa_required),
        )
        physical_records.append(qa_record)

        receipt = {
            "schema": PACKAGE_RECEIPT_SCHEMA,
            "status": "PASS_PREPARED_AWAITING_INDEPENDENT_QA",
            "package_version": PACKAGE_VERSION,
            "manifest": {"path": MANIFEST_NAME, "sha256": manifest_record["sha256"]},
            "independent_qa_required": {
                "path": QA_REQUIRED_NAME,
                "sha256": qa_record["sha256"],
            },
            "role_identity": role_identity,
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
            "result_accessed": False,
            "numerical_metrics_accessed": False,
        }
        receipt_record = _write_file_at(
            output_root.descriptor, PurePosixPath(RECEIPT_NAME), _json_bytes(receipt)
        )
        physical_records.append(receipt_record)

        sha_index_payload = _write_sha_index(physical_records)
        sha_index_record = _write_file_at(
            output_root.descriptor, PurePosixPath(SHA_INDEX_NAME), sha_index_payload
        )
        commit = {
            "schema": PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
            "package_version": PACKAGE_VERSION,
            "manifest": {"path": MANIFEST_NAME, "sha256": manifest_record["sha256"]},
            "receipt": {"path": RECEIPT_NAME, "sha256": receipt_record["sha256"]},
            "independent_qa_required": {
                "path": QA_REQUIRED_NAME,
                "sha256": qa_record["sha256"],
            },
            "sha256sums": {
                "path": SHA_INDEX_NAME,
                "sha256": sha_index_record["sha256"],
            },
            "required_external_pass_attempt": {
                "body": {
                    "path": str(failure_receipt_dir / BUILD_ATTEMPT_RECEIPT_NAME),
                    "schema": BUILD_ATTEMPT_BODY_SCHEMA,
                    "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
                },
                "committed": {
                    "path": str(failure_receipt_dir / BUILD_ATTEMPT_COMMITTED_NAME),
                    "schema": BUILD_ATTEMPT_COMMIT_SCHEMA,
                    "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
                },
            },
            "creation_order_contract": {
                "this_member_created_last": True,
                "post_commit_package_file_creation_permitted": False,
            },
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
        }
        # Deliberately the final package member. Nothing below creates a package file.
        commit_record = _write_file_at(
            output_root.descriptor, PurePosixPath(COMMIT_NAME), _json_bytes(commit)
        )
        _freeze_directories(output_root.descriptor)
        _durable_package_barrier(output_parent, output_root)
        output_parent.assert_continuity()
        output_root.assert_continuity()
        observed = _audit_frozen_tree(output_root.descriptor)
        expected_paths = {item["path"] for item in physical_records} | {
            SHA_INDEX_NAME,
            COMMIT_NAME,
        }
        if set(observed) != expected_paths:
            raise PackageError("final package file set differs from frozen closure")
        expected_records = {
            item["path"]: item
            for item in [*physical_records, sha_index_record, commit_record]
        }
        for path, expected in expected_records.items():
            actual = observed[path]
            if actual["sha256"] != expected["sha256"] or actual["size_bytes"] != expected["size_bytes"]:
                raise PackageError(f"final package audit failed: {path}")
        for held in held_roles.values():
            held.assert_continuity()
        _assert_source_tree_continuity(tree_root, tree_walk, tree_records)
        builder_source.assert_continuity()
        spec_source.assert_continuity()

        pass_receipt = {
            "schema": BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            "started_utc": started_utc,
            "completed_utc": _utc_now(),
            "invocation": invocation,
            "observed_identity": {
                "package_spec_sha256": spec_source.sha256,
                "builder_sha256": builder_source.sha256,
                "package_output_device": int(os.fstat(output_root.descriptor).st_dev),
                "package_output_inode": int(os.fstat(output_root.descriptor).st_ino),
            },
            "package": {
                "path": str(out_dir),
                "manifest_sha256": manifest_record["sha256"],
                "receipt_sha256": receipt_record["sha256"],
                "independent_qa_required_sha256": qa_record["sha256"],
                "sha256sums_sha256": sha_index_record["sha256"],
                "package_commit_sha256": commit_record["sha256"],
                "file_count": len(observed),
            },
            "partial_output_preserved": False,
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
        }
        attempt_state = {
            "pass_finalization_started": False,
            "terminal_inode_reserved": False,
            "pass_body_written": False,
            "terminal_marker_published": False,
        }
        attempt_receipt_path, attempt_committed_path = _finalize_pass_attempt(
            attempt_parent,
            attempt_root,
            output_root,
            pass_receipt,
            commit_record,
            attempt_state,
        )
        return {
            "package_root": out_dir,
            "manifest": out_dir / MANIFEST_NAME,
            "receipt": out_dir / RECEIPT_NAME,
            "independent_qa_required": out_dir / QA_REQUIRED_NAME,
            "sha_index": out_dir / SHA_INDEX_NAME,
            "package_commit": out_dir / COMMIT_NAME,
            "build_attempt_receipt": attempt_receipt_path,
            "build_attempt_committed": attempt_committed_path,
        }
    except BaseException as error:
        partial: dict[str, Any]
        if output_created and output_root is not None:
            freeze_error: str | None = None
            try:
                _freeze_directories(output_root.descriptor)
                os.fsync(output_root.descriptor)
                if output_parent is not None:
                    os.fsync(output_parent.descriptor)
            except BaseException as partial_freeze_error:
                freeze_error = (
                    f"{type(partial_freeze_error).__name__}: {partial_freeze_error}"
                )
            try:
                partial = _snapshot_tree(output_root.descriptor)
                partial["immutable_freeze_succeeded"] = freeze_error is None
                partial["immutable_freeze_error"] = freeze_error
            except BaseException as snapshot_error:
                partial = {
                    "automatic_cleanup_performed": False,
                    "entries": [],
                    "entry_count": 0,
                    "immutable_freeze_succeeded": freeze_error is None,
                    "immutable_freeze_error": freeze_error,
                    "snapshot_error": f"{type(snapshot_error).__name__}: {snapshot_error}",
                }
        else:
            partial = {
                "automatic_cleanup_performed": False,
                "entries": [],
                "entry_count": 0,
                "immutable_freeze_succeeded": False,
                "immutable_freeze_error": "package output was not created",
            }
        ambiguous = bool(
            locals().get("attempt_state", {}).get("pass_finalization_started", False)
        )
        problem_schema = (
            BUILD_ATTEMPT_AMBIGUOUS_SCHEMA
            if ambiguous
            else BUILD_ATTEMPT_FAILURE_SCHEMA
        )
        problem_status = (
            "AMBIGUOUS_NO_GO_PASS_BODY_NOT_COMMITTED"
            if ambiguous
            else "FAIL_NO_GO_PRESERVED"
        )
        problem_name = (
            BUILD_ATTEMPT_AMBIGUOUS_NAME if ambiguous else BUILD_ATTEMPT_FAILED_NAME
        )
        fail_receipt = {
            "schema": problem_schema,
            "status": problem_status,
            "started_utc": started_utc,
            "completed_utc": _utc_now(),
            "invocation": invocation,
            "failure": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "partial_output_preserved": output_created,
            "partial_output": partial,
            "authorities": dict(PACKAGE_AUTHORITIES),
            "execution_authorized": False,
        }
        try:
            _publish_attempt_problem(
                attempt_parent,
                attempt_root,
                name=problem_name,
                payload=fail_receipt,
            )
        except BaseException as terminal_error:
            if hasattr(error, "add_note"):
                error.add_note(
                    "attempt NO-GO terminalization also failed without replacing "
                    f"the original error: {type(terminal_error).__name__}: {terminal_error}"
                )
        raise
    finally:
        for held in held_roles.values():
            held.close()
        _close_walk_files(tree_walk)
        if tree_root is not None:
            tree_root.close()
        if spec_source is not None:
            spec_source.close()
        if builder_source is not None:
            builder_source.close()
        if output_root is not None:
            output_root.close()
        if output_parent is not None:
            output_parent.close()
        attempt_root.close()
        attempt_parent.close()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--failure-receipt-dir", required=True, type=Path)
    parser.add_argument("--package-spec", required=True, type=Path)
    parser.add_argument("--expected-package-spec-sha256", required=True)
    parser.add_argument("--expected-builder-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if (
        sys.flags.isolated != 1
        or sys.flags.no_site != 1
        or sys.dont_write_bytecode is not True
    ):
        raise PackageError("package builder CLI requires exact -I -B -S isolation")
    args = _parse_args(argv)
    effective_argv = list(sys.argv if argv is None else [str(Path(__file__)), *argv])
    paths = build_package(
        args.out_dir,
        args.package_spec,
        args.failure_receipt_dir,
        expected_package_spec_sha256=args.expected_package_spec_sha256,
        expected_builder_sha256=args.expected_builder_sha256,
        invocation_argv=effective_argv,
    )
    print(json.dumps({key: str(value) for key, value in paths.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
