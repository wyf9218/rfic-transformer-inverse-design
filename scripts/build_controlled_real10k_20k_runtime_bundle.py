#!/usr/bin/env python3
"""Build the sealed package-v5 runtime closure on the canonical MARS host.

This tool is deliberately result-blind.  It only snapshots hash-pinned code,
the already-installed NumPy runtime, and package inputs into two create-once
evidence directories.  It grants no authority to run native tests,
materialize data, train, evaluate, access metrics, or signal processes.

Production invocation is accepted only from CPython started with ``-I -B -S``.
The input specification is strict JSON and binds the exact Python executable,
ten project sources, and the remaining nine package-v5 file roles.  NumPy is
never imported: its package tree and ``numpy.libs`` tree are consumed through
held, no-follow descriptors and its ELF dependency closure is parsed by the
bounded parser in this file.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import platform
import re
import resource
import stat
import struct
import sys
import sysconfig
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


INPUT_SPEC_SCHEMA = "controlled_real10k_20k_runtime_bundle_input_spec_v1"
RUNTIME_CLOSURE_SCHEMA = "controlled_real10k_20k_runtime_closure_v1"
PACKAGE_BUILD_SPEC_SCHEMA = "controlled_real10k_20k_mars_package_build_spec_v1"
PACKAGE_VERSION = "v5"
FROZEN_PACKAGE_BUILDER_SHA256 = (
    "0b2c7d3382817a9a0647ce284dcc81334f0f60364ee38cecf0fb4a1cad1824d3"
)
PACKAGE_QA_REQUIRED_SCHEMA = (
    "controlled_real10k_20k_mars_package_independent_qa_required_v3"
)
PACKAGE_COMMIT_SCHEMA = "controlled_real10k_20k_mars_package_commit_v2"
PACKAGE_COMMIT_STATUS = (
    "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT"
)
PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_body_v3"
)
PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
BUNDLE_RECEIPT_SCHEMA = "controlled_real10k_20k_runtime_bundle_build_receipt_v1"
BUNDLE_COMMIT_SCHEMA = "controlled_real10k_20k_runtime_bundle_commit_v1"
ATTEMPT_RECEIPT_SCHEMA = "controlled_real10k_20k_runtime_bundle_attempt_receipt_v1"
ATTEMPT_COMMIT_SCHEMA = "controlled_real10k_20k_runtime_bundle_attempt_commit_v1"
FAILURE_SCHEMA = "controlled_real10k_20k_runtime_bundle_failure_v1"
FAILURE_COMMIT_SCHEMA = "controlled_real10k_20k_runtime_bundle_failure_commit_v1"

PYTHON_VERSION = "3.12.13"
NUMPY_VERSION = "2.5.0"
RUNTIME_BOOTSTRAP_MODULE = (
    "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap"
)
RUNTIME_TREE_RELATIVE = "runtime/dependencies"
RUNTIME_CLOSURE_RELATIVE = "runtime/contracts/RUNTIME_CLOSURE.json"
PACKAGE_SPEC_RELATIVE = "PACKAGE_BUILD_SPEC.json"
BUNDLE_RECEIPT_RELATIVE = "RUNTIME_BUNDLE_BUILD_RECEIPT.json"
SHA_INDEX_RELATIVE = "SHA256SUMS.txt"
BUNDLE_COMMIT_RELATIVE = "RUNTIME_BUNDLE_COMMIT.json"
ATTEMPT_RECEIPT_RELATIVE = "RUNTIME_BUNDLE_BUILD_ATTEMPT_RECEIPT.json"
ATTEMPT_COMMIT_RELATIVE = "RUNTIME_BUNDLE_ATTEMPT_COMMIT.json"
FAILURE_RELATIVE = "RUNTIME_BUNDLE_FAILURE.json"
FAILURE_SHA_INDEX_RELATIVE = "FAILURE_SHA256SUMS.txt"
FAILURE_COMMIT_RELATIVE = "RUNTIME_BUNDLE_FAILURE_COMMIT.json"

FILE_MODE = 0o444
DIRECTORY_MODE = 0o555
WORK_DIRECTORY_MODE = 0o700
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_EXTERNAL_ATTR = (stat.S_IFREG | FILE_MODE) << 16
MAX_TREE_FILES = 8192
MAX_TREE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 1024 * 1024 * 1024
MAX_ELF_PROGRAM_HEADERS = 4096
MAX_ELF_DYNAMIC_ENTRIES = 65536
MAX_ELF_STRING_TABLE = 64 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_BASENAMES = frozenset({"sitecustomize.py", "usercustomize.py"})
FORBIDDEN_SUFFIXES = frozenset({".pyc", ".pyo", ".pth"})

# These are the only unresolved ELF identities accepted from the trusted MARS
# Linux runtime.  The manifest binds this complete reviewed host boundary,
# never a result-dependent observed subset.  Any other DT_NEEDED identity
# fails closed.
FROZEN_SYSTEM_LIBRARY_POLICY = frozenset(
    {
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libdl.so.2",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        "librt.so.1",
        "libstdc++.so.6",
    }
)

PROJECT_SOURCE_ROLES = frozenset(
    {
        "runtime_bootstrap_code",
        "runtime_package_init_code",
        "shared_contract_code",
        "splitter_code",
        "materialization_gate_code",
        "materialization_builder_code",
        "runner_code",
        "trainer_code",
        "evaluator_code",
        "native_smoke_test",
    }
)
PACKAGE_EXTRA_FILE_ROLES = frozenset(
    {
        "package_builder_code",
        "preflight_code",
        "process_singleton_contract_json",
        "preregistration_v1_json",
        "preregistration_addendum_v1_1_json",
        "preregistration_addendum_v1_2_json",
        "authoritative_100k_csv",
        "historical_10k_csv",
        "historical_model_summary_json",
    }
)
GENERATED_PACKAGE_ROLES = frozenset(
    {"runtime_dependency_closure_tree", "runtime_dependency_closure_json"}
)
ALL_PACKAGE_ROLES = PROJECT_SOURCE_ROLES | PACKAGE_EXTRA_FILE_ROLES | GENERATED_PACKAGE_ROLES

PROJECT_MEMBER_BINDINGS: dict[str, tuple[str, str, str | None, bool]] = {
    "runtime_package_init_code": (
        "rfic_transformer_inverse_design/__init__.py",
        "package_init_code",
        "rfic_transformer_inverse_design",
        True,
    ),
    "runtime_bootstrap_code": (
        "rfic_transformer_inverse_design/controlled_real10k_20k_runtime_bootstrap.py",
        "runtime_bootstrap_code",
        RUNTIME_BOOTSTRAP_MODULE,
        False,
    ),
    "shared_contract_code": (
        "rfic_transformer_inverse_design/controlled_real10k_20k_contract.py",
        "shared_contract_code",
        "rfic_transformer_inverse_design.controlled_real10k_20k_contract",
        False,
    ),
    "splitter_code": (
        "rfic_transformer_inverse_design/model_splitting.py",
        "splitter_code",
        "rfic_transformer_inverse_design.model_splitting",
        False,
    ),
    "materialization_builder_code": (
        "controlled_entrypoints/build_controlled_real10k_20k_nested.py",
        "materialization_builder_code",
        None,
        False,
    ),
    "materialization_gate_code": (
        "controlled_entrypoints/run_controlled_real10k_20k_materialization.py",
        "materialization_gate_code",
        None,
        False,
    ),
    "runner_code": (
        "controlled_entrypoints/run_controlled_real10k_20k_paired.py",
        "runner_code",
        None,
        False,
    ),
    "trainer_code": (
        "controlled_entrypoints/train_physical_feature_tandem_inverse.py",
        "trainer_code",
        None,
        False,
    ),
    "evaluator_code": (
        "controlled_entrypoints/evaluate_controlled_real10k_20k_common.py",
        "evaluator_code",
        None,
        False,
    ),
    "native_smoke_test": (
        "controlled_entrypoints/controlled_real10k_20k_mars_native_smoke.py",
        "native_smoke_test",
        None,
        False,
    ),
}

ENTRYPOINT_BINDINGS: dict[str, tuple[str, str]] = {
    "materialization": (
        "materialization_gate_code",
        "runtime/project/scripts/run_controlled_real10k_20k_materialization.py",
    ),
    "runner": (
        "runner_code",
        "runtime/project/scripts/run_controlled_real10k_20k_paired.py",
    ),
    "trainer": (
        "trainer_code",
        "runtime/project/scripts/train_physical_feature_tandem_inverse.py",
    ),
    "evaluator": (
        "evaluator_code",
        "runtime/project/scripts/evaluate_controlled_real10k_20k_common.py",
    ),
    "native_smoke": (
        "native_smoke_test",
        "runtime/project/tests/controlled_real10k_20k_mars_native_smoke.py",
    ),
}

NO_AUTHORITY = {
    "native_linux_test_execution": False,
    "data_materialization": False,
    "training": False,
    "common_test_access": False,
    "numerical_metric_access": False,
    "fresh_emx": False,
    "process_signal": False,
}


class BundleBuildError(RuntimeError):
    """The runtime snapshot or its evidence contract is invalid."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("ascii")


def _canonical_json_sha(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    )


def _strict_json_object(payload: bytes, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise ValueError(f"duplicate/non-string key {key!r}")
            result[key] = value
        return result

    def reject_constant(raw: str) -> Any:
        raise ValueError(f"non-finite JSON constant {raw}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BundleBuildError(f"cannot parse {label}: {exc}") from exc
    if type(value) is not dict:
        raise BundleBuildError(f"{label} must be an exact JSON object")
    return value


def _exact_dict(value: Any, keys: Iterable[str], label: str) -> dict[str, Any]:
    expected = set(keys)
    if type(value) is not dict or set(value) != expected or any(
        type(key) is not str for key in value
    ):
        raise BundleBuildError(f"{label} keyset is not exact")
    return value


def _exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise BundleBuildError(f"{label} must be an exact nonempty string")
    return value


def _exact_sha(value: Any, label: str) -> str:
    if type(value) is not str or SHA_RE.fullmatch(value) is None:
        raise BundleBuildError(f"{label} must be an exact lowercase SHA-256")
    return value


def _safe_relative(value: Any, label: str) -> str:
    raw = _exact_string(value, label)
    if not raw.isascii() or raw.startswith("/") or "\\" in raw:
        raise BundleBuildError(f"{label} is not a portable relative path")
    path = PurePosixPath(raw)
    if path.as_posix() != raw or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleBuildError(f"{label} is not canonical")
    lower_parts = tuple(part.lower() for part in path.parts)
    if (
        "__pycache__" in lower_parts
        or path.name.lower() in FORBIDDEN_BASENAMES
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
    ):
        raise BundleBuildError(f"{label} is forbidden")
    return raw


def _canonical_existing_path(raw: Any, label: str) -> Path:
    text = _exact_string(raw, label)
    path = Path(text)
    if not path.is_absolute():
        raise BundleBuildError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BundleBuildError(f"{label} is missing: {path}") from exc
    if resolved != path or str(path) != text:
        raise BundleBuildError(f"{label} must be an exact canonical path")
    return path


def _canonical_absent_path(raw: Any, label: str) -> Path:
    text = _exact_string(raw, label)
    path = Path(text)
    if not path.is_absolute() or str(path) != text or path.name in {"", ".", ".."}:
        raise BundleBuildError(f"{label} must be an exact absolute path")
    parent = _canonical_existing_path(str(path.parent), f"{label} parent")
    if path != parent / path.name:
        raise BundleBuildError(f"{label} is not canonical")
    return path


def _open_canonical_directory(path: Path, label: str) -> int:
    """Open an absolute canonical directory one no-follow component at a time."""

    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise BundleBuildError(f"{label} is not a canonical directory")
    current = os.open(
        "/",
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        for part in path.parts[1:]:
            child = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current,
            )
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise BundleBuildError(f"{label} contains a nondirectory component")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _read_all_fd(descriptor: int, *, maximum: int, label: str) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        block = os.pread(descriptor, min(1024 * 1024, maximum + 1 - offset), offset)
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        offset += len(block)
        if offset > maximum:
            raise BundleBuildError(f"{label} exceeds the bounded size limit")


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _ensure_descriptor_capacity() -> tuple[int, int]:
    """Raise only this process's soft FD limit enough to hold both source trees."""

    required = 2 * MAX_TREE_FILES + 512
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    unlimited = getattr(resource, "RLIM_INFINITY", -1)
    hard_value = required if hard == unlimited else int(hard)
    if hard_value < required:
        raise BundleBuildError(
            f"descriptor hard limit {hard_value} is below required held-closure limit {required}"
        )
    if soft < required:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))
        except (OSError, ValueError) as exc:
            raise BundleBuildError("cannot raise process-local descriptor soft limit") from exc
    observed_soft, observed_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if observed_soft < required:
        raise BundleBuildError("process-local descriptor capacity remains insufficient")
    return int(observed_soft), int(observed_hard)


@dataclass
class HeldFile:
    path: Path
    parent_fd: int
    descriptor: int
    metadata: os.stat_result
    payload: bytes
    sha256: str

    @classmethod
    def open(
        cls,
        raw_path: Any,
        label: str,
        *,
        expected_sha256: str | None = None,
        executable: bool = False,
    ) -> "HeldFile":
        path = _canonical_existing_path(raw_path, label)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        parent_fd = _open_canonical_directory(path.parent, label + " parent")
        descriptor = -1
        try:
            before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise BundleBuildError(f"{label} must be regular, nonlinked, nlink=1")
            if executable and opened.st_mode & 0o111 == 0:
                raise BundleBuildError(f"{label} is not executable")
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise BundleBuildError(f"{label} changed while opening")
            payload = _read_all_fd(descriptor, maximum=MAX_SINGLE_FILE_BYTES, label=label)
            sha = _sha256_bytes(payload)
            if expected_sha256 is not None and sha != _exact_sha(expected_sha256, label + " SHA"):
                raise BundleBuildError(f"{label} SHA-256 mismatch")
            held = cls(path, parent_fd, descriptor, opened, payload, sha)
            held.verify(label)
            return held
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)
            raise

    def verify(self, label: str) -> None:
        current = os.fstat(self.descriptor)
        lexical = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        full_lexical = os.lstat(self.path)
        frozen = self.metadata
        identity = (frozen.st_dev, frozen.st_ino, frozen.st_size, frozen.st_mtime_ns, frozen.st_ctime_ns)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
            != identity
            or (lexical.st_dev, lexical.st_ino) != (current.st_dev, current.st_ino)
            or (full_lexical.st_dev, full_lexical.st_ino)
            != (current.st_dev, current.st_ino)
            or _sha256_fd(self.descriptor) != self.sha256
        ):
            raise BundleBuildError(f"{label} mutated or was replaced while held")

    def close(self) -> None:
        os.close(self.descriptor)
        os.close(self.parent_fd)


@dataclass
class HeldTreeFile:
    relative: str
    descriptor: int
    metadata: os.stat_result
    payload: bytes
    sha256: str

    def verify(self, label: str) -> None:
        current = os.fstat(self.descriptor)
        frozen = self.metadata
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
            != (frozen.st_dev, frozen.st_ino, frozen.st_size, frozen.st_mtime_ns, frozen.st_ctime_ns)
            or _sha256_fd(self.descriptor) != self.sha256
        ):
            raise BundleBuildError(f"{label} tree member mutated while held: {self.relative}")


@dataclass
class HeldTree:
    path: Path
    parent_fd: int
    descriptor: int
    metadata: os.stat_result
    files: list[HeldTreeFile]
    directories: set[str]

    @classmethod
    def open(cls, raw_path: Any, label: str) -> "HeldTree":
        path = _canonical_existing_path(raw_path, label)
        parent_fd = _open_canonical_directory(path.parent, label + " parent")
        descriptor = -1
        held_files: list[HeldTreeFile] = []
        try:
            before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            descriptor = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode) or (
                before.st_dev,
                before.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise BundleBuildError(f"{label} changed while opening")
            directories: set[str] = set()
            total = [0]
            cls._walk(descriptor, "", label, held_files, directories, total)
            result = cls(path, parent_fd, descriptor, opened, held_files, directories)
            result.verify(label)
            return result
        except BaseException:
            for item in held_files:
                os.close(item.descriptor)
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)
            raise

    @classmethod
    def _walk(
        cls,
        directory_fd: int,
        prefix: str,
        label: str,
        files: list[HeldTreeFile],
        directories: set[str],
        total: list[int],
    ) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise BundleBuildError(f"cannot list {label}") from exc
        for name in names:
            relative = name if not prefix else f"{prefix}/{name}"
            _safe_relative(relative, label + " member")
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child)
                    if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
                        raise BundleBuildError(f"{label} directory changed while opening")
                    directories.add(relative)
                    cls._walk(child, relative, label, files, directories, total)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise BundleBuildError(f"{label} contains a link or special file: {relative}")
            if len(files) >= MAX_TREE_FILES:
                raise BundleBuildError(f"{label} exceeds the bounded file-count limit")
            child = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                opened = os.fstat(child)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
                ):
                    raise BundleBuildError(f"{label} file changed while opening: {relative}")
                payload = _read_all_fd(child, maximum=MAX_SINGLE_FILE_BYTES, label=relative)
                total[0] += len(payload)
                if total[0] > MAX_TREE_BYTES:
                    raise BundleBuildError(f"{label} exceeds the bounded byte limit")
                files.append(
                    HeldTreeFile(relative, child, opened, payload, _sha256_bytes(payload))
                )
                child = -1
            finally:
                if child >= 0:
                    os.close(child)

    def verify(self, label: str) -> None:
        current = os.fstat(self.descriptor)
        lexical = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        full_lexical = os.lstat(self.path)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (self.metadata.st_dev, self.metadata.st_ino)
            or (lexical.st_dev, lexical.st_ino) != (current.st_dev, current.st_ino)
            or (full_lexical.st_dev, full_lexical.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise BundleBuildError(f"{label} root was replaced while held")
        for item in self.files:
            item.verify(label)
        # A second descriptor walk closes add/remove/rename races and extras.
        observed_files, observed_dirs = _inventory_tree_descriptor(self.descriptor, label)
        frozen = {
            item.relative: (item.sha256, len(item.payload), item.metadata.st_dev, item.metadata.st_ino)
            for item in self.files
        }
        if observed_files != frozen or observed_dirs != self.directories:
            raise BundleBuildError(f"{label} membership changed while held")

    def close(self) -> None:
        for item in self.files:
            os.close(item.descriptor)
        os.close(self.descriptor)
        os.close(self.parent_fd)


def _inventory_tree_descriptor(
    root_fd: int, label: str
) -> tuple[dict[str, tuple[str, int, int, int]], set[str]]:
    observed: dict[str, tuple[str, int, int, int]] = {}
    directories: set[str] = set()

    def walk(fd: int, prefix: str) -> None:
        for name in sorted(os.listdir(fd)):
            relative = name if not prefix else f"{prefix}/{name}"
            _safe_relative(relative, label + " member")
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    opened = os.fstat(child)
                    if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
                        raise BundleBuildError(f"{label} directory was swapped")
                    directories.add(relative)
                    walk(child, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                child = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    opened = os.fstat(child)
                    if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
                        raise BundleBuildError(f"{label} file was swapped")
                    observed[relative] = (
                        _sha256_fd(child),
                        opened.st_size,
                        opened.st_dev,
                        opened.st_ino,
                    )
                finally:
                    os.close(child)
            else:
                raise BundleBuildError(f"{label} contains a linked/special file")

    walk(root_fd, "")
    return observed, directories


@dataclass
class OutputRoot:
    path: Path
    parent_fd: int
    descriptor: int
    metadata: os.stat_result

    @classmethod
    def reserve(cls, raw_path: Any, label: str) -> "OutputRoot":
        path = _canonical_absent_path(raw_path, label)
        parent_fd = _open_canonical_directory(path.parent, label + " parent")
        descriptor = -1
        try:
            try:
                os.mkdir(path.name, WORK_DIRECTORY_MODE, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise BundleBuildError(f"{label} already exists (no-clobber)") from exc
            descriptor = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            metadata = os.fstat(descriptor)
            result = cls(path, parent_fd, descriptor, metadata)
            result.verify(label)
            return result
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)
            raise

    def verify(self, label: str) -> None:
        current = os.fstat(self.descriptor)
        lexical = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        full_lexical = os.lstat(self.path)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (self.metadata.st_dev, self.metadata.st_ino)
            or (lexical.st_dev, lexical.st_ino) != (current.st_dev, current.st_ino)
            or (full_lexical.st_dev, full_lexical.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise BundleBuildError(f"{label} root was replaced")

    def close(self) -> None:
        os.close(self.descriptor)
        os.close(self.parent_fd)


def _open_output_directory(root_fd: int, relative_parent: PurePosixPath, create: bool) -> int:
    current = os.dup(root_fd)
    try:
        for part in relative_parent.parts:
            if create:
                try:
                    os.mkdir(part, WORK_DIRECTORY_MODE, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current,
            )
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                raise BundleBuildError("output path component is not a directory")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _write_output_file(root_fd: int, relative: str, payload: bytes) -> dict[str, Any]:
    safe = PurePosixPath(_safe_relative(relative, "output path"))
    parent_fd = _open_output_directory(root_fd, safe.parent, True)
    descriptor = -1
    try:
        descriptor = os.open(
            safe.name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise BundleBuildError(f"short write for {relative}")
            offset += written
        os.fchmod(descriptor, FILE_MODE)
        _fsync_fd(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != FILE_MODE
            or metadata.st_size != len(payload)
            or _sha256_fd(descriptor) != _sha256_bytes(payload)
        ):
            raise BundleBuildError(f"output identity mismatch for {relative}")
        return {
            "path": relative,
            "sha256": _sha256_bytes(payload),
            "size_bytes": len(payload),
            "mode_octal": "0444",
            "nlink": 1,
        }
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _fsync_fd(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_output_tree(root_fd: int) -> None:
    def walk(fd: int) -> None:
        for name in sorted(os.listdir(fd)):
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    walk(child)
                    _fsync_fd(child)
                finally:
                    os.close(child)
        _fsync_fd(fd)

    walk(root_fd)


def _freeze_output_tree(root_fd: int) -> None:
    def walk(fd: int) -> None:
        for name in sorted(os.listdir(fd)):
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    walk(child)
                    os.fchmod(child, DIRECTORY_MODE)
                    _fsync_fd(child)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    opened = os.fstat(child)
                    if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
                        raise BundleBuildError("output file changed while freezing")
                    os.fchmod(child, FILE_MODE)
                    _fsync_fd(child)
                finally:
                    os.close(child)
            else:
                raise BundleBuildError("output tree contains a linked/special member")
        os.fchmod(fd, DIRECTORY_MODE)
        _fsync_fd(fd)

    walk(root_fd)


@dataclass(frozen=True)
class ElfDynamicIdentity:
    soname: str | None
    needed: tuple[str, ...]


def _elf_string(table: bytes, offset: int, label: str) -> str:
    if type(offset) is not int or offset < 0 or offset >= len(table):
        raise BundleBuildError(f"{label} has an invalid ELF string offset")
    end = table.find(b"\x00", offset)
    if end < 0:
        raise BundleBuildError(f"{label} ELF string is not NUL terminated")
    raw = table[offset:end]
    if not raw or len(raw) > 4096:
        raise BundleBuildError(f"{label} ELF string is empty/too long")
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BundleBuildError(f"{label} ELF identity is not ASCII") from exc
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise BundleBuildError(f"{label} ELF identity is not a basename")
    return value


def parse_elf64_x86_64_dynamic(payload: bytes, label: str) -> ElfDynamicIdentity:
    """Parse bounded ELF64 LE x86_64 ET_DYN DT_SONAME/DT_NEEDED records."""

    if type(payload) is not bytes or len(payload) < 64 or len(payload) > MAX_SINGLE_FILE_BYTES:
        raise BundleBuildError(f"{label} is not a bounded ELF payload")
    ident = payload[:16]
    if (
        ident[:4] != b"\x7fELF"
        or ident[4] != 2
        or ident[5] != 1
        or ident[6] != 1
        or any(ident[index] != 0 for index in range(9, 16))
    ):
        raise BundleBuildError(f"{label} is not ELF64 little-endian version 1")
    try:
        (
            _,
            elf_type,
            machine,
            version,
            _,
            program_offset,
            _,
            _,
            header_size,
            program_entry_size,
            program_count,
            _,
            _,
            _,
        ) = struct.unpack_from("<16sHHIQQQIHHHHHH", payload, 0)
    except struct.error as exc:
        raise BundleBuildError(f"{label} has a truncated ELF header") from exc
    if elf_type != 3 or machine != 62 or version != 1 or header_size != 64:
        raise BundleBuildError(f"{label} is not x86_64 ET_DYN")
    if (
        program_entry_size != 56
        or program_count < 1
        or program_count > MAX_ELF_PROGRAM_HEADERS
        or program_offset < header_size
        or program_offset + program_entry_size * program_count > len(payload)
    ):
        raise BundleBuildError(f"{label} has invalid program headers")

    loads: list[tuple[int, int, int]] = []
    dynamics: list[tuple[int, int]] = []
    for index in range(program_count):
        offset = program_offset + index * program_entry_size
        try:
            p_type, _, p_offset, p_vaddr, _, p_filesz, p_memsz, _ = struct.unpack_from(
                "<IIQQQQQQ", payload, offset
            )
        except struct.error as exc:
            raise BundleBuildError(f"{label} has a truncated program header") from exc
        if p_filesz > p_memsz or p_offset > len(payload) or p_filesz > len(payload) - p_offset:
            raise BundleBuildError(f"{label} has an out-of-bounds segment")
        if p_type == 1:
            loads.append((p_vaddr, p_filesz, p_offset))
        elif p_type == 2:
            dynamics.append((p_offset, p_filesz))
    if not loads or len(dynamics) != 1:
        raise BundleBuildError(f"{label} must have load segments and exactly one PT_DYNAMIC")
    dynamic_offset, dynamic_size = dynamics[0]
    if (
        dynamic_size < 16
        or dynamic_size % 16 != 0
        or dynamic_size // 16 > MAX_ELF_DYNAMIC_ENTRIES
    ):
        raise BundleBuildError(f"{label} has an invalid PT_DYNAMIC size")
    entries: list[tuple[int, int]] = []
    found_null = False
    for offset in range(dynamic_offset, dynamic_offset + dynamic_size, 16):
        tag, value = struct.unpack_from("<qQ", payload, offset)
        if tag == 0:
            found_null = True
            break
        entries.append((tag, value))
    if not found_null:
        raise BundleBuildError(f"{label} has no bounded DT_NULL")

    def exactly_one(tag: int, name: str) -> int:
        values = [value for item_tag, value in entries if item_tag == tag]
        if len(values) != 1:
            raise BundleBuildError(f"{label} must have exactly one {name}")
        return values[0]

    string_vaddr = exactly_one(5, "DT_STRTAB")
    string_size = exactly_one(10, "DT_STRSZ")
    if string_size < 1 or string_size > MAX_ELF_STRING_TABLE:
        raise BundleBuildError(f"{label} has an invalid DT_STRSZ")
    matches: list[int] = []
    for vaddr, file_size, file_offset in loads:
        if string_vaddr >= vaddr and string_vaddr - vaddr <= file_size:
            delta = string_vaddr - vaddr
            if string_size <= file_size - delta and file_offset + delta + string_size <= len(payload):
                matches.append(file_offset + delta)
    if len(matches) != 1:
        raise BundleBuildError(f"{label} DT_STRTAB cannot be mapped uniquely")
    table = payload[matches[0] : matches[0] + string_size]
    soname_offsets = [value for tag, value in entries if tag == 14]
    if len(soname_offsets) > 1:
        raise BundleBuildError(f"{label} has duplicate DT_SONAME")
    soname = _elf_string(table, soname_offsets[0], label) if soname_offsets else None
    needed_values = [_elf_string(table, value, label) for tag, value in entries if tag == 1]
    return ElfDynamicIdentity(soname, tuple(sorted(set(needed_values))))


def _numpy_module(relative: str) -> tuple[str, bool] | None:
    if not relative.endswith(".py"):
        return None
    parts = relative.split("/")
    if parts[-1] == "__init__.py":
        module_parts = ["numpy", *parts[:-1]]
        is_package = True
    else:
        module_parts = ["numpy", *parts[:-1], parts[-1][:-3]]
        is_package = False
    if any(not part.isidentifier() for part in module_parts):
        raise BundleBuildError(f"NumPy Python member has a non-module path: {relative}")
    return ".".join(module_parts), is_package


def _numpy_version(files: Mapping[str, HeldTreeFile]) -> str:
    record = files.get("version.py")
    if record is None:
        raise BundleBuildError("NumPy package lacks version.py")
    try:
        tree = ast.parse(record.payload.decode("utf-8"), filename="numpy/version.py")
    except (UnicodeError, SyntaxError) as exc:
        raise BundleBuildError("NumPy version.py is invalid") from exc
    values: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "version" for target in node.targets
        ) and isinstance(node.value, ast.Constant) and type(node.value.value) is str:
            values.append(node.value.value)
    if len(values) != 1:
        raise BundleBuildError("NumPy version.py does not have one literal version assignment")
    return values[0]


def _extension_module(relative: str, suffixes: Sequence[str]) -> str:
    matched = next((suffix for suffix in suffixes if relative.endswith(suffix)), None)
    if matched is None:
        raise BundleBuildError(f"NumPy native member has no accepted extension suffix: {relative}")
    stem = relative[: -len(matched)]
    parts = ["numpy", *stem.split("/")]
    if any(not part.isidentifier() for part in parts):
        raise BundleBuildError(f"NumPy extension has a non-module path: {relative}")
    return ".".join(parts)


def _deterministic_zip(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.comment = b""
        for path in sorted(entries):
            _safe_relative(path, "ZIP member")
            info = zipfile.ZipInfo(path, ZIP_TIMESTAMP)
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = ZIP_EXTERNAL_ATTR
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, entries[path])
    return buffer.getvalue()


def _dependency_order(libraries: Mapping[str, ElfDynamicIdentity]) -> list[str]:
    vendored = set(libraries)
    state: dict[str, int] = {}
    result: list[str] = []

    def visit(soname: str) -> None:
        marker = state.get(soname, 0)
        if marker == 1:
            raise BundleBuildError("vendored ELF dependency graph contains a cycle")
        if marker == 2:
            return
        state[soname] = 1
        for needed in libraries[soname].needed:
            if needed in vendored:
                visit(needed)
        state[soname] = 2
        result.append(soname)

    for soname in sorted(vendored):
        visit(soname)
    return result


def _parse_input_spec(payload: bytes) -> dict[str, Any]:
    spec = _exact_dict(
        _strict_json_object(payload, "runtime bundle input spec"),
        {
            "schema",
            "build_identity",
            "python",
            "numpy",
            "project_sources",
            "package_extra_file_roles",
        },
        "runtime bundle input spec",
    )
    if spec["schema"] != INPUT_SPEC_SCHEMA:
        raise BundleBuildError("runtime bundle input spec schema mismatch")
    _exact_string(spec["build_identity"], "build_identity")
    python = _exact_dict(
        spec["python"],
        {"executable_path", "executable_sha256", "implementation", "version", "abi_tag", "platform"},
        "python identity",
    )
    _canonical_existing_path(python["executable_path"], "Python executable")
    _exact_sha(python["executable_sha256"], "Python executable SHA")
    for key in ("implementation", "version", "abi_tag", "platform"):
        _exact_string(python[key], f"python.{key}")
    numpy = _exact_dict(spec["numpy"], {"package_root", "libraries_root", "version"}, "NumPy identity")
    package_root = _canonical_existing_path(numpy["package_root"], "NumPy package root")
    libraries_root = _canonical_existing_path(numpy["libraries_root"], "NumPy libraries root")
    if (
        package_root.name != "numpy"
        or libraries_root.name != "numpy.libs"
        or package_root.parent != libraries_root.parent
    ):
        raise BundleBuildError("NumPy roots do not have the canonical wheel basenames")
    _exact_string(numpy["version"], "numpy.version")
    if type(spec["project_sources"]) is not dict or set(spec["project_sources"]) != PROJECT_SOURCE_ROLES:
        raise BundleBuildError("project source role set is not exact")
    if type(spec["package_extra_file_roles"]) is not dict or set(spec["package_extra_file_roles"]) != PACKAGE_EXTRA_FILE_ROLES:
        raise BundleBuildError("package extra role set is not exact")
    for group_name in ("project_sources", "package_extra_file_roles"):
        for role, raw in spec[group_name].items():
            entry = _exact_dict(raw, {"path", "sha256"}, f"{group_name}.{role}")
            _canonical_existing_path(entry["path"], f"{group_name}.{role}.path")
            _exact_sha(entry["sha256"], f"{group_name}.{role}.sha256")
    if (
        spec["package_extra_file_roles"]["package_builder_code"]["sha256"]
        != FROZEN_PACKAGE_BUILDER_SHA256
    ):
        raise BundleBuildError("package-v5 requires the frozen package builder identity")
    return spec


def _require_isolation_flags() -> None:
    flags = sys.flags
    if not (
        flags.isolated == 1
        and flags.ignore_environment == 1
        and flags.no_site == 1
        and flags.no_user_site == 1
        and flags.dont_write_bytecode == 1
        and getattr(flags, "safe_path", False)
    ):
        raise BundleBuildError("production generator requires exact CPython -I -B -S isolation")


def _verify_isolated_runtime(spec: Mapping[str, Any], executable: HeldFile) -> None:
    _require_isolation_flags()
    if "site" in sys.modules or any(name == "numpy" or name.startswith("numpy.") for name in sys.modules):
        raise BundleBuildError("site/NumPy was loaded before runtime snapshot")
    expected = spec["python"]
    observed_executable = Path(sys.executable).resolve(strict=True)
    if observed_executable != executable.path or executable.sha256 != expected["executable_sha256"]:
        raise BundleBuildError("executing Python does not match the pinned canonical executable")
    observed = {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "abi_tag": sysconfig.get_config_var("SOABI"),
        "platform": sysconfig.get_platform(),
    }
    if any(type(observed[key]) is not str or observed[key] != expected[key] for key in observed):
        raise BundleBuildError("executing Python identity does not match the input spec")
    if expected["implementation"] != "CPython" or expected["version"] != PYTHON_VERSION:
        raise BundleBuildError("package-v5 requires canonical CPython 3.12.13")
    if not sys.platform.startswith("linux") or platform.machine() != "x86_64":
        raise BundleBuildError("runtime bundle production is restricted to MARS Linux x86_64")


def _source_member(
    role: str, held: HeldFile
) -> tuple[dict[str, Any], tuple[str, bytes]]:
    member_path, closure_role, module, is_package = PROJECT_MEMBER_BINDINGS[role]
    return (
        {
            "path": member_path,
            "sha256": held.sha256,
            "size_bytes": len(held.payload),
            "kind": "python_source",
            "module": module,
            "is_package": is_package,
            "role": closure_role,
        },
        (member_path, held.payload),
    )


def _build_runtime_artifacts(
    spec: Mapping[str, Any],
    project: Mapping[str, HeldFile],
    numpy_tree: HeldTree,
    libs_tree: HeldTree,
) -> tuple[bytes, dict[str, Any], dict[str, bytes], dict[str, Any]]:
    if project["runtime_package_init_code"].payload != b"":
        raise BundleBuildError("controlled runtime package initializer must be exactly zero bytes")
    for role, held in project.items():
        try:
            ast.parse(held.payload.decode("utf-8"), filename=str(held.path))
        except (UnicodeError, SyntaxError) as exc:
            raise BundleBuildError(f"project Python source is invalid: {role}") from exc

    numpy_files = {item.relative: item for item in numpy_tree.files}
    if "__init__.py" not in numpy_files or _numpy_version(numpy_files) != spec["numpy"]["version"]:
        raise BundleBuildError("NumPy package/version identity mismatch")
    if spec["numpy"]["version"] != NUMPY_VERSION:
        raise BundleBuildError("package-v5 requires NumPy 2.5.0")
    if not libs_tree.files or libs_tree.directories:
        raise BundleBuildError("numpy.libs must be a nonempty flat exact library root")

    suffixes = sorted(importlib.machinery.EXTENSION_SUFFIXES, key=len, reverse=True)
    zip_entries: dict[str, bytes] = {}
    members: list[dict[str, Any]] = []
    native_extension_sources: list[tuple[str, HeldTreeFile, ElfDynamicIdentity]] = []
    for relative, item in sorted(numpy_files.items()):
        if relative.endswith(".so"):
            module = _extension_module(relative, suffixes)
            native_extension_sources.append(
                (module, item, parse_elf64_x86_64_dynamic(item.payload, f"NumPy extension {relative}"))
            )
            continue
        archive_path = f"numpy/{relative}"
        module_identity = _numpy_module(relative)
        if module_identity is None:
            module, is_package, kind = None, False, "data"
        else:
            module, is_package = module_identity
            kind = "python_source"
        zip_entries[archive_path] = item.payload
        members.append(
            {
                "path": archive_path,
                "sha256": item.sha256,
                "size_bytes": len(item.payload),
                "kind": kind,
                "module": module,
                "is_package": is_package,
                "role": "numpy_pure",
            }
        )

    for role in sorted(PROJECT_SOURCE_ROLES):
        member, entry = _source_member(role, project[role])
        if entry[0] in zip_entries:
            raise BundleBuildError("project/NumPy ZIP member collision")
        members.append(member)
        zip_entries[entry[0]] = entry[1]
    members.sort(key=lambda item: item["path"])
    modules = [item["module"] for item in members if item["module"] is not None]
    if len(modules) != len(set(modules)):
        raise BundleBuildError("pure runtime module identities are not unique")
    archive = _deterministic_zip(zip_entries)

    library_sources: dict[str, tuple[HeldTreeFile, ElfDynamicIdentity]] = {}
    for item in libs_tree.files:
        identity = parse_elf64_x86_64_dynamic(item.payload, f"numpy.libs {item.relative}")
        if identity.soname is None:
            raise BundleBuildError(f"numpy.libs member lacks DT_SONAME: {item.relative}")
        if identity.soname in library_sources:
            raise BundleBuildError("numpy.libs contains duplicate SONAMEs")
        library_sources[identity.soname] = (item, identity)
    dependency_identities = {soname: item[1] for soname, item in library_sources.items()}
    order = _dependency_order(dependency_identities)
    vendored = set(order)
    unresolved = {
        needed
        for _, identity in [*library_sources.values()]
        for needed in identity.needed
        if needed not in vendored
    } | {
        needed
        for _, _, identity in native_extension_sources
        for needed in identity.needed
        if needed not in vendored
    }
    forbidden = unresolved - FROZEN_SYSTEM_LIBRARY_POLICY
    if forbidden:
        raise BundleBuildError(f"native dependency is outside the frozen system allowlist: {sorted(forbidden)}")
    # The bootstrap contract binds the reviewed host-trust boundary itself,
    # not a result-dependent subset.  Every dependency outside this exact
    # boundary must therefore be present in ``numpy.libs``.
    system_allowlist = sorted(FROZEN_SYSTEM_LIBRARY_POLICY)

    tree_payloads: dict[str, bytes] = {"pure/RUNTIME_PURE.zip": archive}
    native_libraries: list[dict[str, Any]] = []
    for load_order, soname in enumerate(order):
        source, identity = library_sources[soname]
        basename = PurePosixPath(source.relative).name
        relative = f"native/libraries/{soname}/{basename}"
        _safe_relative(relative, "native library destination")
        if relative in tree_payloads:
            raise BundleBuildError("native library destination collision")
        tree_payloads[relative] = source.payload
        native_libraries.append(
            {
                "soname": soname,
                "path": relative,
                "basename": basename,
                "sha256": source.sha256,
                "size_bytes": len(source.payload),
                "dt_needed": list(identity.needed),
                "load_order": load_order,
            }
        )

    native_extensions: list[dict[str, Any]] = []
    seen_extension_modules: set[str] = set()
    for module, source, identity in sorted(native_extension_sources, key=lambda item: item[0]):
        if module in seen_extension_modules or module in modules:
            raise BundleBuildError("native/pure module identity collision")
        seen_extension_modules.add(module)
        basename = PurePosixPath(source.relative).name
        relative = f"native/extensions/{module}/{basename}"
        _safe_relative(relative, "native extension destination")
        if relative in tree_payloads:
            raise BundleBuildError("native extension destination collision")
        tree_payloads[relative] = source.payload
        native_extensions.append(
            {
                "module": module,
                "path": relative,
                "basename": basename,
                "sha256": source.sha256,
                "size_bytes": len(source.payload),
                "init_symbol": "PyInit_" + module.rsplit(".", 1)[-1],
                "dt_needed": list(identity.needed),
            }
        )

    entrypoints: dict[str, Any] = {}
    for name, (role, display_path) in ENTRYPOINT_BINDINGS.items():
        member_path, closure_role, _, _ = PROJECT_MEMBER_BINDINGS[role]
        entrypoints[name] = {
            "member": member_path,
            "sha256": project[role].sha256,
            "display_path": display_path,
            "role": closure_role,
        }
    closure = {
        "schema": RUNTIME_CLOSURE_SCHEMA,
        "bootstrap": {
            "module": RUNTIME_BOOTSTRAP_MODULE,
            "sha256": project["runtime_bootstrap_code"].sha256,
            "size_bytes": len(project["runtime_bootstrap_code"].payload),
        },
        "python": {
            "implementation": spec["python"]["implementation"],
            "version": spec["python"]["version"],
            "abi_tag": spec["python"]["abi_tag"],
            "platform": spec["python"]["platform"],
            "executable_sha256": spec["python"]["executable_sha256"],
        },
        "numpy": {"version": spec["numpy"]["version"]},
        "pure_archive": {
            "path": "pure/RUNTIME_PURE.zip",
            "sha256": _sha256_bytes(archive),
            "size_bytes": len(archive),
            "format": "zip",
            "compression": "ZIP_STORED",
        },
        "members": members,
        "native_extensions": native_extensions,
        "native_libraries": native_libraries,
        "system_library_allowlist": system_allowlist,
        "entrypoints": entrypoints,
    }
    inventory = {
        "numpy_package": [
            {"path": item.relative, "sha256": item.sha256, "size_bytes": len(item.payload)}
            for item in sorted(numpy_tree.files, key=lambda item: item.relative)
        ],
        "numpy_libraries": [
            {"path": item.relative, "sha256": item.sha256, "size_bytes": len(item.payload)}
            for item in sorted(libs_tree.files, key=lambda item: item.relative)
        ],
    }
    return archive, closure, tree_payloads, inventory


def _package_build_spec(
    spec: Mapping[str, Any],
    held_roles: Mapping[str, HeldFile],
    bundle_root: Path,
    closure_sha: str,
) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    package_builder = held_roles.get("package_builder_code")
    if (
        package_builder is None
        or package_builder.sha256 != FROZEN_PACKAGE_BUILDER_SHA256
    ):
        raise BundleBuildError("package-v5 build spec requires the frozen package builder")
    source_shas = {held.sha256 for held in held_roles.values()}
    if len(source_shas) != len(held_roles) or closure_sha in source_shas:
        raise BundleBuildError("package-v5 file role SHA-256 identities are not unique")
    for role in sorted(PROJECT_SOURCE_ROLES | PACKAGE_EXTRA_FILE_ROLES):
        held = held_roles[role]
        roles[role] = {"kind": "file", "source_path": str(held.path), "sha256": held.sha256}
    closure_path = bundle_root / RUNTIME_CLOSURE_RELATIVE
    tree_path = bundle_root / RUNTIME_TREE_RELATIVE
    roles["runtime_dependency_closure_json"] = {
        "kind": "file",
        "source_path": str(closure_path),
        "sha256": closure_sha,
    }
    roles["runtime_dependency_closure_tree"] = {
        "kind": "tree",
        "source_root": str(tree_path),
        "inventory_path": str(closure_path),
        "inventory_sha256": closure_sha,
    }
    if set(roles) != ALL_PACKAGE_ROLES or len(roles) != 21:
        raise BundleBuildError("generated package build spec does not contain exactly 21 roles")
    return {"schema": PACKAGE_BUILD_SPEC_SCHEMA, "package_version": PACKAGE_VERSION, "roles": roles}


def _load_bootstrap_for_audit(bootstrap: HeldFile) -> Any:
    if any(name == "numpy" or name.startswith("numpy.") for name in sys.modules):
        raise BundleBuildError("NumPy was imported before closure self-audit")
    name = "_controlled_real10k_20k_runtime_bootstrap_bundle_audit"
    loader = importlib.machinery.SourceFileLoader(name, f"/proc/self/fd/{bootstrap.descriptor}")
    module_spec = importlib.util.spec_from_loader(name, loader)
    if module_spec is None:
        raise BundleBuildError("cannot create runtime bootstrap audit module spec")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if any(key == "numpy" or key.startswith("numpy.") for key in sys.modules):
        raise BundleBuildError("runtime bootstrap self-audit imported NumPy")
    return module


def _sha_index(records: Sequence[Mapping[str, Any]]) -> bytes:
    paths = [record["path"] for record in records]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise BundleBuildError("SHA index records must be sorted and unique")
    return "".join(f"{record['sha256']}  {record['path']}\n" for record in records).encode("ascii")


def _verify_output_records(root_fd: int, expected: Mapping[str, str]) -> None:
    observed, _ = _inventory_tree_descriptor(root_fd, "output bundle")
    observed_simple = {path: value[0] for path, value in observed.items()}
    if observed_simple != dict(expected):
        raise BundleBuildError(
            "output bundle set/identity mismatch: "
            f"missing={sorted(set(expected) - set(observed_simple))} "
            f"extra={sorted(set(observed_simple) - set(expected))}"
        )

    def verify_file_modes(fd: int) -> None:
        for name in sorted(os.listdir(fd)):
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    verify_file_modes(child)
                finally:
                    os.close(child)
            elif (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != FILE_MODE
            ):
                raise BundleBuildError("output file mode/link identity is not immutable")

    verify_file_modes(root_fd)


def _verify_frozen_output_tree(root_fd: int) -> None:
    root_metadata = os.fstat(root_fd)
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_IMODE(root_metadata.st_mode) != DIRECTORY_MODE:
        raise BundleBuildError("frozen output root mode is not exact")

    def walk(fd: int) -> None:
        for name in sorted(os.listdir(fd)):
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                if stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE:
                    raise BundleBuildError("frozen output directory mode is not exact")
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    walk(child)
                finally:
                    os.close(child)
            elif (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != FILE_MODE
            ):
                raise BundleBuildError("frozen output file identity is not exact")

    walk(root_fd)


def _build_success(
    spec: Mapping[str, Any],
    spec_file: HeldFile,
    generator: HeldFile,
    executable: HeldFile,
    project: Mapping[str, HeldFile],
    extras: Mapping[str, HeldFile],
    numpy_tree: HeldTree,
    libs_tree: HeldTree,
    bundle: OutputRoot,
    attempt: OutputRoot,
) -> dict[str, Any]:
    archive, closure, tree_payloads, source_inventory = _build_runtime_artifacts(
        spec, project, numpy_tree, libs_tree
    )
    closure_payload = _json_bytes(closure)
    closure_sha = _sha256_bytes(closure_payload)
    package_spec = _package_build_spec(
        spec, {**project, **extras}, bundle.path, closure_sha
    )
    package_spec_payload = _json_bytes(package_spec)

    records: list[dict[str, Any]] = []
    for relative, payload in sorted(tree_payloads.items()):
        records.append(
            _write_output_file(bundle.descriptor, f"runtime/dependencies/{relative}", payload)
        )
    records.append(_write_output_file(bundle.descriptor, RUNTIME_CLOSURE_RELATIVE, closure_payload))
    records.append(_write_output_file(bundle.descriptor, PACKAGE_SPEC_RELATIVE, package_spec_payload))

    # Invoke the exact pinned bootstrap audit implementation.  It reads only
    # the emitted closure and tree and must not import NumPy.
    bundle.verify("runtime bundle output")
    for held in [spec_file, generator, executable, *project.values(), *extras.values()]:
        held.verify(str(held.path))
    numpy_tree.verify("NumPy package root")
    libs_tree.verify("NumPy libraries root")
    bootstrap_module = _load_bootstrap_for_audit(project["runtime_bootstrap_code"])
    try:
        self_audit = bootstrap_module.audit_runtime_closure_paths(
            bundle.path / RUNTIME_CLOSURE_RELATIVE,
            closure_sha,
            bundle.path / RUNTIME_TREE_RELATIVE,
            project["runtime_bootstrap_code"].path,
            project["runtime_bootstrap_code"].sha256,
        )
    except BaseException as exc:
        raise BundleBuildError(f"runtime bootstrap path self-audit failed: {exc}") from exc
    finally:
        sys.modules.pop("_controlled_real10k_20k_runtime_bootstrap_bundle_audit", None)
    if any(name == "numpy" or name.startswith("numpy.") for name in sys.modules):
        raise BundleBuildError("NumPy was imported during closure generation")
    bundle.verify("runtime bundle output")
    for held in [spec_file, generator, executable, *project.values(), *extras.values()]:
        held.verify(str(held.path))
    numpy_tree.verify("NumPy package root")
    libs_tree.verify("NumPy libraries root")

    receipt = {
        "schema": BUNDLE_RECEIPT_SCHEMA,
        "status": "PASS_PREPARED_RESULT_BLIND",
        "build_identity": spec["build_identity"],
        "input_spec": {"path": str(spec_file.path), "sha256": spec_file.sha256, "size_bytes": len(spec_file.payload)},
        "generator": {"path": str(generator.path), "sha256": generator.sha256, "size_bytes": len(generator.payload)},
        "python_executable": {"path": str(executable.path), "sha256": executable.sha256, "size_bytes": len(executable.payload)},
        "output_identity": {
            "path": str(bundle.path),
            "device": int(os.fstat(bundle.descriptor).st_dev),
            "inode": int(os.fstat(bundle.descriptor).st_ino),
        },
        "source_inventory": {
            "numpy_package": {
                "root": str(numpy_tree.path),
                "device": int(numpy_tree.metadata.st_dev),
                "inode": int(numpy_tree.metadata.st_ino),
                "file_count": len(numpy_tree.files),
                "canonical_inventory_sha256": _canonical_json_sha(source_inventory["numpy_package"]),
            },
            "numpy_libraries": {
                "root": str(libs_tree.path),
                "device": int(libs_tree.metadata.st_dev),
                "inode": int(libs_tree.metadata.st_ino),
                "file_count": len(libs_tree.files),
                "canonical_inventory_sha256": _canonical_json_sha(source_inventory["numpy_libraries"]),
            },
            "project_roles": {
                role: {
                    "path": str(held.path),
                    "sha256": held.sha256,
                    "size_bytes": len(held.payload),
                    "device": int(held.metadata.st_dev),
                    "inode": int(held.metadata.st_ino),
                    "nlink": int(held.metadata.st_nlink),
                }
                for role, held in sorted(project.items())
            },
            "package_extra_file_roles": {
                role: {
                    "path": str(held.path),
                    "sha256": held.sha256,
                    "size_bytes": len(held.payload),
                    "device": int(held.metadata.st_dev),
                    "inode": int(held.metadata.st_ino),
                    "nlink": int(held.metadata.st_nlink),
                }
                for role, held in sorted(extras.items())
            },
        },
        "runtime_closure": {
            "path": RUNTIME_CLOSURE_RELATIVE,
            "sha256": closure_sha,
            "pure_archive_sha256": _sha256_bytes(archive),
            "member_count": len(closure["members"]),
            "native_extension_count": len(closure["native_extensions"]),
            "native_library_count": len(closure["native_libraries"]),
            "system_library_allowlist": closure["system_library_allowlist"],
        },
        "package_build_spec": {
            "path": PACKAGE_SPEC_RELATIVE,
            "sha256": _sha256_bytes(package_spec_payload),
            "role_count": 21,
        },
        "self_audit": self_audit,
        "authorities": dict(NO_AUTHORITY),
    }
    receipt_payload = _json_bytes(receipt)
    records.append(_write_output_file(bundle.descriptor, BUNDLE_RECEIPT_RELATIVE, receipt_payload))
    records.sort(key=lambda item: item["path"])
    index_payload = _sha_index(records)
    index_record = _write_output_file(bundle.descriptor, SHA_INDEX_RELATIVE, index_payload)

    # Durability checks intentionally precede publication of either terminal
    # commit.  A failure here can only produce a FAIL closure, never PASS.
    _fsync_output_tree(bundle.descriptor)
    _fsync_fd(bundle.parent_fd)
    bundle.verify("runtime bundle output")
    expected_before_commit = {item["path"]: item["sha256"] for item in [*records, index_record]}
    _verify_output_records(bundle.descriptor, expected_before_commit)
    commit = {
        "schema": BUNDLE_COMMIT_SCHEMA,
        "status": "PASS",
        "build_identity": spec["build_identity"],
        "output_identity": {
            "path": str(bundle.path),
            "device": int(os.fstat(bundle.descriptor).st_dev),
            "inode": int(os.fstat(bundle.descriptor).st_ino),
        },
        "runtime_closure": {"path": RUNTIME_CLOSURE_RELATIVE, "sha256": closure_sha},
        "package_build_spec": {"path": PACKAGE_SPEC_RELATIVE, "sha256": _sha256_bytes(package_spec_payload)},
        "build_receipt": {"path": BUNDLE_RECEIPT_RELATIVE, "sha256": _sha256_bytes(receipt_payload)},
        "sha256sums": {"path": SHA_INDEX_RELATIVE, "sha256": _sha256_bytes(index_payload)},
        "required_external_pass_receipt": {
            "path": str(attempt.path / ATTEMPT_RECEIPT_RELATIVE),
            "schema": ATTEMPT_RECEIPT_SCHEMA,
            "status": "PASS",
        },
        "creation_order_contract": {
            "this_member_created_last": True,
            "post_commit_bundle_file_creation_permitted": False,
        },
        "authorities": dict(NO_AUTHORITY),
    }
    commit_payload = _json_bytes(commit)
    commit_record = _write_output_file(bundle.descriptor, BUNDLE_COMMIT_RELATIVE, commit_payload)
    _fsync_output_tree(bundle.descriptor)
    _fsync_fd(bundle.parent_fd)
    bundle.verify("runtime bundle output")
    _verify_output_records(
        bundle.descriptor,
        {**expected_before_commit, commit_record["path"]: commit_record["sha256"]},
    )
    _freeze_output_tree(bundle.descriptor)
    _verify_frozen_output_tree(bundle.descriptor)
    _fsync_fd(bundle.parent_fd)

    attempt_receipt = {
        "schema": ATTEMPT_RECEIPT_SCHEMA,
        "status": "PASS",
        "build_identity": spec["build_identity"],
        "bundle_root": str(bundle.path),
        "bundle_output_identity": {
            "device": int(os.fstat(bundle.descriptor).st_dev),
            "inode": int(os.fstat(bundle.descriptor).st_ino),
        },
        "attempt_output_identity": {
            "device": int(os.fstat(attempt.descriptor).st_dev),
            "inode": int(os.fstat(attempt.descriptor).st_ino),
        },
        "bundle_commit": {"path": str(bundle.path / BUNDLE_COMMIT_RELATIVE), "sha256": commit_record["sha256"]},
        "runtime_closure": {"path": str(bundle.path / RUNTIME_CLOSURE_RELATIVE), "sha256": closure_sha},
        "package_build_spec": {"path": str(bundle.path / PACKAGE_SPEC_RELATIVE), "sha256": _sha256_bytes(package_spec_payload)},
        "authorities": dict(NO_AUTHORITY),
    }
    attempt_receipt_payload = _json_bytes(attempt_receipt)
    attempt_record = _write_output_file(attempt.descriptor, ATTEMPT_RECEIPT_RELATIVE, attempt_receipt_payload)
    attempt_index_payload = _sha_index([attempt_record])
    attempt_index_record = _write_output_file(attempt.descriptor, SHA_INDEX_RELATIVE, attempt_index_payload)
    _fsync_output_tree(attempt.descriptor)
    _fsync_fd(attempt.parent_fd)
    attempt.verify("runtime bundle attempt output")
    attempt_commit = {
        "schema": ATTEMPT_COMMIT_SCHEMA,
        "status": "PASS",
        "build_identity": spec["build_identity"],
        "attempt_receipt": {"path": ATTEMPT_RECEIPT_RELATIVE, "sha256": attempt_record["sha256"]},
        "sha256sums": {"path": SHA_INDEX_RELATIVE, "sha256": attempt_index_record["sha256"]},
        "bundle_commit": {"path": str(bundle.path / BUNDLE_COMMIT_RELATIVE), "sha256": commit_record["sha256"]},
        "creation_order_contract": {
            "this_member_created_last": True,
            "post_commit_attempt_file_creation_permitted": False,
        },
        "authorities": dict(NO_AUTHORITY),
    }
    attempt_commit_record = _write_output_file(
        attempt.descriptor, ATTEMPT_COMMIT_RELATIVE, _json_bytes(attempt_commit)
    )
    _fsync_output_tree(attempt.descriptor)
    _fsync_fd(attempt.parent_fd)
    _freeze_output_tree(attempt.descriptor)
    _verify_frozen_output_tree(attempt.descriptor)
    _fsync_fd(attempt.parent_fd)
    return {
        "bundle_commit_sha256": commit_record["sha256"],
        "attempt_commit_sha256": attempt_commit_record["sha256"],
        "runtime_closure_sha256": closure_sha,
        "package_build_spec_sha256": _sha256_bytes(package_spec_payload),
    }


def _best_effort_failure_closure(
    root: OutputRoot | None,
    *,
    error: BaseException,
    build_identity: str,
    label: str,
) -> None:
    if root is None:
        return
    try:
        root.verify(label)
        existing = sorted(os.listdir(root.descriptor))
        failure = {
            "schema": FAILURE_SCHEMA,
            "status": "FAIL",
            "build_identity": build_identity,
            "error_type": type(error).__name__,
            "error": str(error),
            "partial_members": existing,
            "success_authority": False,
            "authorities": dict(NO_AUTHORITY),
        }
        failure_record = _write_output_file(root.descriptor, FAILURE_RELATIVE, _json_bytes(failure))
        index_payload = _sha_index([failure_record])
        index_record = _write_output_file(root.descriptor, FAILURE_SHA_INDEX_RELATIVE, index_payload)
        _fsync_output_tree(root.descriptor)
        _fsync_fd(root.parent_fd)
        terminal = {
            "schema": FAILURE_COMMIT_SCHEMA,
            "status": "FAIL",
            "build_identity": build_identity,
            "failure": {"path": FAILURE_RELATIVE, "sha256": failure_record["sha256"]},
            "sha256sums": {"path": FAILURE_SHA_INDEX_RELATIVE, "sha256": index_record["sha256"]},
            "success_authority": False,
            "authorities": dict(NO_AUTHORITY),
        }
        _write_output_file(root.descriptor, FAILURE_COMMIT_RELATIVE, _json_bytes(terminal))
        _fsync_output_tree(root.descriptor)
        _freeze_output_tree(root.descriptor)
        _verify_frozen_output_tree(root.descriptor)
        _fsync_fd(root.parent_fd)
    except BaseException:
        # The original error remains authoritative; never overwrite or delete a
        # partial create-once evidence directory while trying to report it.
        return


def build_from_cli(
    *,
    input_spec_path: str,
    expected_input_spec_sha256: str,
    bundle_dir: str,
    attempt_receipt_dir: str,
    expected_generator_sha256: str,
) -> dict[str, Any]:
    attempt: OutputRoot | None = None
    bundle: OutputRoot | None = None
    held_files: list[HeldFile] = []
    held_trees: list[HeldTree] = []
    build_identity = "unknown"
    try:
        spec_file = HeldFile.open(
            input_spec_path,
            "runtime bundle input spec",
            expected_sha256=_exact_sha(expected_input_spec_sha256, "input spec expected SHA"),
        )
        held_files.append(spec_file)
        spec = _parse_input_spec(spec_file.payload)
        build_identity = spec["build_identity"]
        generator = HeldFile.open(
            str(Path(__file__).resolve(strict=True)),
            "runtime bundle generator",
            expected_sha256=_exact_sha(expected_generator_sha256, "generator expected SHA"),
        )
        held_files.append(generator)
        executable = HeldFile.open(
            spec["python"]["executable_path"],
            "Python executable",
            expected_sha256=spec["python"]["executable_sha256"],
            executable=True,
        )
        held_files.append(executable)
        _verify_isolated_runtime(spec, executable)

        attempt_path = _canonical_absent_path(attempt_receipt_dir, "attempt receipt root")
        bundle_path = _canonical_absent_path(bundle_dir, "runtime bundle root")
        if attempt_path == bundle_path or attempt_path in bundle_path.parents or bundle_path in attempt_path.parents:
            raise BundleBuildError("the two no-clobber output roots must be separate and non-nested")
        attempt = OutputRoot.reserve(str(attempt_path), "attempt receipt root")
        bundle = OutputRoot.reserve(str(bundle_path), "runtime bundle root")

        project: dict[str, HeldFile] = {}
        extras: dict[str, HeldFile] = {}
        seen_paths: set[Path] = {spec_file.path, generator.path, executable.path}
        seen_role_shas: set[str] = set()
        for group_name, target in (
            ("project_sources", project),
            ("package_extra_file_roles", extras),
        ):
            for role, raw in sorted(spec[group_name].items()):
                held = HeldFile.open(
                    raw["path"], f"{group_name}.{role}", expected_sha256=raw["sha256"]
                )
                if held.path in seen_paths:
                    held.close()
                    raise BundleBuildError("input source paths must be unique")
                if held.sha256 in seen_role_shas:
                    held.close()
                    raise BundleBuildError("package source role SHA-256 identities must be unique")
                seen_paths.add(held.path)
                seen_role_shas.add(held.sha256)
                target[role] = held
                held_files.append(held)
                if role.endswith("_json"):
                    _strict_json_object(held.payload, f"{group_name}.{role}")
        _ensure_descriptor_capacity()
        numpy_tree = HeldTree.open(spec["numpy"]["package_root"], "NumPy package root")
        libs_tree = HeldTree.open(spec["numpy"]["libraries_root"], "NumPy libraries root")
        held_trees.extend([numpy_tree, libs_tree])
        if numpy_tree.path == libs_tree.path:
            raise BundleBuildError("NumPy package/libs roots must be distinct")
        return _build_success(
            spec,
            spec_file,
            generator,
            executable,
            project,
            extras,
            numpy_tree,
            libs_tree,
            bundle,
            attempt,
        )
    except BaseException as exc:
        _best_effort_failure_closure(
            bundle, error=exc, build_identity=build_identity, label="runtime bundle output"
        )
        _best_effort_failure_closure(
            attempt, error=exc, build_identity=build_identity, label="attempt receipt output"
        )
        if isinstance(exc, BundleBuildError):
            raise
        raise BundleBuildError(f"runtime bundle build failed: {type(exc).__name__}: {exc}") from exc
    finally:
        for tree in reversed(held_trees):
            tree.close()
        for held in reversed(held_files):
            held.close()
        if bundle is not None:
            bundle.close()
        if attempt is not None:
            attempt.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-spec", required=True)
    parser.add_argument("--expected-input-spec-sha256", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--attempt-receipt-dir", required=True)
    parser.add_argument("--expected-generator-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _require_isolation_flags()
    except BundleBuildError as exc:
        print(f"NO_GO: {exc}", file=sys.stderr)
        return 1
    args = _parser().parse_args(argv)
    try:
        result = build_from_cli(
            input_spec_path=args.input_spec,
            expected_input_spec_sha256=args.expected_input_spec_sha256,
            bundle_dir=args.bundle_dir,
            attempt_receipt_dir=args.attempt_receipt_dir,
            expected_generator_sha256=args.expected_generator_sha256,
        )
    except BundleBuildError as exc:
        print(f"NO_GO: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
