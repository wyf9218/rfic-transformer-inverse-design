#!/usr/bin/env python3
"""Prepared-only result-free MARS native preflight v3 implementation.

The exact v10 runtime package and its formal zero-finding independent QA are
source-bound below.  This source is still not authorized to execute a real
preflight: its own future prepared/QA closure must be supplied by a separately
SHA-bound root authorization, and its production entry must be compiled from
verified bytes held on FD198 by the frozen bootstrap below.  FD197 is the
source interpreter and FD199 is the root authorization.  Direct pathname
execution is always rejected.

The module contains no result reader, controller, resume, signal, network, or
production-build entry.  Its only future mutation surface is a fixed,
result-free preflight work root plus atomically published evidence records.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PREFLIGHT_SCHEMA = "historical_200k_fixed10k_result_free_mars_native_preflight_v3"
PREFLIGHT_STATUS = (
    "PASS_PREPARED_ONLY_NOT_AUTHORIZED_NOT_EXECUTED_AWAITING_INDEPENDENT_QA"
)
AUTH_SCHEMA = "historical_200k_fixed10k_root_held_preflight_launch_authorization_v3"
AUTH_STATUS = "AUTHORIZED_TRUSTED_HELD_PREFLIGHT_PACKAGE_AND_QA_ONLY"
API_SCHEMA = "historical_200k_fixed10k_v10_scoped_native_compatibility_api_v1"
API_STATUS = "PASS_NATIVE_COMPATIBILITY_NOT_PRODUCTION_BUILD"
API_FUNCTION = "execute_scoped_noncanonical_native_compatibility_preflight_v1"
API_SCOPE = "NOT_PRODUCTION_BUILD"

HELD_INTERPRETER_FD = 197
HELD_PREFLIGHT_SOURCE_FD = 198
HELD_AUTHORIZATION_FD = 199
HELD_READ_LIMIT = 256 * 1024 * 1024
AUTH_READ_LIMIT = 16 * 1024 * 1024

EXPECTED_SOURCE_PYTHON = Path(
    "${MARS_RESEARCH_ROOT}/"
    "rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python"
)
EXPECTED_SOURCE_SITE_PACKAGES = Path(
    "${MARS_RESEARCH_ROOT}/"
    "rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/lib64/"
    "python3.12/site-packages"
)
EXPECTED_PRODUCTION_FINAL_ROOT = Path(
    "${MARS_RESEARCH_ROOT}/"
    "historical_200k_fixed10k_emx_v2_20260822/"
    "runtime_addendum_post_stage06_release_chain_v5_20260822T125710Z"
)
EXPECTED_PRODUCTION_PARENT = EXPECTED_PRODUCTION_FINAL_ROOT.parent
EXPECTED_DECISION_ID = "historical-200k-fixed10k-post-stage06-runtime-v10"
EXPECTED_PRODUCTION_JOURNAL = (
    EXPECTED_PRODUCTION_PARENT / f".result-free-transport-v10.{EXPECTED_DECISION_ID}"
)
EXPECTED_PREFLIGHT_WORK_PARENT = Path(
    "${MARS_RESEARCH_ROOT}/"
    "historical_200k_fixed10k_result_free_native_preflight_v3_20260822"
)
EXPECTED_PREFLIGHT_WORK_ROOT = (
    EXPECTED_PREFLIGHT_WORK_PARENT / "decision_001_native_compatibility_only"
)
EXPECTED_EVIDENCE_JOURNAL = EXPECTED_PREFLIGHT_WORK_ROOT / "evidence"
EXPECTED_COMPATIBILITY_ROOT = EXPECTED_PREFLIGHT_WORK_ROOT / "compat_runtime_root"
EXPECTED_COMPATIBILITY_JOURNAL = (
    EXPECTED_PREFLIGHT_WORK_ROOT
    / f".result-free-transport-v10.{EXPECTED_DECISION_ID}.native-compatibility"
)
EXPECTED_V10_EVIDENCE_BASE = Path(
    "${MARS_RESEARCH_ROOT}/"
    "historical_200k_fixed10k_preflight_evidence_transport_v10_20260822"
)
EXPECTED_V10_PACKAGE_ROOT = (
    EXPECTED_V10_EVIDENCE_BASE
    / "transport_runtime_layout_builder_v10_prepared_20260822T205518Z"
)
EXPECTED_V10_AUDIT_ROOT = (
    EXPECTED_V10_EVIDENCE_BASE
    / "independent_transport_runtime_layout_builder_v10_qa_20260822T211115Z"
)

UNBOUND_PACKAGE = "UNBOUND_AWAITING_THIS_PREFLIGHT_PREPARED_PACKAGE"
UNBOUND_AUDIT = "UNBOUND_AWAITING_FRESH_PREFLIGHT_V3_INDEPENDENT_QA"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DECISION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{15,127}$")
UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_TMPFILE = getattr(os, "O_TMPFILE", 0)
AT_FDCWD = -100
AT_SYMLINK_FOLLOW = 0x400

PRODUCTION_EVIDENCE_PUBLICATION_METHOD = (
    "LINUX_XFS_O_TMPFILE_COMPLETE_FCHMOD0444_FSYNC_"
    "LINKAT_PROC_SELF_FD_AT_SYMLINK_FOLLOW_NOREPLACE_DIRFSYNC_V1"
)
EVIDENCE_VISIBILITY_RULE = (
    "CANONICAL_NAME_ABSENT_UNTIL_COMPLETE_0444_FSYNCED_INODE_LINKED_NOREPLACE"
)
BEGIN_SCHEMA = "historical_200k_fixed10k_result_free_native_preflight_begin_v3"
INTENT_SCHEMA = "historical_200k_fixed10k_result_free_native_preflight_intent_v3"
PASS_SCHEMA = "historical_200k_fixed10k_result_free_native_preflight_pass_v3"
FAIL_SCHEMA = "historical_200k_fixed10k_result_free_native_preflight_fail_v3"
RESULT_SCHEMA = (
    "historical_200k_fixed10k_result_free_native_preflight_compatibility_result_v3"
)
BEGIN_STATUS = "DURABLE_BEGIN_BEFORE_NATIVE_COMPATIBILITY_CALL"
INTENT_STATUS = "DURABLE_INTENT_BEFORE_NATIVE_COMPATIBILITY_CALL"
PASS_STATUS = "PASS_NATIVE_COMPATIBILITY_ONLY_NOT_PRODUCTION_BUILD"
FAIL_STATUS = "FAIL_CLOSED_NATIVE_COMPATIBILITY_PREFLIGHT"
RESULT_STATUS = "PASS_VALIDATED_NATIVE_COMPATIBILITY_RESULT_NOT_PRODUCTION_BUILD"
BEGIN_NAME = "BEGIN.json"
INTENT_NAME = "INTENT.json"
RESULT_NAME = "COMPATIBILITY_RESULT.json"
PASS_NAME = "TERMINAL_PASS.json"
FAIL_NAME = "TERMINAL_FAIL.json"

PACKAGE_BINDING_KEYS = frozenset({
    "prepared_receipt_path", "prepared_receipt_sha256",
    "bundle_manifest_path", "bundle_manifest_sha256",
    "sha256_index_path", "sha256_index_sha256",
})
AUDIT_BINDING_KEYS = frozenset({
    "report_path", "report_sha256", "receipt_path", "receipt_sha256",
    "output_path", "output_sha256", "log_path", "log_sha256",
    "harness_path", "harness_sha256", "closure_path", "closure_sha256",
    "bundle_manifest_path", "bundle_manifest_sha256",
    "sha256_index_path", "sha256_index_sha256",
})
V10_PACKAGE_BINDING_KEYS = frozenset({
    "builder_path", "builder_sha256", "builder_test_path", "builder_test_sha256",
    "smoke_path", "smoke_sha256", "smoke_test_path", "smoke_test_sha256",
    "prepared_receipt_path", "prepared_receipt_sha256",
    "bundle_manifest_path", "bundle_manifest_sha256",
    "sha256_index_path", "sha256_index_sha256",
})
V10_AUDIT_BINDING_KEYS = AUDIT_BINDING_KEYS | frozenset({
    "action_scoped_verdict", "finding_counts",
})
V10_PACKAGE_TOP_LEVEL_COUNT = 15
V10_PACKAGE_INDEXED_COUNT = 14
V10_QA_TOP_LEVEL_COUNT = 14
V10_QA_INDEXED_COUNT = 12
V10_QA_ACTION_SCOPED_VERDICT = (
    "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LOCAL_NATIVE_PREFLIGHT_"
    "PREREQUISITE_ONLY"
)
V10_PREPARED_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_transport_runtime_layout_builder_v10_"
    "bundle_manifest_v1"
)
V10_PREPARED_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_transport_runtime_layout_builder_v10_"
    "prepared_receipt_v1"
)
V10_PREPARED_STATUS = (
    "PASS_PREPARED_ONLY_AWAITING_FRESH_INDEPENDENT_QA_NOT_AUTHORIZED"
)
V10_QA_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_"
    "v10_qa_bundle_manifest_v1"
)
V10_QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_independent_transport_runtime_layout_builder_"
    "v10_qa_receipt_v1"
)
V10_QA_MANIFEST_STATUS = "PASS_INDEPENDENT_LOCAL_RESULT_BLIND_QA_ZERO_FINDINGS"
V10_QA_RECEIPT_STATUS = (
    "PASS_INDEPENDENT_QA_RESULT_FREE_PACKAGE_ONLY_ZERO_FINDINGS"
)
V10_PACKAGE_TOP_LEVEL_MEMBERS = frozenset({
    "AUTHOR_BUILDER_V10_SYNTHETIC_OUTPUT.json",
    "AUTHOR_COMPILE_V10_OUTPUT.json",
    "AUTHOR_DOUBLE_RUN_V10_VALIDATION.json",
    "AUTHOR_SMOKE_V10_SYNTHETIC_OUTPUT.json",
    "AUTHOR_V10_WIP_FAILURES.json",
    "BUNDLE_MANIFEST.json",
    "PREPARED_RESULT_FREE_RECEIPT.json",
    "SHA256SUMS",
    "TRANSPORT_RUNTIME_LAYOUT_BUILDER_V10_PREPARED_CN.md",
    "TRANSPORT_RUNTIME_LAYOUT_CONTRACT_V10.json",
    "UPSTREAM_EVIDENCE_BINDINGS_V10.json",
    "build_result_free_transport_runtime_v10.py",
    "result_free_runtime_smoke_v10.py",
    "test_result_free_runtime_smoke_v10_synthetic.py",
    "test_transport_runtime_layout_builder_v10_synthetic.py",
})
V10_PACKAGE_INDEX_MEMBERS = V10_PACKAGE_TOP_LEVEL_MEMBERS - {"SHA256SUMS"}
V10_QA_TOP_LEVEL_MEMBERS = frozenset({
    "BUNDLE_MANIFEST.json",
    "COMMAND_LOG.txt",
    "FIXTURE_PROTOTYPE_ATTEMPT1_FAILURE.log",
    "FIXTURE_PROTOTYPE_ATTEMPT2_FAILURE.log",
    "HARNESS_ATTEMPT1_FAILURE.log",
    "HARNESS_ATTEMPT2_FAILURE.log",
    "HARNESS_DOUBLE_RUN_VALIDATION.json",
    "INDEPENDENT_QA_HARNESS.py",
    "INDEPENDENT_QA_OUTPUT.json",
    "INDEPENDENT_QA_RECEIPT.json",
    "INDEPENDENT_QA_REPORT_CN.md",
    "PACKAGE_CLOSURE_QA.json",
    "SHA256SUMS",
    "TEST_MATRIX_RESULT_BLIND_V10_WIP.md",
})
V10_QA_INDEX_MEMBERS = V10_QA_TOP_LEVEL_MEMBERS - {
    "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
}
PREPARED_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v3_bundle_manifest_v3"
)
PREPARED_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v3_prepared_receipt_v3"
)
PREPARED_STATUS = (
    "PASS_PREPARED_ONLY_NOT_AUTHORIZED_NOT_EXECUTED_AWAITING_INDEPENDENT_QA"
)
QA_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v3_"
    "independent_qa_receipt_v1"
)
QA_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_result_free_mars_native_preflight_v3_"
    "independent_qa_bundle_manifest_v1"
)
QA_GO_STATUS = (
    "PASS_INDEPENDENT_QA_CODE_GO_REQUIRES_SEPARATE_EXACT_AUTHORIZATION"
)
QA_GO_VERDICT = (
    "GO_FOR_SEPARATELY_SIGNED_RESULT_FREE_LINUX_MARS_XFS_NATIVE_"
    "COMPATIBILITY_PREFLIGHT_ONLY"
)
QA_AUTHORITY_KEYS = frozenset({
    "mars_access_authorized", "mars_write_authorized",
    "preflight_execution_authorized", "transport_build_or_smoke_authorized",
    "production_root_or_journal_write_authorized", "result_access_authorized",
    "external_process_inspection_or_control_authorized", "signals_authorized",
    "controller_or_resume_authorized", "deployment_authorized",
})
PREPARED_TOP_LEVEL_MEMBERS = frozenset({
    "AUTHOR_COMPILE_V3_OUTPUT.json", "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json",
    "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
    "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json",
    "RESULT_FREE_MARS_NATIVE_PREFLIGHT_V3_CN.md", "SHA256SUMS",
    "UPSTREAM_EVIDENCE_BINDINGS_V3.json",
    "run_result_free_mars_native_preflight_v3.py",
    "test_result_free_mars_native_preflight_v3_synthetic.py",
})
PREPARED_MANIFEST_PAYLOAD_MEMBERS = PREPARED_TOP_LEVEL_MEMBERS - {
    "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json", "SHA256SUMS",
}
QA_TOP_LEVEL_MEMBERS = frozenset({
    "BUNDLE_MANIFEST.json", "COMMAND_LOG.txt", "INDEPENDENT_QA_OUTPUT.json",
    "INDEPENDENT_QA_RECEIPT.json", "INDEPENDENT_QA_REPORT_CN.md",
    "PACKAGE_CLOSURE_QA.json", "QA_HARNESS_OR_METHOD.md", "SHA256SUMS",
})
QA_MANIFEST_PAYLOAD_MEMBERS = QA_TOP_LEVEL_MEMBERS - {
    "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
}
MANIFEST_FILE_RECORD_KEYS = frozenset({
    "relative_path", "role", "sha256", "size_bytes",
})
PREPARED_PACKAGE_CLOSURE_KEYS = frozenset({
    "bundle_manifest_sha256", "payload_file_count",
    "sha_index_listed_count_expected", "top_level_file_count_expected",
})
PREPARED_LOCKED_TOOLS_KEYS = frozenset({"preflight", "synthetic_test"})
LOCKED_TOOL_RECORD_KEYS = frozenset({"path", "sha256", "line_count"})
AUTHOR_VALIDATION_KEYS = frozenset({
    "darwin_actual", "linux_xfs_actual", "manifest_payload_hash_and_size_pass",
    "source_compile", "strict_json_parse_pass", "synthetic_test",
})
COMPILE_VALIDATION_KEYS = frozenset({"checked", "failed", "output_sha256", "passed"})
SYNTHETIC_VALIDATION_KEYS = frozenset({
    "checked", "failed", "passed", "raw_output_sha256",
})
PREPARED_SCOPE_KEYS = frozenset({
    "mars_accessed", "mars_written", "results_accessed",
    "external_processes_inspected_or_controlled",
    "real_preflight_or_smoke_subprocess_started", "signals_sent",
    "controller_or_outer_main_executed", "deployment_or_resume_executed",
    "production_root_or_journal_created_or_modified",
})
QA_AUDITED_PACKAGE_KEYS = frozenset({
    "bundle_manifest_sha256", "contract_sha256", "directory",
    "evidence_bindings_sha256", "prepared_receipt_sha256", "script_sha256",
    "sha256_index_sha256", "test_sha256",
})
QA_ARTIFACT_KEYS = frozenset({
    "closure", "harness", "log", "manifest", "output", "report",
})
QA_ARTIFACT_RECORD_KEYS = frozenset({"path", "sha256"})
QA_SCOPE_KEYS = frozenset({
    "mars_accessed", "results_accessed", "real_preflight_executed",
    "production_executed", "external_processes_inspected_or_controlled",
    "signals_sent", "candidate_modified", "memory_modified",
})
ZERO_FINDINGS = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
XFS_SUPER_MAGIC = 0x58465342


class PreflightError(RuntimeError):
    """Fail-closed contract error."""


class PreflightBlocked(PreflightError):
    """Expected prepared-only/dependency block before contract mutation."""


class DuplicateKeyError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pairs_no_duplicate(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if type(key) is not str or key in result:
            raise DuplicateKeyError(f"duplicate/non-string JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _validate_json_types(value: Any, label: str) -> None:
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise PreflightError(f"{label}: non-string key")
            _validate_json_types(key, f"{label}.key")
            _validate_json_types(item, f"{label}.{key}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_types(item, f"{label}[{index}]")
        return
    if type(value) is str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise PreflightError(f"{label}: forbidden string code point")
        return
    if type(value) in {bool, int}:
        return
    raise PreflightError(f"{label}: unsupported JSON type {type(value).__name__}")


def strict_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise PreflightError(f"{label}: BOM forbidden")
    try:
        value = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=_pairs_no_duplicate,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        raise PreflightError(f"{label}: strict JSON failure: {exc}") from exc
    if type(value) is not dict:
        raise PreflightError(f"{label}: top-level object required")
    _validate_json_types(value, label)
    if canonical_json_bytes(value) != data:
        raise PreflightError(f"{label}: noncanonical JSON bytes")
    return value


def exact_object(value: Any, keys: frozenset[str] | set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        raise PreflightError(f"{label}: exact object mismatch")
    return value


def exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise PreflightError(f"{label}: exact nonempty string required")
    return value


def exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise PreflightError(f"{label}: exact boolean {expected} required")


def exact_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise PreflightError(f"{label}: exact nonnegative integer required")
    return value


def exact_sha(value: Any, label: str) -> str:
    if type(value) is not str or SHA_RE.fullmatch(value) is None:
        raise PreflightError(f"{label}: lowercase SHA-256 required")
    return value


def exact_absolute_path(value: Any, label: str) -> Path:
    raw = exact_string(value, label)
    if not raw.startswith("/") or os.path.normpath(raw) != raw:
        raise PreflightError(f"{label}: canonical absolute path required")
    return Path(raw)


def exact_utc(value: Any, label: str) -> str:
    text = exact_string(value, label)
    if UTC_RE.fullmatch(text) is None:
        raise PreflightError(f"{label}: canonical UTC second timestamp required")
    return text


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    nlink: int

    @classmethod
    def from_stat(cls, info: os.stat_result) -> "FileIdentity":
        return cls(
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, stat.S_IMODE(info.st_mode), info.st_nlink,
        )

    @classmethod
    def from_json(cls, value: Any, label: str) -> "FileIdentity":
        item = exact_object(
            value,
            {"device", "inode", "size_bytes", "mtime_ns", "ctime_ns", "mode", "nlink"},
            label,
        )
        for key in ("device", "inode", "size_bytes", "mtime_ns", "ctime_ns", "nlink"):
            exact_int(item[key], f"{label}.{key}")
        if type(item["mode"]) is not str or re.fullmatch(r"0[0-7]{3}", item["mode"]) is None:
            raise PreflightError(f"{label}.mode: canonical mode required")
        return cls(
            item["device"], item["inode"], item["size_bytes"], item["mtime_ns"],
            item["ctime_ns"], int(item["mode"], 8), item["nlink"],
        )

    def json(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "inode": self.inode,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
            "mode": f"{self.mode:04o}",
            "nlink": self.nlink,
        }


def _stable_stat_tuple(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_mode, info.st_nlink, info.st_dev, info.st_ino, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def pread_exact(fd: int, size: int, label: str, limit: int = HELD_READ_LIMIT) -> bytes:
    if type(fd) is not int or fd < 0 or size < 0 or size > limit:
        raise PreflightError(f"{label}: invalid FD/size")
    pieces: list[bytes] = []
    offset = 0
    while offset < size:
        block = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not block:
            raise PreflightError(f"{label}: short pread")
        pieces.append(block)
        offset += len(block)
    if os.pread(fd, 1, size) != b"":
        raise PreflightError(f"{label}: file grew beyond authorized size")
    return b"".join(pieces)


def read_stable_fd(
    fd: int, label: str, *, limit: int = HELD_READ_LIMIT,
    mode: int | None = 0o444, nlink: int = 1,
) -> tuple[bytes, FileIdentity]:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flags & os.O_ACCMODE != os.O_RDONLY:
        raise PreflightError(f"{label}: held FD is not O_RDONLY")
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise PreflightError(f"{label}: held FD is not regular")
    identity = FileIdentity.from_stat(before)
    if (mode is not None and identity.mode != mode) or identity.nlink != nlink:
        raise PreflightError(f"{label}: held mode/nlink mismatch")
    data = pread_exact(fd, before.st_size, label, limit)
    if _stable_stat_tuple(os.fstat(fd)) != _stable_stat_tuple(before):
        raise PreflightError(f"{label}: identity changed during pread")
    return data, identity


def validate_inherited_held_fd(fd: int, label: str) -> None:
    """Bootstrap FDs must survive exec and remain read-only regular files."""

    if type(fd) is not int or fd < 0:
        raise PreflightError(f"{label}: fixed FD is missing")
    try:
        status_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        descriptor_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        info = os.fstat(fd)
    except OSError as exc:
        raise PreflightError(f"{label}: fixed FD is unavailable") from exc
    if status_flags & os.O_ACCMODE != os.O_RDONLY:
        raise PreflightError(f"{label}: fixed FD is not O_RDONLY")
    if descriptor_flags & fcntl.FD_CLOEXEC:
        raise PreflightError(f"{label}: fixed FD is CLOEXEC/not inherited")
    if not stat.S_ISREG(info.st_mode):
        raise PreflightError(f"{label}: fixed FD is not regular")


def open_absolute_directory_nofollow(path: Path, label: str) -> int:
    """Open an absolute directory component-by-component without following links."""

    if not path.is_absolute() or os.path.normpath(os.fspath(path)) != os.fspath(path):
        raise PreflightError(f"{label}: canonical absolute directory required")
    current = os.open("/", os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise PreflightError(f"{label}: unsafe path component")
            next_fd = os.open(
                component,
                os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


class FileLease:
    """Held nofollow file descriptor plus repeated named-inode validation."""

    def __init__(
        self, path: Path, fd: int, identity: FileIdentity, digest: str,
        parent_fd: int, parent_identity: FileIdentity, data: bytes,
        mode_constraint: int | None = 0o444,
    ) -> None:
        self.path = path
        self.fd = fd
        self.identity = identity
        self.digest = digest
        self.parent_fd = parent_fd
        self.parent_identity = parent_identity
        self.data = data
        self.mode_constraint = mode_constraint

    @classmethod
    def open(
        cls, path: Path, expected_sha: str, label: str, *, mode: int | None = 0o444,
    ) -> "FileLease":
        exact_sha(expected_sha, f"{label}.sha256")
        parent_fd = open_absolute_directory_nofollow(path.parent, f"{label}.parent")
        try:
            parent_identity = FileIdentity.from_stat(os.fstat(parent_fd))
            fd = os.open(path.name, os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd)
            try:
                data, identity = read_stable_fd(fd, label, mode=mode)
                digest = sha256_bytes(data)
                if digest != expected_sha:
                    raise PreflightError(f"{label}: SHA mismatch")
                lease = cls(
                    path, fd, identity, digest, parent_fd, parent_identity, data,
                    mode,
                )
                lease.revalidate(label + ".initial")
                return lease
            except BaseException:
                os.close(fd)
                raise
        except BaseException:
            os.close(parent_fd)
            raise

    def revalidate(self, label: str) -> None:
        if FileIdentity.from_stat(os.fstat(self.parent_fd)) != self.parent_identity:
            raise PreflightError(f"{label}: anchor parent directory mutated")
        held = FileIdentity.from_stat(os.fstat(self.fd))
        if held != self.identity:
            raise PreflightError(f"{label}: held identity drift")
        probe = os.open(
            self.path.name, os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC,
            dir_fd=self.parent_fd,
        )
        try:
            data, identity = read_stable_fd(probe, label, mode=self.mode_constraint)
            if identity != self.identity or sha256_bytes(data) != self.digest:
                raise PreflightError(f"{label}: named anchor differs from held inode")
        finally:
            os.close(probe)

    def stable_bytes(self, label: str) -> bytes:
        """Return the originally verified bytes after revalidating held and named identity."""

        self.revalidate(label)
        data, identity = read_stable_fd(
            self.fd, label, mode=self.mode_constraint
        )
        if identity != self.identity or sha256_bytes(data) != self.digest:
            raise PreflightError(f"{label}: held anchor bytes changed")
        if data != self.data:
            raise PreflightError(f"{label}: held anchor differs from initial bytes")
        return data

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


class DirectoryLease:
    """Held directory descriptor with path/inode continuity checks."""

    def __init__(
        self, path: Path, fd: int, identity: FileIdentity,
        parent_fd: int, parent_identity: FileIdentity,
    ) -> None:
        self.path = path
        self.fd = fd
        self.identity = identity
        self.parent_fd = parent_fd
        self.parent_identity = parent_identity

    @classmethod
    def open(cls, path: Path, label: str) -> "DirectoryLease":
        parent_fd = open_absolute_directory_nofollow(path.parent, f"{label}.parent")
        try:
            parent_identity = FileIdentity.from_stat(os.fstat(parent_fd))
            fd = os.open(
                path.name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except BaseException:
            os.close(parent_fd)
            raise
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise PreflightError(f"{label}: directory required")
            lease = cls(
                path, fd, FileIdentity.from_stat(info), parent_fd, parent_identity
            )
            lease.revalidate(label + ".initial")
            return lease
        except BaseException:
            os.close(fd)
            os.close(parent_fd)
            raise

    def revalidate(self, label: str) -> None:
        if FileIdentity.from_stat(os.fstat(self.parent_fd)) != self.parent_identity:
            raise PreflightError(f"{label}: directory parent mutated")
        if FileIdentity.from_stat(os.fstat(self.fd)) != self.identity:
            raise PreflightError(f"{label}: held directory identity drift")
        probe = os.open(
            self.path.name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            dir_fd=self.parent_fd,
        )
        try:
            if FileIdentity.from_stat(os.fstat(probe)) != self.identity:
                raise PreflightError(f"{label}: directory path changed")
        finally:
            os.close(probe)
        absolute_probe = open_absolute_directory_nofollow(
            self.path, f"{label}.absolute_path"
        )
        try:
            if FileIdentity.from_stat(os.fstat(absolute_probe)) != self.identity:
                raise PreflightError(f"{label}: directory absolute path changed")
        finally:
            os.close(absolute_probe)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


@dataclass(frozen=True)
class FrozenBindings:
    preflight_package: Mapping[str, str]
    preflight_audit: Mapping[str, Any]
    v10_package: Mapping[str, str]
    v10_audit: Mapping[str, Any]

    def all_sha_values(self) -> list[str]:
        result: list[str] = []
        for group in (
            self.preflight_package, self.preflight_audit,
            self.v10_package, self.v10_audit,
        ):
            result.extend(
                value for key, value in group.items()
                if key.endswith("_sha256") and type(value) is str
            )
        return result

    def is_fully_bound(self) -> bool:
        values = self.all_sha_values()
        return bool(values) and all(SHA_RE.fullmatch(value) for value in values)

    def upstream_v10_is_fully_bound(self) -> bool:
        values: list[str] = []
        for group in (self.v10_package, self.v10_audit):
            values.extend(
                value for key, value in group.items()
                if key.endswith("_sha256") and type(value) is str
            )
        return bool(values) and all(SHA_RE.fullmatch(value) for value in values)


FROZEN_BINDINGS = FrozenBindings(
    preflight_package={
        "prepared_receipt_path": UNBOUND_PACKAGE,
        "prepared_receipt_sha256": UNBOUND_PACKAGE,
        "bundle_manifest_path": UNBOUND_PACKAGE,
        "bundle_manifest_sha256": UNBOUND_PACKAGE,
        "sha256_index_path": UNBOUND_PACKAGE,
        "sha256_index_sha256": UNBOUND_PACKAGE,
    },
    preflight_audit={
        key: UNBOUND_AUDIT for key in AUDIT_BINDING_KEYS
    },
    v10_package={
        "builder_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT / "build_result_free_transport_runtime_v10.py"
        ),
        "builder_sha256": "fefbfcf8ecc77dcc55ba509e803e0c4a442f1e61164e02eec38f6e34c03d9de1",
        "builder_test_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT
            / "test_transport_runtime_layout_builder_v10_synthetic.py"
        ),
        "builder_test_sha256": "cea2026629fd750009daf2d0926f9323f725e5ba4b1307ba05d08c901ce8c96e",
        "smoke_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT / "result_free_runtime_smoke_v10.py"
        ),
        "smoke_sha256": "93d78448eb37fa47ad2760e78d8e0148d5018f06985e7b2a66649288682b6282",
        "smoke_test_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT
            / "test_result_free_runtime_smoke_v10_synthetic.py"
        ),
        "smoke_test_sha256": "0c17ef19dff6f4abeff00d9ad64903cd414b15c93bb0ba431a7d9aac238c73be",
        "prepared_receipt_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT / "PREPARED_RESULT_FREE_RECEIPT.json"
        ),
        "prepared_receipt_sha256": "b73aed2f3a2f6e225390c9d2df402fa8e61a457e70ec55b73af1b845f1f7ec2b",
        "bundle_manifest_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT / "BUNDLE_MANIFEST.json"
        ),
        "bundle_manifest_sha256": "9a5bfca586a7cc2e0ed795cc3c785bc74889a285644e950d40a21fc8bd1fec28",
        "sha256_index_path": os.fspath(
            EXPECTED_V10_PACKAGE_ROOT / "SHA256SUMS"
        ),
        "sha256_index_sha256": "e2073343323a19a153843079dd8b787c97929c02b6c9c4152fd03e0e2799acb2",
    },
    v10_audit={
        "report_path": os.fspath(
            EXPECTED_V10_AUDIT_ROOT / "INDEPENDENT_QA_REPORT_CN.md"
        ),
        "report_sha256": "f9e054d940a7f5a8f319cc51efefcb0ea1df99aaed3d591eae04408cc67d2a24",
        "receipt_path": os.fspath(
            EXPECTED_V10_AUDIT_ROOT / "INDEPENDENT_QA_RECEIPT.json"
        ),
        "receipt_sha256": "ba35a9a1f597e81c819a43c3a22920a19873c27c88cf1ea6a1d2ca8e6cac5d45",
        "output_path": os.fspath(
            EXPECTED_V10_AUDIT_ROOT / "INDEPENDENT_QA_OUTPUT.json"
        ),
        "output_sha256": "2b1a2dc1ac91c52c130eb522c6f53d19784879ee317e9aed9a5a7e6e947d1749",
        "log_path": os.fspath(EXPECTED_V10_AUDIT_ROOT / "COMMAND_LOG.txt"),
        "log_sha256": "ce557892bcbf32be412ae0298bf3dd01fa81519d7cb84d1e69e268c6cab44081",
        "harness_path": os.fspath(
            EXPECTED_V10_AUDIT_ROOT / "INDEPENDENT_QA_HARNESS.py"
        ),
        "harness_sha256": "99f5fe129ec3667db57cd4c7db6d6589897726427b7eb3901d0f39ea2fc7467d",
        "closure_path": os.fspath(
            EXPECTED_V10_AUDIT_ROOT / "PACKAGE_CLOSURE_QA.json"
        ),
        "closure_sha256": "7efec95bbe8e636567f07434f19b80a0b050c68553f6f3ce59760501aab2025a",
        "bundle_manifest_path": os.fspath(
            EXPECTED_V10_AUDIT_ROOT / "BUNDLE_MANIFEST.json"
        ),
        "bundle_manifest_sha256": "1c5b26207f5e5b12fdf5dbad6c5fc29b65a14f631c65cca78f33564bab41a942",
        "sha256_index_path": os.fspath(EXPECTED_V10_AUDIT_ROOT / "SHA256SUMS"),
        "sha256_index_sha256": "f0bc12e7b359aa3bd934f33b643f4836a876c6ed672b29ee525c546dc75d539a",
        "action_scoped_verdict": V10_QA_ACTION_SCOPED_VERDICT,
        "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
    },
)


def require_frozen_bindings(bindings: FrozenBindings = FROZEN_BINDINGS) -> None:
    if not bindings.is_fully_bound():
        raise PreflightBlocked(
            "preflight v3 self prepared package and fresh independent QA "
            "closure are not both exact-bound"
        )


def validate_frozen_bindings_shape(
    bindings: FrozenBindings, *, require_self: bool,
) -> None:
    """Require exact binding keys and source-frozen v10 GO semantics."""

    groups: tuple[tuple[Mapping[str, Any], frozenset[str], str], ...] = (
        (bindings.v10_package, V10_PACKAGE_BINDING_KEYS, "v10_package"),
        (bindings.v10_audit, V10_AUDIT_BINDING_KEYS, "v10_audit"),
    )
    if require_self:
        groups = (
            (bindings.preflight_package, PACKAGE_BINDING_KEYS, "preflight_package"),
            (bindings.preflight_audit, AUDIT_BINDING_KEYS, "preflight_audit"),
        ) + groups
    for group, keys, label in groups:
        item = exact_object(group, keys, f"bindings.{label}")
        for key in keys:
            if key.endswith("_path"):
                exact_absolute_path(item[key], f"bindings.{label}.{key}")
            elif key.endswith("_sha256"):
                exact_sha(item[key], f"bindings.{label}.{key}")
    if (
        bindings.v10_audit["action_scoped_verdict"]
        != V10_QA_ACTION_SCOPED_VERDICT
    ):
        raise PreflightError("v10 independent QA exact scoped GO absent")
    _validate_zero_findings(
        bindings.v10_audit["finding_counts"],
        "bindings.v10_audit.finding_counts",
    )


def validate_production_v10_binding_roots(bindings: FrozenBindings) -> None:
    """Bind production execution to the predeclared MARS transport roots."""

    validate_frozen_bindings_shape(bindings, require_self=False)
    expected_package_names = {
        "builder_path": "build_result_free_transport_runtime_v10.py",
        "builder_test_path": "test_transport_runtime_layout_builder_v10_synthetic.py",
        "smoke_path": "result_free_runtime_smoke_v10.py",
        "smoke_test_path": "test_result_free_runtime_smoke_v10_synthetic.py",
        "prepared_receipt_path": "PREPARED_RESULT_FREE_RECEIPT.json",
        "bundle_manifest_path": "BUNDLE_MANIFEST.json",
        "sha256_index_path": "SHA256SUMS",
    }
    expected_audit_names = {
        "report_path": "INDEPENDENT_QA_REPORT_CN.md",
        "receipt_path": "INDEPENDENT_QA_RECEIPT.json",
        "output_path": "INDEPENDENT_QA_OUTPUT.json",
        "log_path": "COMMAND_LOG.txt",
        "harness_path": "INDEPENDENT_QA_HARNESS.py",
        "closure_path": "PACKAGE_CLOSURE_QA.json",
        "bundle_manifest_path": "BUNDLE_MANIFEST.json",
        "sha256_index_path": "SHA256SUMS",
    }
    for group, root, names, label in (
        (
            bindings.v10_package,
            EXPECTED_V10_PACKAGE_ROOT,
            expected_package_names,
            "v10_package",
        ),
        (
            bindings.v10_audit,
            EXPECTED_V10_AUDIT_ROOT,
            expected_audit_names,
            "v10_audit",
        ),
    ):
        for key, name in names.items():
            path = exact_absolute_path(group[key], f"bindings.{label}.{key}")
            if path != root / name:
                raise PreflightError(
                    f"{label}: path is outside the predeclared MARS evidence root"
                )
    if (
        EXPECTED_V10_PACKAGE_ROOT.parent != EXPECTED_V10_EVIDENCE_BASE
        or EXPECTED_V10_AUDIT_ROOT.parent != EXPECTED_V10_EVIDENCE_BASE
        or EXPECTED_V10_PACKAGE_ROOT == EXPECTED_V10_AUDIT_ROOT
    ):
        raise PreflightError("predeclared MARS v10 evidence roots are invalid")


def effective_bindings_from_signed_authorization(
    auth: Mapping[str, Any],
    upstream: FrozenBindings = FROZEN_BINDINGS,
) -> FrozenBindings:
    """Expand the v10-consumable root authorization into exact self closures."""

    if not upstream.upstream_v10_is_fully_bound():
        raise PreflightBlocked("frozen v10 prepared package and independent QA absent")
    validate_frozen_bindings_shape(upstream, require_self=False)
    top = exact_object(auth, AUTH_TOP_KEYS, "authorization")
    package_manifest_path = exact_absolute_path(
        top["preflight_package_manifest_path"],
        "authorization.preflight_package_manifest_path",
    )
    package_index_path = exact_absolute_path(
        top["preflight_package_index_path"],
        "authorization.preflight_package_index_path",
    )
    audit_receipt_path = exact_absolute_path(
        top["preflight_independent_audit_receipt_path"],
        "authorization.preflight_independent_audit_receipt_path",
    )
    audit_index_path = exact_absolute_path(
        top["preflight_independent_audit_index_path"],
        "authorization.preflight_independent_audit_index_path",
    )
    if (
        package_manifest_path.name != "BUNDLE_MANIFEST.json"
        or package_index_path.name != "SHA256SUMS"
        or package_manifest_path.parent != package_index_path.parent
        or audit_receipt_path.name != "INDEPENDENT_QA_RECEIPT.json"
        or audit_index_path.name != "SHA256SUMS"
        or audit_receipt_path.parent != audit_index_path.parent
        or package_manifest_path.parent == audit_receipt_path.parent
    ):
        raise PreflightError("root authorization self-closure paths are not exact")

    package_index_lease = FileLease.open(
        package_index_path,
        top["preflight_package_index_sha256"],
        "authorization.preflight_package_index",
    )
    try:
        package_index = parse_sha256_index(
            package_index_lease.data,
            PREPARED_TOP_LEVEL_MEMBERS - {"SHA256SUMS"},
            "authorization.preflight_package_index",
        )
    finally:
        package_index_lease.close()
    if package_index["BUNDLE_MANIFEST.json"] != exact_sha(
        top["preflight_package_manifest_sha256"],
        "authorization.preflight_package_manifest_sha256",
    ):
        raise PreflightError("root authorization prepared manifest/index mismatch")

    audit_index_lease = FileLease.open(
        audit_index_path,
        top["preflight_independent_audit_index_sha256"],
        "authorization.preflight_independent_audit_index",
    )
    try:
        audit_index = parse_sha256_index(
            audit_index_lease.data,
            QA_TOP_LEVEL_MEMBERS - {"SHA256SUMS"},
            "authorization.preflight_independent_audit_index",
        )
    finally:
        audit_index_lease.close()
    if audit_index["INDEPENDENT_QA_RECEIPT.json"] != exact_sha(
        top["preflight_independent_audit_receipt_sha256"],
        "authorization.preflight_independent_audit_receipt_sha256",
    ):
        raise PreflightError("root authorization QA receipt/index mismatch")

    package_binding = {
        "prepared_receipt_path": os.fspath(
            package_manifest_path.parent / "PREPARED_RESULT_FREE_RECEIPT.json"
        ),
        "prepared_receipt_sha256": package_index[
            "PREPARED_RESULT_FREE_RECEIPT.json"
        ],
        "bundle_manifest_path": os.fspath(package_manifest_path),
        "bundle_manifest_sha256": top["preflight_package_manifest_sha256"],
        "sha256_index_path": os.fspath(package_index_path),
        "sha256_index_sha256": top["preflight_package_index_sha256"],
    }
    audit_names = {
        "report": "INDEPENDENT_QA_REPORT_CN.md",
        "receipt": "INDEPENDENT_QA_RECEIPT.json",
        "output": "INDEPENDENT_QA_OUTPUT.json",
        "log": "COMMAND_LOG.txt",
        "harness": "QA_HARNESS_OR_METHOD.md",
        "closure": "PACKAGE_CLOSURE_QA.json",
        "bundle_manifest": "BUNDLE_MANIFEST.json",
        "sha256_index": "SHA256SUMS",
    }
    audit_binding: dict[str, str] = {}
    for stem, name in audit_names.items():
        audit_binding[f"{stem}_path"] = os.fspath(audit_receipt_path.parent / name)
        audit_binding[f"{stem}_sha256"] = (
            top["preflight_independent_audit_index_sha256"]
            if name == "SHA256SUMS"
            else audit_index[name]
        )
    effective = FrozenBindings(
        preflight_package=package_binding,
        preflight_audit=audit_binding,
        v10_package=dict(upstream.v10_package),
        v10_audit=dict(upstream.v10_audit),
    )
    require_frozen_bindings(effective)
    return effective


AUTH_TOP_KEYS = frozenset({
    "schema", "status", "created_utc", "decision_id",
    "preflight_package_manifest_path",
    "preflight_package_manifest_sha256",
    "preflight_package_index_path", "preflight_package_index_sha256",
    "preflight_independent_audit_receipt_path",
    "preflight_independent_audit_receipt_sha256",
    "preflight_independent_audit_index_path",
    "preflight_independent_audit_index_sha256", "authority",
})
AUTHORITY_KEYS = frozenset({
    "preflight_launch_authorized", "transport_runtime_layout_authorized",
    "result_access_authorized", "signals_authorized",
    "deployment_or_resume_authorized",
})


def validate_authorization_payload(
    auth: Mapping[str, Any], auth_sha: str,
) -> None:
    top = exact_object(auth, AUTH_TOP_KEYS, "authorization")
    if top["schema"] != AUTH_SCHEMA or top["status"] != AUTH_STATUS:
        raise PreflightError("authorization schema/status mismatch")
    exact_utc(top["created_utc"], "authorization.created_utc")
    if (
        top["decision_id"] != EXPECTED_DECISION_ID
        or DECISION_RE.fullmatch(top["decision_id"]) is None
    ):
        raise PreflightError("authorization decision_id mismatch")
    exact_sha(auth_sha, "actual authorization SHA")
    for key in AUTH_TOP_KEYS:
        if key.endswith("_path"):
            exact_absolute_path(top[key], f"authorization.{key}")
        elif key.endswith("_sha256"):
            exact_sha(top[key], f"authorization.{key}")

    authority = exact_object(top["authority"], AUTHORITY_KEYS, "authorization.authority")
    expected_authority = {
        "preflight_launch_authorized": True,
        "transport_runtime_layout_authorized": False,
        "result_access_authorized": False,
        "signals_authorized": False,
        "deployment_or_resume_authorized": False,
    }
    for key, expected in expected_authority.items():
        exact_bool(authority[key], expected, f"authorization.authority.{key}")


BOOTSTRAP_CONTEXT_KEYS = frozenset({
    "protocol", "bootstrap_sha256", "proc_argv", "interpreter_fd",
    "source_fd", "authorization_fd", "interpreter_identity", "source_identity",
    "authorization_identity", "interpreter_sha256", "source_sha256",
    "authorization_sha256",
})


def validate_bootstrap_context(
    context: Mapping[str, Any], auth_sha: str,
) -> None:
    item = exact_object(context, BOOTSTRAP_CONTEXT_KEYS, "bootstrap_context")
    if item["protocol"] != "HELD_FD197_198_199_PRECOMPILE_ROOT_BOOTSTRAP_V3":
        raise PreflightError("bootstrap protocol mismatch")
    if item["bootstrap_sha256"] != ROOT_BOOTSTRAP_SHA256:
        raise PreflightError("bootstrap SHA differs from frozen source contract")
    if (
        item["interpreter_fd"] != HELD_INTERPRETER_FD
        or item["source_fd"] != HELD_PREFLIGHT_SOURCE_FD
        or item["authorization_fd"] != HELD_AUTHORIZATION_FD
    ):
        raise PreflightError("bootstrap fixed FD mismatch")
    interpreter_identity = FileIdentity.from_json(
        item["interpreter_identity"], "bootstrap.interpreter_identity"
    )
    source_identity = FileIdentity.from_json(
        item["source_identity"], "bootstrap.source_identity"
    )
    if interpreter_identity.mode not in {0o555, 0o755}:
        raise PreflightError("bootstrap interpreter mode must be 0555 or 0755")
    if source_identity.mode != 0o444:
        raise PreflightError("bootstrap preflight source mode must be 0444")
    exact_sha(item["interpreter_sha256"], "bootstrap.interpreter_sha256")
    exact_sha(item["source_sha256"], "bootstrap.source_sha256")
    if item["authorization_sha256"] != auth_sha:
        raise PreflightError("bootstrap authorization SHA mismatch")
    FileIdentity.from_json(item["authorization_identity"], "bootstrap.authorization_identity")
    argv = item["proc_argv"]
    if type(argv) is not list or len(argv) < 6 or argv[:5] != [
        "/proc/self/fd/197", "-I", "-B", "-S", "-c",
    ]:
        raise PreflightError("bootstrap exact /proc argv prefix mismatch")
    if sha256_bytes(argv[5].encode("utf-8")) != item["bootstrap_sha256"]:
        raise PreflightError("bootstrap executed text SHA mismatch")


class EvidenceLeaseSet:
    """All anchors remain open until a terminal evidence record is durable."""

    def __init__(self) -> None:
        self.files: list[FileLease] = []
        self.directories: list[DirectoryLease] = []

    def add_file(
        self, path: Path, digest: str, label: str, *, mode: int | None = 0o444,
    ) -> FileLease:
        lease = FileLease.open(path, digest, label, mode=mode)
        self.files.append(lease)
        return lease

    def add_directory(self, path: Path, label: str) -> DirectoryLease:
        lease = DirectoryLease.open(path, label)
        self.directories.append(lease)
        return lease

    def add_file_at(
        self, directory: DirectoryLease, name: str, digest: str, label: str,
    ) -> FileLease:
        if not name or "/" in name or name in {".", ".."}:
            raise PreflightError(f"{label}: top-level basename required")
        exact_sha(digest, f"{label}.sha256")
        parent_fd = os.dup(directory.fd)
        try:
            parent_identity = FileIdentity.from_stat(os.fstat(parent_fd))
            if parent_identity != directory.identity:
                raise PreflightError(f"{label}: package directory identity drift")
            fd = os.open(
                name, os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd
            )
            try:
                data, identity = read_stable_fd(fd, label)
                observed = sha256_bytes(data)
                if observed != digest:
                    raise PreflightError(f"{label}: SHA mismatch")
                lease = FileLease(
                    directory.path / name, fd, identity, observed,
                    parent_fd, parent_identity, data, 0o444,
                )
                lease.revalidate(label + ".initial")
                self.files.append(lease)
                return lease
            except BaseException:
                os.close(fd)
                raise
        except BaseException:
            os.close(parent_fd)
            raise

    def revalidate(self, phase: str) -> None:
        for index, lease in enumerate(self.directories):
            lease.revalidate(f"{phase}.directory[{index}]")
        for index, lease in enumerate(self.files):
            lease.revalidate(f"{phase}.file[{index}]")

    def close(self) -> None:
        for lease in reversed(self.files):
            lease.close()
        for lease in reversed(self.directories):
            lease.close()
        self.files.clear()
        self.directories.clear()


def _fresh_directory_members(directory_fd: int, label: str) -> frozenset[str]:
    cursor = os.open(
        ".", os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
        dir_fd=directory_fd,
    )
    try:
        names = os.listdir(cursor)
    finally:
        os.close(cursor)
    if any(type(name) is not str or not name or "/" in name for name in names):
        raise PreflightError(f"{label}: invalid directory entry")
    if len(names) != len(set(names)):
        raise PreflightError(f"{label}: duplicate directory entry")
    return frozenset(names)


def parse_sha256_index(data: bytes, expected_names: frozenset[str], label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PreflightError(f"{label}: index is not UTF-8") from exc
    if not text.endswith("\n") or "\r" in text:
        raise PreflightError(f"{label}: canonical LF-terminated index required")
    lines = text[:-1].split("\n") if text[:-1] else []
    result: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise PreflightError(f"{label}: malformed SHA line")
        digest = exact_sha(line[:64], f"{label}.sha256")
        name = line[66:]
        if not name or "/" in name or name in {".", ".."} or name in result:
            raise PreflightError(f"{label}: unsafe/duplicate index name")
        result[name] = digest
    if frozenset(result) != expected_names:
        raise PreflightError(f"{label}: exact member set mismatch")
    if lines != sorted(lines, key=lambda item: item[66:]):
        raise PreflightError(f"{label}: entries must be basename-sorted")
    return result


def _validate_all_false_authority(value: Any, label: str) -> None:
    item = exact_object(value, QA_AUTHORITY_KEYS, label)
    for key in QA_AUTHORITY_KEYS:
        exact_bool(item[key], False, f"{label}.{key}")


def _validate_all_false_object(
    value: Any, keys: frozenset[str], label: str,
) -> dict[str, Any]:
    item = exact_object(value, keys, label)
    for key in keys:
        exact_bool(item[key], False, f"{label}.{key}")
    return item


def _validate_zero_findings(value: Any, label: str) -> None:
    item = exact_object(value, set(ZERO_FINDINGS), label)
    for key in ZERO_FINDINGS:
        if exact_int(item[key], f"{label}.{key}") != 0:
            raise PreflightError(f"{label}.{key}: exact zero required")


def _validate_pass_counter(
    value: Any,
    keys: frozenset[str],
    hash_key: str,
    label: str,
) -> None:
    item = exact_object(value, keys, label)
    checked = exact_int(item["checked"], f"{label}.checked")
    failed = exact_int(item["failed"], f"{label}.failed")
    passed = exact_int(item["passed"], f"{label}.passed")
    if checked <= 0 or failed != 0 or passed != checked:
        raise PreflightError(f"{label}: exact all-pass counter required")
    exact_sha(item[hash_key], f"{label}.{hash_key}")


def _validate_manifest(
    data: bytes,
    *,
    label: str,
    schema: str,
    status: str,
    payload_names: frozenset[str],
    closure_names: frozenset[str],
    qa: bool,
) -> dict[str, Any]:
    value = strict_json_bytes(data, label)
    keys = {
        "schema", "status", "created_utc", "payload_file_count", "files",
        "closure_files_not_in_payload_manifest", "authority",
    }
    if qa:
        keys |= {"action_scoped_verdict", "finding_counts"}
    exact_object(value, keys, label)
    if value["schema"] != schema or value["status"] != status:
        raise PreflightError(f"{label}: schema/status mismatch")
    exact_utc(value["created_utc"], f"{label}.created_utc")
    if exact_int(value["payload_file_count"], f"{label}.payload_file_count") != len(payload_names):
        raise PreflightError(f"{label}: payload count mismatch")
    if type(value["files"]) is not list or len(value["files"]) != len(payload_names):
        raise PreflightError(f"{label}: payload records mismatch")
    observed: set[str] = set()
    ordered_names: list[str] = []
    for index, raw_record in enumerate(value["files"]):
        record = exact_object(raw_record, MANIFEST_FILE_RECORD_KEYS, f"{label}.files[{index}]")
        name = exact_string(record["relative_path"], f"{label}.files[{index}].relative_path")
        if "/" in name or name in observed or name not in payload_names:
            raise PreflightError(f"{label}: unsafe/duplicate payload member")
        observed.add(name)
        ordered_names.append(name)
        exact_string(record["role"], f"{label}.files[{index}].role")
        exact_sha(record["sha256"], f"{label}.files[{index}].sha256")
        exact_int(record["size_bytes"], f"{label}.files[{index}].size_bytes")
    if observed != set(payload_names):
        raise PreflightError(f"{label}: manifest payload set mismatch")
    if ordered_names != sorted(ordered_names):
        raise PreflightError(f"{label}: payload records must be basename-sorted")
    closure = value["closure_files_not_in_payload_manifest"]
    if type(closure) is not list or closure != sorted(closure_names):
        raise PreflightError(f"{label}: closure member set/order mismatch")
    _validate_all_false_authority(value["authority"], f"{label}.authority")
    if qa:
        if value["action_scoped_verdict"] != QA_GO_VERDICT:
            raise PreflightError(f"{label}: exact QA GO absent")
        _validate_zero_findings(value["finding_counts"], f"{label}.finding_counts")
    return value


def _open_validate_one_closure(
    lease_set: EvidenceLeaseSet,
    binding: Mapping[str, Any],
    *,
    label: str,
    expected_members: frozenset[str],
    payload_members: frozenset[str],
    manifest_schema: str,
    status: str,
    receipt_name: str,
    receipt_schema: str,
    qa: bool,
    prepared_members: Mapping[str, FileLease] | None = None,
    prepared_binding: Mapping[str, Any] | None = None,
) -> dict[str, FileLease]:
    manifest_path = Path(exact_string(binding["bundle_manifest_path"], f"{label}.manifest_path"))
    receipt_path = Path(exact_string(binding["receipt_path"] if qa else binding["prepared_receipt_path"], f"{label}.receipt_path"))
    index_path = Path(exact_string(binding["sha256_index_path"], f"{label}.index_path"))
    root = manifest_path.parent
    if receipt_path.parent != root or index_path.parent != root:
        raise PreflightError(f"{label}: closure files do not share one root")
    if (
        manifest_path.name != "BUNDLE_MANIFEST.json"
        or receipt_path.name != receipt_name
        or index_path.name != "SHA256SUMS"
    ):
        raise PreflightError(f"{label}: canonical closure basenames required")
    directory = lease_set.add_directory(root, f"{label}.root")
    if directory.identity.mode != 0o555:
        raise PreflightError(f"{label}: frozen package directory mode 0555 required")
    if _fresh_directory_members(directory.fd, f"{label}.root") != expected_members:
        raise PreflightError(f"{label}: exact top-level package closure mismatch")
    manifest_lease = lease_set.add_file_at(
        directory, manifest_path.name, binding["bundle_manifest_sha256"],
        f"{label}.manifest",
    )
    receipt_sha_key = "receipt_sha256" if qa else "prepared_receipt_sha256"
    receipt_lease = lease_set.add_file_at(
        directory, receipt_path.name, binding[receipt_sha_key], f"{label}.receipt"
    )
    index_lease = lease_set.add_file_at(
        directory, index_path.name, binding["sha256_index_sha256"],
        f"{label}.index",
    )
    index = parse_sha256_index(
        index_lease.data, expected_members - {"SHA256SUMS"}, f"{label}.index"
    )
    if (
        index[manifest_path.name] != manifest_lease.digest
        or index[receipt_path.name] != receipt_lease.digest
    ):
        raise PreflightError(f"{label}: index does not bind closure files")
    manifest = _validate_manifest(
        manifest_lease.data,
        label=f"{label}.manifest",
        schema=manifest_schema,
        status=status,
        payload_names=payload_members,
        closure_names=expected_members - payload_members,
        qa=qa,
    )
    records = {record["relative_path"]: record for record in manifest["files"]}
    opened: dict[str, FileLease] = {
        manifest_path.name: manifest_lease,
        receipt_path.name: receipt_lease,
        index_path.name: index_lease,
    }
    for name in sorted(payload_members):
        record = records[name]
        if index[name] != record["sha256"]:
            raise PreflightError(f"{label}: manifest/index SHA mismatch for {name}")
        member = lease_set.add_file_at(
            directory, name, record["sha256"], f"{label}.payload.{name}"
        )
        if member.identity.size_bytes != record["size_bytes"]:
            raise PreflightError(f"{label}: manifest size mismatch for {name}")
        opened[name] = member
    receipt = strict_json_bytes(receipt_lease.data, f"{label}.receipt")
    if receipt.get("schema") != receipt_schema or receipt.get("status") != status:
        raise PreflightError(f"{label}: receipt schema/status mismatch")
    _validate_all_false_authority(receipt.get("authority"), f"{label}.receipt.authority")
    exact_utc(receipt.get("created_utc"), f"{label}.receipt.created_utc")
    exact_string(receipt.get("next_legal_action"), f"{label}.receipt.next_legal_action")
    if qa:
        expected_keys = {
            "schema", "status", "created_utc", "qa_directory",
            "action_scoped_verdict", "audited_package", "qa_artifacts",
            "independent_validation", "finding_counts", "authority", "scope",
            "next_legal_action",
        }
        exact_object(receipt, expected_keys, f"{label}.receipt")
        if receipt["action_scoped_verdict"] != QA_GO_VERDICT:
            raise PreflightError(f"{label}: receipt exact scoped GO absent")
        _validate_zero_findings(
            receipt["finding_counts"], f"{label}.receipt.finding_counts"
        )
        if receipt["qa_directory"] != root.name:
            raise PreflightError(f"{label}: receipt QA directory mismatch")
        _validate_all_false_object(
            receipt["scope"], QA_SCOPE_KEYS, f"{label}.receipt.scope"
        )
        if type(receipt["independent_validation"]) is not dict or not receipt["independent_validation"]:
            raise PreflightError(f"{label}: nonempty independent validation required")
        for key, outcome in receipt["independent_validation"].items():
            exact_string(key, f"{label}.receipt.independent_validation.key")
            text = exact_string(
                outcome, f"{label}.receipt.independent_validation.{key}"
            )
            if text.startswith(("FAIL", "NO_GO")):
                raise PreflightError(
                    f"{label}: independent validation contains a failure"
                )
        if prepared_members is None or prepared_binding is None:
            raise PreflightError(f"{label}: prepared closure cross-binding absent")
        audited = exact_object(
            receipt["audited_package"], QA_AUDITED_PACKAGE_KEYS,
            f"{label}.receipt.audited_package",
        )
        expected_audited = {
            "bundle_manifest_sha256": prepared_binding["bundle_manifest_sha256"],
            "contract_sha256": prepared_members[
                "RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json"
            ].digest,
            "directory": Path(prepared_binding["bundle_manifest_path"]).parent.name,
            "evidence_bindings_sha256": prepared_members[
                "UPSTREAM_EVIDENCE_BINDINGS_V3.json"
            ].digest,
            "prepared_receipt_sha256": prepared_binding["prepared_receipt_sha256"],
            "script_sha256": prepared_members[
                "run_result_free_mars_native_preflight_v3.py"
            ].digest,
            "sha256_index_sha256": prepared_binding["sha256_index_sha256"],
            "test_sha256": prepared_members[
                "test_result_free_mars_native_preflight_v3_synthetic.py"
            ].digest,
        }
        if audited != expected_audited:
            raise PreflightError(f"{label}: audited package cross-binding mismatch")
        artifacts = exact_object(
            receipt["qa_artifacts"], QA_ARTIFACT_KEYS,
            f"{label}.receipt.qa_artifacts",
        )
        artifact_names = {
            "closure": "PACKAGE_CLOSURE_QA.json",
            "harness": "QA_HARNESS_OR_METHOD.md",
            "log": "COMMAND_LOG.txt",
            "manifest": "BUNDLE_MANIFEST.json",
            "output": "INDEPENDENT_QA_OUTPUT.json",
            "report": "INDEPENDENT_QA_REPORT_CN.md",
        }
        for stem, name in artifact_names.items():
            record = exact_object(
                artifacts[stem], QA_ARTIFACT_RECORD_KEYS,
                f"{label}.receipt.qa_artifacts.{stem}",
            )
            exact_string(record["path"], f"{label}.receipt.qa_artifacts.{stem}.path")
            exact_sha(record["sha256"], f"{label}.receipt.qa_artifacts.{stem}.sha256")
            if record != {"path": name, "sha256": opened[name].digest}:
                raise PreflightError(f"{label}: QA artifact cross-binding mismatch: {stem}")
    else:
        expected_keys = {
            "schema", "status", "created_utc", "package_directory",
            "package_closure", "locked_tools", "author_validation", "scope",
            "authority", "next_legal_action",
        }
        exact_object(receipt, expected_keys, f"{label}.receipt")
        if receipt["package_directory"] != root.name:
            raise PreflightError(f"{label}: receipt package directory mismatch")
        closure = exact_object(
            receipt["package_closure"], PREPARED_PACKAGE_CLOSURE_KEYS,
            f"{label}.receipt.package_closure",
        )
        if closure != {
            "bundle_manifest_sha256": manifest_lease.digest,
            "payload_file_count": len(payload_members),
            "sha_index_listed_count_expected": len(expected_members) - 1,
            "top_level_file_count_expected": len(expected_members),
        }:
            raise PreflightError(f"{label}: receipt package closure mismatch")
        locked = exact_object(
            receipt["locked_tools"], PREPARED_LOCKED_TOOLS_KEYS,
            f"{label}.receipt.locked_tools",
        )
        tool_names = {
            "preflight": "run_result_free_mars_native_preflight_v3.py",
            "synthetic_test": "test_result_free_mars_native_preflight_v3_synthetic.py",
        }
        for stem, name in tool_names.items():
            record = exact_object(
                locked[stem], LOCKED_TOOL_RECORD_KEYS,
                f"{label}.receipt.locked_tools.{stem}",
            )
            exact_string(record["path"], f"{label}.receipt.locked_tools.{stem}.path")
            exact_sha(record["sha256"], f"{label}.receipt.locked_tools.{stem}.sha256")
            exact_int(record["line_count"], f"{label}.receipt.locked_tools.{stem}.line_count")
            expected_record = {
                "path": name,
                "sha256": opened[name].digest,
                "line_count": len(opened[name].data.splitlines()),
            }
            if record != expected_record:
                raise PreflightError(f"{label}: locked tool cross-binding mismatch: {stem}")
        author = exact_object(
            receipt["author_validation"], AUTHOR_VALIDATION_KEYS,
            f"{label}.receipt.author_validation",
        )
        exact_string(author["darwin_actual"], f"{label}.receipt.author_validation.darwin_actual")
        exact_string(author["linux_xfs_actual"], f"{label}.receipt.author_validation.linux_xfs_actual")
        exact_bool(
            author["manifest_payload_hash_and_size_pass"], True,
            f"{label}.receipt.author_validation.manifest_payload_hash_and_size_pass",
        )
        exact_bool(
            author["strict_json_parse_pass"], True,
            f"{label}.receipt.author_validation.strict_json_parse_pass",
        )
        _validate_pass_counter(
            author["source_compile"], COMPILE_VALIDATION_KEYS, "output_sha256",
            f"{label}.receipt.author_validation.source_compile",
        )
        if author["source_compile"]["checked"] != 2:
            raise PreflightError(f"{label}: source compile must be exact 2/2")
        _validate_pass_counter(
            author["synthetic_test"], SYNTHETIC_VALIDATION_KEYS,
            "raw_output_sha256", f"{label}.receipt.author_validation.synthetic_test",
        )
        if author["source_compile"]["output_sha256"] != opened[
            "AUTHOR_COMPILE_V3_OUTPUT.json"
        ].digest:
            raise PreflightError(f"{label}: compile evidence cross-binding mismatch")
        if author["synthetic_test"]["raw_output_sha256"] != opened[
            "AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json"
        ].digest:
            raise PreflightError(f"{label}: synthetic evidence cross-binding mismatch")
        _validate_all_false_object(
            receipt["scope"], PREPARED_SCOPE_KEYS, f"{label}.receipt.scope"
        )
    return opened


def _open_exact_upstream_index_closure(
    lease_set: EvidenceLeaseSet,
    directory: DirectoryLease,
    *,
    binding: Mapping[str, Any],
    label: str,
    expected_top_members: frozenset[str],
    expected_index_members: frozenset[str],
    anchor_names: Mapping[str, str],
    unindexed_anchor_stems: frozenset[str] = frozenset(),
) -> dict[str, FileLease]:
    """Open every exact upstream member, including index-only evidence.

    The separately frozen SHA index binds all indexed bytes; explicitly signed
    anchors must agree with that index, while unindexed receipt anchors remain
    independently SHA-bound.  The directory and every member stay leased until
    the preflight reaches a durable terminal and passes final continuity checks.
    """

    if directory.identity.mode != 0o555:
        raise PreflightError(f"{label}: frozen directory mode 0555 required")
    observed = _fresh_directory_members(directory.fd, f"{label}.root")
    if observed != expected_top_members:
        raise PreflightError(f"{label}: exact top-level closure mismatch")
    if len(observed) != len(expected_top_members):
        raise PreflightError(f"{label}: exact top-level count mismatch")

    index_path = exact_absolute_path(
        binding["sha256_index_path"], f"{label}.sha256_index_path"
    )
    if index_path.parent != directory.path or index_path.name != "SHA256SUMS":
        raise PreflightError(f"{label}: canonical SHA index path mismatch")
    index_lease = lease_set.add_file_at(
        directory,
        "SHA256SUMS",
        binding["sha256_index_sha256"],
        f"{label}.sha256_index",
    )
    index = parse_sha256_index(
        index_lease.data, expected_index_members, f"{label}.sha256_index"
    )
    opened: dict[str, FileLease] = {"SHA256SUMS": index_lease}
    for name in sorted(expected_index_members):
        opened[name] = lease_set.add_file_at(
            directory, name, index[name], f"{label}.member.{name}"
        )

    for stem, expected_name in anchor_names.items():
        path = exact_absolute_path(
            binding[f"{stem}_path"], f"{label}.{stem}_path"
        )
        digest = exact_sha(binding[f"{stem}_sha256"], f"{label}.{stem}_sha256")
        if path.parent != directory.path or path.name != expected_name:
            raise PreflightError(f"{label}: canonical anchor path mismatch: {stem}")
        if stem == "sha256_index":
            if digest != index_lease.digest:
                raise PreflightError(f"{label}: SHA index anchor mismatch")
            continue
        if stem in unindexed_anchor_stems:
            opened[expected_name] = lease_set.add_file_at(
                directory, expected_name, digest, f"{label}.anchor.{stem}"
            )
        elif index.get(expected_name) != digest:
            raise PreflightError(f"{label}: indexed anchor mismatch: {stem}")
    return opened


def _relaxed_json_object(data: bytes, label: str) -> dict[str, Any]:
    """Parse frozen historical JSON without imposing canonical whitespace."""

    if data.startswith(b"\xef\xbb\xbf"):
        raise PreflightError(f"{label}: BOM forbidden")
    try:
        value = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=_pairs_no_duplicate,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        raise PreflightError(f"{label}: strict JSON failure: {exc}") from exc
    if type(value) is not dict:
        raise PreflightError(f"{label}: top-level object required")
    _validate_json_types(value, label)
    return value


def _validate_generic_all_false(value: Any, label: str) -> None:
    if (
        type(value) is not dict
        or not value
        or any(type(item) is not bool or item for item in value.values())
    ):
        raise PreflightError(f"{label}: exact all-false authority required")


def _validate_v10_manifest_semantics(
    manifest: Mapping[str, Any],
    members: Mapping[str, FileLease],
    *,
    label: str,
    schema: str,
    status: str,
    payload_names: frozenset[str],
    closure_names: list[str],
    qa: bool,
) -> None:
    required = {
        "schema", "status", "created_utc", "payload_file_count", "files",
        "closure_files_not_in_payload_manifest", "authority",
    }
    if qa:
        required |= {"action_scoped_verdict", "finding_counts"}
    exact_object(manifest, required, label)
    if manifest["schema"] != schema or manifest["status"] != status:
        raise PreflightError(f"{label}: schema/status mismatch")
    exact_utc(manifest["created_utc"], f"{label}.created_utc")
    if (
        manifest["payload_file_count"] != len(payload_names)
        or manifest["closure_files_not_in_payload_manifest"] != closure_names
    ):
        raise PreflightError(f"{label}: payload/closure count mismatch")
    _validate_generic_all_false(manifest["authority"], f"{label}.authority")
    if qa:
        if manifest["action_scoped_verdict"] != V10_QA_ACTION_SCOPED_VERDICT:
            raise PreflightError(f"{label}: exact scoped GO absent")
        _validate_zero_findings(manifest["finding_counts"], f"{label}.findings")
    records = manifest["files"]
    if type(records) is not list or len(records) != len(payload_names):
        raise PreflightError(f"{label}: payload records mismatch")
    observed: set[str] = set()
    for index, record in enumerate(records):
        item = exact_object(
            record,
            MANIFEST_FILE_RECORD_KEYS,
            f"{label}.files[{index}]",
        )
        name = exact_string(item["relative_path"], f"{label}.files[{index}].path")
        exact_string(item["role"], f"{label}.files[{index}].role")
        if name not in payload_names or name in observed:
            raise PreflightError(f"{label}: duplicate/undeclared payload")
        exact_sha(item["sha256"], f"{label}.files[{index}].sha256")
        exact_int(item["size_bytes"], f"{label}.files[{index}].size")
        if (
            item["sha256"] != members[name].digest
            or item["size_bytes"] != members[name].identity.size_bytes
        ):
            raise PreflightError(f"{label}: payload hash/size mismatch")
        observed.add(name)
    if observed != set(payload_names):
        raise PreflightError(f"{label}: payload closure mismatch")


def validate_v10_upstream_semantics(
    package_members: Mapping[str, FileLease],
    audit_members: Mapping[str, FileLease],
    bindings: FrozenBindings,
) -> None:
    """Validate critical v10 prepared/QA semantics in addition to exact bytes."""

    package_payload = V10_PACKAGE_INDEX_MEMBERS - {
        "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
    }
    package_manifest = _relaxed_json_object(
        package_members["BUNDLE_MANIFEST.json"].data,
        "v10_package.manifest",
    )
    _validate_v10_manifest_semantics(
        package_manifest,
        package_members,
        label="v10_package.manifest",
        schema=V10_PREPARED_MANIFEST_SCHEMA,
        status=V10_PREPARED_STATUS,
        payload_names=package_payload,
        closure_names=[
            "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
            "SHA256SUMS",
        ],
        qa=False,
    )
    package_receipt = _relaxed_json_object(
        package_members["PREPARED_RESULT_FREE_RECEIPT.json"].data,
        "v10_package.receipt",
    )
    if (
        package_receipt.get("schema") != V10_PREPARED_RECEIPT_SCHEMA
        or package_receipt.get("status") != V10_PREPARED_STATUS
        or package_receipt.get("package_directory")
        != Path(bindings.v10_package["builder_path"]).parent.name
        or package_receipt.get("package_closure") != {
            "bundle_manifest_sha256": bindings.v10_package[
                "bundle_manifest_sha256"
            ],
            "payload_file_count": 12,
            "sha_index_listed_count_expected": 14,
            "top_level_file_count_expected": 15,
        }
    ):
        raise PreflightError("v10 prepared receipt semantic mismatch")
    _validate_generic_all_false(
        package_receipt.get("authority"), "v10_package.receipt.authority"
    )

    audit_payload = V10_QA_INDEX_MEMBERS - {"BUNDLE_MANIFEST.json"}
    audit_manifest = _relaxed_json_object(
        audit_members["BUNDLE_MANIFEST.json"].data,
        "v10_audit.manifest",
    )
    _validate_v10_manifest_semantics(
        audit_manifest,
        audit_members,
        label="v10_audit.manifest",
        schema=V10_QA_MANIFEST_SCHEMA,
        status=V10_QA_MANIFEST_STATUS,
        payload_names=audit_payload,
        closure_names=[
            "BUNDLE_MANIFEST.json", "INDEPENDENT_QA_RECEIPT.json", "SHA256SUMS",
        ],
        qa=True,
    )
    audit_receipt = strict_json_bytes(
        audit_members["INDEPENDENT_QA_RECEIPT.json"].data,
        "v10_audit.receipt",
    )
    if (
        audit_receipt.get("schema") != V10_QA_RECEIPT_SCHEMA
        or audit_receipt.get("status") != V10_QA_RECEIPT_STATUS
        or audit_receipt.get("qa_directory")
        != Path(bindings.v10_audit["receipt_path"]).parent.name
        or audit_receipt.get("action_scoped_verdict")
        != V10_QA_ACTION_SCOPED_VERDICT
    ):
        raise PreflightError("v10 independent QA receipt semantic mismatch")
    _validate_zero_findings(
        audit_receipt.get("finding_counts"), "v10_audit.receipt.findings"
    )
    _validate_generic_all_false(
        audit_receipt.get("authority"), "v10_audit.receipt.authority"
    )
    audited = audit_receipt.get("audited_candidate")
    expected_audited = {
        "builder_sha256": bindings.v10_package["builder_sha256"],
        "bundle_manifest_sha256": bindings.v10_package["bundle_manifest_sha256"],
        "directory": Path(bindings.v10_package["builder_path"]).parent.name,
        "indexed_count": 14,
        "payload_count": 12,
        "prepared_receipt_sha256": bindings.v10_package["prepared_receipt_sha256"],
        "sha256_index_sha256": bindings.v10_package["sha256_index_sha256"],
        "smoke_sha256": bindings.v10_package["smoke_sha256"],
        "smoke_test_sha256": bindings.v10_package["smoke_test_sha256"],
        "test_sha256": bindings.v10_package["builder_test_sha256"],
        "top_level_count": 15,
    }
    if audited != expected_audited:
        raise PreflightError("v10 QA audited-candidate cross-binding mismatch")


def open_full_evidence_lease(
    bindings: FrozenBindings,
    *,
    preflight_source_sha256: str,
    interpreter_sha256: str,
) -> EvidenceLeaseSet:
    """Open parent/site/v10 dirs and every package/audit/auth file once."""

    lease = EvidenceLeaseSet()
    try:
        require_frozen_bindings(bindings)
        validate_frozen_bindings_shape(bindings, require_self=True)
        exact_sha(preflight_source_sha256, "held preflight source SHA")
        exact_sha(interpreter_sha256, "held interpreter SHA")
        lease.add_directory(EXPECTED_PRODUCTION_PARENT, "production_parent")
        lease.add_directory(EXPECTED_SOURCE_SITE_PACKAGES, "source_site_packages")
        v10_package_directory = lease.add_directory(
            Path(bindings.v10_package["builder_path"]).parent,
            "v10_package_root",
        )
        prepared_members = _open_validate_one_closure(
            lease,
            bindings.preflight_package,
            label="preflight_package",
            expected_members=PREPARED_TOP_LEVEL_MEMBERS,
            payload_members=PREPARED_MANIFEST_PAYLOAD_MEMBERS,
            manifest_schema=PREPARED_MANIFEST_SCHEMA,
            status=PREPARED_STATUS,
            receipt_name="PREPARED_RESULT_FREE_RECEIPT.json",
            receipt_schema=PREPARED_RECEIPT_SCHEMA,
            qa=False,
        )
        if prepared_members[
            "run_result_free_mars_native_preflight_v3.py"
        ].digest != preflight_source_sha256:
            raise PreflightError("prepared package source does not equal held FD198")
        _open_validate_one_closure(
            lease,
            bindings.preflight_audit,
            label="preflight_audit",
            expected_members=QA_TOP_LEVEL_MEMBERS,
            payload_members=QA_MANIFEST_PAYLOAD_MEMBERS,
            manifest_schema=QA_MANIFEST_SCHEMA,
            status=QA_GO_STATUS,
            receipt_name="INDEPENDENT_QA_RECEIPT.json",
            receipt_schema=QA_RECEIPT_SCHEMA,
            qa=True,
            prepared_members=prepared_members,
            prepared_binding=bindings.preflight_package,
        )
        v10_package_members = _open_exact_upstream_index_closure(
            lease,
            v10_package_directory,
            binding=bindings.v10_package,
            label="v10_package",
            expected_top_members=V10_PACKAGE_TOP_LEVEL_MEMBERS,
            expected_index_members=V10_PACKAGE_INDEX_MEMBERS,
            anchor_names={
                "builder": "build_result_free_transport_runtime_v10.py",
                "builder_test": "test_transport_runtime_layout_builder_v10_synthetic.py",
                "smoke": "result_free_runtime_smoke_v10.py",
                "smoke_test": "test_result_free_runtime_smoke_v10_synthetic.py",
                "prepared_receipt": "PREPARED_RESULT_FREE_RECEIPT.json",
                "bundle_manifest": "BUNDLE_MANIFEST.json",
                "sha256_index": "SHA256SUMS",
            },
        )
        v10_audit_root = Path(bindings.v10_audit["receipt_path"]).parent
        v10_audit_directory = lease.add_directory(
            v10_audit_root, "v10_independent_audit_root"
        )
        v10_audit_members = _open_exact_upstream_index_closure(
            lease,
            v10_audit_directory,
            binding=bindings.v10_audit,
            label="v10_independent_audit",
            expected_top_members=V10_QA_TOP_LEVEL_MEMBERS,
            expected_index_members=V10_QA_INDEX_MEMBERS,
            anchor_names={
                "report": "INDEPENDENT_QA_REPORT_CN.md",
                "receipt": "INDEPENDENT_QA_RECEIPT.json",
                "output": "INDEPENDENT_QA_OUTPUT.json",
                "log": "COMMAND_LOG.txt",
                "harness": "INDEPENDENT_QA_HARNESS.py",
                "closure": "PACKAGE_CLOSURE_QA.json",
                "bundle_manifest": "BUNDLE_MANIFEST.json",
                "sha256_index": "SHA256SUMS",
            },
            unindexed_anchor_stems=frozenset({"receipt"}),
        )
        validate_v10_upstream_semantics(
            v10_package_members, v10_audit_members, bindings
        )
        lease.add_file(
            EXPECTED_SOURCE_PYTHON, interpreter_sha256,
            "source_python", mode=None,
        )
        lease.revalidate("full_lease_opened")
        return lease
    except BaseException:
        lease.close()
        raise


def _is_at_or_below(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def reject_compatibility_aliases(
    compatibility_root: Path,
    compatibility_journal: Path,
    *,
    allowed_work_root: Path = EXPECTED_PREFLIGHT_WORK_ROOT,
    canonical_root: Path = EXPECTED_PRODUCTION_FINAL_ROOT,
    canonical_parent: Path = EXPECTED_PRODUCTION_PARENT,
    canonical_journal: Path = EXPECTED_PRODUCTION_JOURNAL,
) -> None:
    """Reject lexical and resolved aliases of the production tree."""

    candidates = (compatibility_root, compatibility_journal)
    for candidate in candidates:
        if not candidate.is_absolute() or os.path.normpath(os.fspath(candidate)) != os.fspath(candidate):
            raise PreflightError("compatibility target is not canonical absolute")
        if not _is_at_or_below(candidate, allowed_work_root) or candidate == allowed_work_root:
            raise PreflightError("compatibility target is outside fixed work root")
        if candidate in {canonical_root, canonical_parent, canonical_journal}:
            raise PreflightError("compatibility target is canonical production path")
        if _is_at_or_below(candidate, canonical_parent):
            raise PreflightError("compatibility target is under canonical production parent")
        resolved = Path(os.path.realpath(candidate))
        resolved_parent = Path(os.path.realpath(canonical_parent))
        resolved_root = Path(os.path.realpath(canonical_root))
        resolved_journal = Path(os.path.realpath(canonical_journal))
        if (
            resolved in {resolved_parent, resolved_root, resolved_journal}
            or _is_at_or_below(resolved, resolved_parent)
        ):
            raise PreflightError("compatibility target resolves into production tree")
        if candidate.exists() and candidate.is_symlink():
            raise PreflightError("compatibility target symlink forbidden")


def _assert_absent_at(parent_fd: int, name: str, label: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise PreflightError(f"{label} must remain absent")


def assert_production_absent(parent_fd: int) -> None:
    _assert_absent_at(parent_fd, EXPECTED_PRODUCTION_FINAL_ROOT.name, "production ROOT")
    _assert_absent_at(parent_fd, EXPECTED_PRODUCTION_JOURNAL.name, "production journal")


def make_compatibility_request(auth_sha: str) -> dict[str, Any]:
    exact_sha(auth_sha, "authorization_sha256")
    return {
        "schema": API_SCHEMA,
        "scope": API_SCOPE,
        "decision_id": EXPECTED_DECISION_ID,
        "authorization_sha256": auth_sha,
        "compatibility_root": os.fspath(EXPECTED_COMPATIBILITY_ROOT),
        "compatibility_journal": os.fspath(EXPECTED_COMPATIBILITY_JOURNAL),
        "canonical_production_final_root_forbidden": os.fspath(EXPECTED_PRODUCTION_FINAL_ROOT),
        "canonical_production_journal_forbidden": os.fspath(EXPECTED_PRODUCTION_JOURNAL),
        "canonical_production_parent_forbidden": os.fspath(EXPECTED_PRODUCTION_PARENT),
        "publication_requirements": {
            "real_linux_renameat2_noreplace": True,
            "real_linux_xfs_otmpfile_procfd_linkat": True,
            "pathname_fallback_allowed": False,
        },
        "authority": {
            "not_production_build": True,
            "production_root_write_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "controller_or_resume_authorized": False,
        },
    }


API_RESULT_KEYS = frozenset({
    "schema", "status", "scope", "decision_id", "authorization_sha256",
    "compatibility_root", "compatibility_journal", "publication",
    "production_guards", "result_accessed", "signals_sent",
    "external_processes_inspected", "controller_or_resume_executed",
})


def validate_compatibility_result(result: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    item = exact_object(result, API_RESULT_KEYS, "v10 compatibility result")
    if item["schema"] != API_SCHEMA or item["status"] != API_STATUS:
        raise PreflightError("v10 compatibility result schema/status mismatch")
    for key in ("scope", "decision_id", "authorization_sha256", "compatibility_root", "compatibility_journal"):
        if item[key] != request[key]:
            raise PreflightError(f"v10 compatibility result request mismatch: {key}")
    publication = exact_object(
        item["publication"],
        {"renameat2_noreplace", "otmpfile_procfd_linkat", "pathname_fallback_used"},
        "v10 compatibility result.publication",
    )
    exact_bool(publication["renameat2_noreplace"], True, "publication.renameat2")
    exact_bool(publication["otmpfile_procfd_linkat"], True, "publication.otmpfile")
    exact_bool(publication["pathname_fallback_used"], False, "publication.fallback")
    guards = exact_object(
        item["production_guards"],
        {"final_root_absent_before_after", "journal_absent_before_after", "parent_inode_held", "canonical_alias_rejected"},
        "v10 compatibility result.production_guards",
    )
    for key in guards:
        exact_bool(guards[key], True, f"production_guards.{key}")
    for key in (
        "result_accessed", "signals_sent", "external_processes_inspected",
        "controller_or_resume_executed",
    ):
        exact_bool(item[key], False, f"v10 compatibility result.{key}")
    return item


def make_result_record(
    result: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    validated = validate_compatibility_result(result, request)
    return {
        "schema": RESULT_SCHEMA,
        "status": RESULT_STATUS,
        "decision_id": EXPECTED_DECISION_ID,
        "authorization_sha256": request["authorization_sha256"],
        "compatibility_request_sha256": sha256_bytes(
            canonical_json_bytes(dict(request))
        ),
        "compatibility_result": dict(validated),
        "production_build_executed": False,
        "production_root_or_journal_touched": False,
        "result_accessed": False,
        "signals_sent": False,
    }


def validate_result_record(
    value: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    item = exact_object(
        value,
        {
            "schema", "status", "decision_id", "authorization_sha256",
            "compatibility_request_sha256", "compatibility_result",
            "production_build_executed", "production_root_or_journal_touched",
            "result_accessed", "signals_sent",
        },
        "compatibility result record",
    )
    if item["schema"] != RESULT_SCHEMA or item["status"] != RESULT_STATUS:
        raise PreflightError("compatibility result record schema/status mismatch")
    if item["decision_id"] != EXPECTED_DECISION_ID:
        raise PreflightError("compatibility result record decision mismatch")
    if item["authorization_sha256"] != request["authorization_sha256"]:
        raise PreflightError("compatibility result record authorization mismatch")
    if item["compatibility_request_sha256"] != sha256_bytes(
        canonical_json_bytes(dict(request))
    ):
        raise PreflightError("compatibility result record request mismatch")
    for key in (
        "production_build_executed", "production_root_or_journal_touched",
        "result_accessed", "signals_sent",
    ):
        exact_bool(item[key], False, f"compatibility result record.{key}")
    validate_compatibility_result(item["compatibility_result"], request)
    return item


def invoke_v10_scoped_native_compatibility(
    builder: Any,
    request: Mapping[str, Any],
    *,
    parent_fd: int,
    work_root_fd: int,
) -> dict[str, Any]:
    """Call only the future v10 noncanonical real-native compatibility API."""

    reject_compatibility_aliases(
        Path(request["compatibility_root"]), Path(request["compatibility_journal"])
    )
    if getattr(builder, "NATIVE_COMPATIBILITY_API_SCHEMA", None) != API_SCHEMA:
        raise PreflightBlocked("v10 scoped native compatibility API schema is absent/unbound")
    api = getattr(builder, API_FUNCTION, None)
    if not callable(api):
        raise PreflightBlocked("v10 scoped native compatibility API callable is absent")
    rename_impl = getattr(builder, "renameat2_noreplace", None)
    terminal_impl = getattr(builder, "publish_terminal_linux_otmpfile_noreplace", None)
    if not callable(rename_impl) or not callable(terminal_impl):
        raise PreflightBlocked("v10 real renameat2/O_TMPFILE implementations are absent")
    result = api(
        request=dict(request),
        production_parent_fd=parent_fd,
        compatibility_work_root_fd=work_root_fd,
        rename_impl=rename_impl,
        terminal_publish_impl=terminal_impl,
    )
    return validate_compatibility_result(result, request)


def require_linux_xfs_directory_fd(directory_fd: int, label: str) -> None:
    """Require a held Linux XFS directory before any contract mutation."""

    if not sys.platform.startswith("linux") or O_TMPFILE == 0:
        raise PreflightBlocked(f"{label}: Linux O_TMPFILE runtime required")
    info = os.fstat(directory_fd)
    if not stat.S_ISDIR(info.st_mode):
        raise PreflightError(f"{label}: held directory FD required")
    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs
    fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    fstatfs.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(256)
    if fstatfs(directory_fd, ctypes.byref(buffer)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), label)
    magic = ctypes.c_long.from_buffer_copy(
        buffer.raw[:ctypes.sizeof(ctypes.c_long)]
    ).value
    if magic != XFS_SUPER_MAGIC:
        raise PreflightBlocked(f"{label}: exact XFS filesystem required")


def publish_otmpfile_noreplace(
    directory_fd: int,
    name: str,
    data: bytes,
    *,
    after_link_before_dir_fsync_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """The only production evidence publisher; there is no pathname fallback."""

    if not name or "/" in name or name in {".", ".."}:
        raise PreflightError("canonical evidence basename required")
    require_linux_xfs_directory_fd(directory_fd, "evidence publisher")
    fd = os.open(".", os.O_WRONLY | O_TMPFILE | O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 0:
            raise PreflightError("O_TMPFILE evidence is not an anonymous regular inode")
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise PreflightError("O_TMPFILE evidence short write")
            offset += written
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        libc = ctypes.CDLL(None, use_errno=True)
        linkat = libc.linkat
        linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        linkat.restype = ctypes.c_int
        source = f"/proc/self/fd/{fd}".encode("ascii")
        if linkat(AT_FDCWD, source, directory_fd, name.encode("utf-8"), AT_SYMLINK_FOLLOW) != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(error, os.strerror(error), name)
            raise OSError(error, os.strerror(error), name)
        if after_link_before_dir_fsync_hook is not None:
            after_link_before_dir_fsync_hook()
        os.fsync(directory_fd)
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        held = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o444
            or info.st_nlink != 1
            or info.st_size != len(data)
            or (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino)
            or held.st_nlink != 1
        ):
            raise PreflightError("published evidence identity mismatch")
        probe = os.open(name, os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=directory_fd)
        try:
            observed, observed_identity = read_stable_fd(probe, name)
            if observed != data or observed_identity.inode != held.st_ino:
                raise PreflightError("published evidence bytes/inode mismatch")
        finally:
            os.close(probe)
        return {
            "method": PRODUCTION_EVIDENCE_PUBLICATION_METHOD,
            "canonical_visibility_rule": EVIDENCE_VISIBILITY_RULE,
            "name": name,
            "sha256": sha256_bytes(data),
            "size_bytes": len(data),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": "0444",
            "nlink": 1,
        }
    finally:
        os.close(fd)


def make_begin(auth_sha: str) -> dict[str, Any]:
    return {
        "schema": BEGIN_SCHEMA,
        "status": BEGIN_STATUS,
        "decision_id": EXPECTED_DECISION_ID,
        "authorization_sha256": exact_sha(auth_sha, "authorization_sha256"),
        "production_final_root": os.fspath(EXPECTED_PRODUCTION_FINAL_ROOT),
        "production_journal": os.fspath(EXPECTED_PRODUCTION_JOURNAL),
        "preflight_work_root": os.fspath(EXPECTED_PREFLIGHT_WORK_ROOT),
        "compatibility_scope": API_SCOPE,
        "result_accessed": False,
        "signals_sent": False,
    }


def make_intent(begin: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    begin_bytes = canonical_json_bytes(dict(begin))
    return {
        "schema": INTENT_SCHEMA,
        "status": INTENT_STATUS,
        "decision_id": EXPECTED_DECISION_ID,
        "authorization_sha256": begin["authorization_sha256"],
        "begin_sha256": sha256_bytes(begin_bytes),
        "compatibility_request_sha256": sha256_bytes(canonical_json_bytes(dict(request))),
        "compatibility_root": request["compatibility_root"],
        "compatibility_journal": request["compatibility_journal"],
        "scope": API_SCOPE,
        "production_build_authorized": False,
        "result_accessed": False,
    }


def make_terminal(
    *,
    passed: bool,
    auth_sha: str,
    begin_sha: str,
    intent_sha: str | None,
    result_sha: str | None,
    phase: str,
    error_type: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    if passed and (intent_sha is None or result_sha is None or error_type or error_message):
        raise PreflightError("PASS terminal requires intent/result and no error")
    if not passed and (not error_type or not error_message):
        raise PreflightError("FAIL terminal requires explicit error")
    return {
        "schema": PASS_SCHEMA if passed else FAIL_SCHEMA,
        "status": PASS_STATUS if passed else FAIL_STATUS,
        "decision_id": EXPECTED_DECISION_ID,
        "authorization_sha256": exact_sha(auth_sha, "authorization_sha256"),
        "begin_sha256": exact_sha(begin_sha, "begin_sha256"),
        "intent_sha256": intent_sha or "ABSENT_BEFORE_INTENT",
        "compatibility_result_sha256": result_sha or "ABSENT_NO_COMPATIBILITY_RESULT",
        "phase": exact_string(phase, "phase"),
        "error_type": error_type,
        "error_message": error_message,
        "scope": API_SCOPE,
        "production_build_executed": False,
        "production_root_or_journal_touched": False,
        "result_accessed": False,
        "signals_sent": False,
        "external_processes_inspected": False,
        "controller_or_resume_executed": False,
        "publication_method": PRODUCTION_EVIDENCE_PUBLICATION_METHOD,
        "canonical_visibility_rule": EVIDENCE_VISIBILITY_RULE,
    }


def validate_record(value: Mapping[str, Any]) -> None:
    schema = value.get("schema")
    if schema == BEGIN_SCHEMA:
        expected = set(make_begin("0" * 64))
        exact_object(value, expected, "BEGIN")
        if value["status"] != BEGIN_STATUS:
            raise PreflightError("BEGIN status mismatch")
        if value["production_final_root"] != os.fspath(EXPECTED_PRODUCTION_FINAL_ROOT):
            raise PreflightError("BEGIN production root mismatch")
        if value["production_journal"] != os.fspath(EXPECTED_PRODUCTION_JOURNAL):
            raise PreflightError("BEGIN production journal mismatch")
        if value["preflight_work_root"] != os.fspath(EXPECTED_PREFLIGHT_WORK_ROOT):
            raise PreflightError("BEGIN work root mismatch")
        for key in ("result_accessed", "signals_sent"):
            exact_bool(value[key], False, f"BEGIN.{key}")
    elif schema == INTENT_SCHEMA:
        expected = {
            "schema", "status", "decision_id", "authorization_sha256",
            "begin_sha256", "compatibility_request_sha256", "compatibility_root",
            "compatibility_journal", "scope", "production_build_authorized",
            "result_accessed",
        }
        exact_object(value, expected, "INTENT")
        if value["status"] != INTENT_STATUS:
            raise PreflightError("INTENT status mismatch")
        exact_sha(value["begin_sha256"], "INTENT.begin_sha256")
        exact_sha(
            value["compatibility_request_sha256"],
            "INTENT.compatibility_request_sha256",
        )
        if value["compatibility_root"] != os.fspath(EXPECTED_COMPATIBILITY_ROOT):
            raise PreflightError("INTENT compatibility root mismatch")
        if value["compatibility_journal"] != os.fspath(EXPECTED_COMPATIBILITY_JOURNAL):
            raise PreflightError("INTENT compatibility journal mismatch")
        exact_bool(
            value["production_build_authorized"], False,
            "INTENT.production_build_authorized",
        )
        exact_bool(value["result_accessed"], False, "INTENT.result_accessed")
    elif schema in {PASS_SCHEMA, FAIL_SCHEMA}:
        expected = {
            "schema", "status", "decision_id", "authorization_sha256",
            "begin_sha256", "intent_sha256", "compatibility_result_sha256",
            "phase", "error_type", "error_message", "scope",
            "production_build_executed", "production_root_or_journal_touched",
            "result_accessed", "signals_sent", "external_processes_inspected",
            "controller_or_resume_executed", "publication_method",
            "canonical_visibility_rule",
        }
        exact_object(value, expected, "terminal")
        if schema == PASS_SCHEMA and value["status"] != PASS_STATUS:
            raise PreflightError("PASS status mismatch")
        if schema == FAIL_SCHEMA and value["status"] != FAIL_STATUS:
            raise PreflightError("FAIL status mismatch")
        exact_sha(value["begin_sha256"], "terminal.begin_sha256")
        if schema == PASS_SCHEMA:
            exact_sha(value["intent_sha256"], "PASS.intent_sha256")
            exact_sha(
                value["compatibility_result_sha256"],
                "PASS.compatibility_result_sha256",
            )
            if value["error_type"] != "" or value["error_message"] != "":
                raise PreflightError("PASS terminal contains an error")
        else:
            if value["intent_sha256"] != "ABSENT_BEFORE_INTENT":
                exact_sha(value["intent_sha256"], "FAIL.intent_sha256")
            if value["compatibility_result_sha256"] != "ABSENT_NO_COMPATIBILITY_RESULT":
                exact_sha(
                    value["compatibility_result_sha256"],
                    "FAIL.compatibility_result_sha256",
                )
            exact_string(value["error_type"], "FAIL.error_type")
            exact_string(value["error_message"], "FAIL.error_message")
        for key in (
            "production_build_executed", "production_root_or_journal_touched",
            "result_accessed", "signals_sent", "external_processes_inspected",
            "controller_or_resume_executed",
        ):
            exact_bool(value[key], False, f"terminal.{key}")
        if value["publication_method"] != PRODUCTION_EVIDENCE_PUBLICATION_METHOD:
            raise PreflightError("terminal publication method mismatch")
        if value["canonical_visibility_rule"] != EVIDENCE_VISIBILITY_RULE:
            raise PreflightError("terminal visibility rule mismatch")
    else:
        raise PreflightError("unknown evidence record schema")
    observed_scope = (
        value["compatibility_scope"] if schema == BEGIN_SCHEMA else value["scope"]
    )
    if value["decision_id"] != EXPECTED_DECISION_ID or observed_scope != API_SCOPE:
        raise PreflightError("evidence record decision/scope mismatch")
    exact_sha(value["authorization_sha256"], "record.authorization_sha256")


def classify_recovery(
    begin: Mapping[str, Any] | None,
    intent: Mapping[str, Any] | None,
    terminal: Mapping[str, Any] | None,
) -> str:
    if begin is None:
        if intent is not None or terminal is not None:
            raise PreflightError("intent/terminal without durable BEGIN")
        return "NEW_WRITE_BEGIN"
    validate_record(begin)
    begin_sha = sha256_bytes(canonical_json_bytes(dict(begin)))
    if intent is None:
        if terminal is None:
            return "RECOVER_BEGIN_ONLY_PUBLISH_FAIL_TERMINAL"
        validate_record(terminal)
        if (
            terminal["schema"] != FAIL_SCHEMA
            or terminal["begin_sha256"] != begin_sha
            or terminal["authorization_sha256"] != begin["authorization_sha256"]
        ):
            raise PreflightError("BEGIN-only terminal is not exact FAIL")
        return "ALREADY_TERMINAL_FAIL"
    validate_record(intent)
    if (
        intent["begin_sha256"] != begin_sha
        or intent["authorization_sha256"] != begin["authorization_sha256"]
    ):
        raise PreflightError("INTENT does not bind BEGIN")
    intent_sha = sha256_bytes(canonical_json_bytes(dict(intent)))
    if terminal is None:
        return "RECOVER_INTENT_REVALIDATE_COMPATIBILITY_OUTCOME"
    validate_record(terminal)
    if (
        terminal["begin_sha256"] != begin_sha
        or terminal["intent_sha256"] != intent_sha
        or terminal["authorization_sha256"] != begin["authorization_sha256"]
    ):
        raise PreflightError("terminal does not bind BEGIN/INTENT")
    return "ALREADY_TERMINAL_PASS" if terminal["schema"] == PASS_SCHEMA else "ALREADY_TERMINAL_FAIL"


class MutableDirectoryLease:
    """Create-or-open no-clobber directory whose children may change."""

    def __init__(
        self, path: Path, fd: int, parent_fd: int,
        device: int, inode: int, mode: int,
    ) -> None:
        self.path = path
        self.fd = fd
        self.parent_fd = parent_fd
        self.device = device
        self.inode = inode
        self.mode = mode

    @classmethod
    def create_or_open_at(
        cls, parent_fd: int, parent_path: Path, name: str, label: str,
        *, mode: int = 0o700,
    ) -> "MutableDirectoryLease":
        if not name or "/" in name or name in {".", ".."}:
            raise PreflightError(f"{label}: directory basename required")
        owned_parent = os.dup(parent_fd)
        try:
            try:
                os.mkdir(name, mode, dir_fd=owned_parent)
            except FileExistsError:
                pass
            os.fsync(owned_parent)
            fd = os.open(
                name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
                dir_fd=owned_parent,
            )
            try:
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
                    raise PreflightError(f"{label}: directory mode/type mismatch")
                lease = cls(
                    parent_path / name, fd, owned_parent,
                    info.st_dev, info.st_ino, mode,
                )
                lease.revalidate(label + ".initial")
                return lease
            except BaseException:
                os.close(fd)
                raise
        except BaseException:
            os.close(owned_parent)
            raise

    def revalidate(self, label: str) -> None:
        held = os.fstat(self.fd)
        if (
            not stat.S_ISDIR(held.st_mode)
            or (held.st_dev, held.st_ino) != (self.device, self.inode)
            or stat.S_IMODE(held.st_mode) != self.mode
        ):
            raise PreflightError(f"{label}: held mutable directory drift")
        probe = os.open(
            self.path.name,
            os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            dir_fd=self.parent_fd,
        )
        try:
            named = os.fstat(probe)
            if (
                (named.st_dev, named.st_ino) != (self.device, self.inode)
                or stat.S_IMODE(named.st_mode) != self.mode
            ):
                raise PreflightError(f"{label}: mutable directory path replaced")
        finally:
            os.close(probe)
        absolute_probe = open_absolute_directory_nofollow(
            self.path, f"{label}.absolute_path"
        )
        try:
            absolute_named = os.fstat(absolute_probe)
            if (
                (absolute_named.st_dev, absolute_named.st_ino)
                != (self.device, self.inode)
                or stat.S_IMODE(absolute_named.st_mode) != self.mode
            ):
                raise PreflightError(
                    f"{label}: mutable directory absolute path replaced"
                )
        finally:
            os.close(absolute_probe)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


def acquire_exclusive_evidence_lock(directory_fd: int, label: str) -> None:
    """Serialize one decision transaction without creating a lock pathname."""

    info = os.fstat(directory_fd)
    if not stat.S_ISDIR(info.st_mode):
        raise PreflightError(f"{label}: held evidence directory required")
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise PreflightBlocked(
            f"{label}: another preflight transaction holds the decision lock"
        ) from exc
    except OSError as exc:
        raise PreflightError(f"{label}: exclusive directory lock failed") from exc


def read_evidence_record(
    directory_fd: int, name: str, label: str,
) -> dict[str, Any] | None:
    if not name or "/" in name or name in {".", ".."}:
        raise PreflightError(f"{label}: canonical evidence basename required")
    os.fsync(directory_fd)
    try:
        fd = os.open(name, os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    try:
        data, _identity = read_stable_fd(fd, label)
        return strict_json_bytes(data, label)
    finally:
        os.close(fd)


def load_held_v10_builder(
    lease_set: EvidenceLeaseSet, bindings: FrozenBindings,
) -> types.SimpleNamespace:
    expected_path = Path(bindings.v10_package["builder_path"])
    matches = [lease for lease in lease_set.files if lease.path == expected_path]
    if len(matches) != 1:
        raise PreflightError("exactly one held v10 builder lease required")
    source = matches[0].stable_bytes("v10_builder.precompile")
    namespace: dict[str, Any] = {
        "__name__": "trusted_held_transport_runtime_v10",
        "__file__": "<held-v10-builder>",
        "__package__": None,
    }
    code = compile(source, "<held-v10-builder>", "exec", dont_inherit=True)
    exec(code, namespace, namespace)
    matches[0].revalidate("v10_builder.postcompile")
    return types.SimpleNamespace(**namespace)


def _terminal_pair(
    evidence_fd: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    passed = read_evidence_record(evidence_fd, PASS_NAME, "PASS terminal")
    failed = read_evidence_record(evidence_fd, FAIL_NAME, "FAIL terminal")
    if passed is not None and failed is not None:
        raise PreflightError("both PASS and FAIL terminal records exist")
    return passed, failed


def compatibility_staging_name(auth_sha: str) -> str:
    exact_sha(auth_sha, "authorization_sha256")
    return ".v10-native-compatibility-staging-" + auth_sha[:24]


def allowed_work_root_members(auth_sha: str) -> set[str]:
    return {
        EXPECTED_EVIDENCE_JOURNAL.name,
        EXPECTED_COMPATIBILITY_ROOT.name,
        EXPECTED_COMPATIBILITY_JOURNAL.name,
        compatibility_staging_name(auth_sha),
    }


def validate_transaction_directory_relationships(
    work_root: MutableDirectoryLease,
    evidence: MutableDirectoryLease,
) -> None:
    if evidence.path.parent != work_root.path:
        raise PreflightError("evidence journal is not a direct work-root child")
    work_info = os.fstat(work_root.fd)
    evidence_parent_info = os.fstat(evidence.parent_fd)
    if (
        (work_info.st_dev, work_info.st_ino)
        != (evidence_parent_info.st_dev, evidence_parent_info.st_ino)
    ):
        raise PreflightError("evidence parent FD differs from held work-root FD")


def revalidate_transaction_trust_through_terminal(
    *,
    immutable_leases: EvidenceLeaseSet,
    work_root: MutableDirectoryLease,
    evidence: MutableDirectoryLease,
    production_parent_fd: int,
    phase: str,
) -> None:
    """Final named-path-to-held-inode trust check after terminal durability."""

    immutable_leases.revalidate(f"{phase}.immutable")
    work_root.revalidate(f"{phase}.work_root")
    evidence.revalidate(f"{phase}.evidence")
    validate_transaction_directory_relationships(work_root, evidence)
    assert_production_absent(production_parent_fd)


def execute_preflight_transaction(
    *,
    builder: Any,
    auth_sha: str,
    production_parent_fd: int,
    work_root: MutableDirectoryLease,
    evidence: MutableDirectoryLease,
    immutable_leases: EvidenceLeaseSet,
    publisher: Callable[..., Mapping[str, Any]] = publish_otmpfile_noreplace,
) -> str:
    """Run or recover the result-free compatibility transaction.

    The caller owns every FD.  This function borrows them, publishes only the
    five fixed evidence names, and never closes or transfers caller-owned FDs.
    """

    request = make_compatibility_request(auth_sha)
    allowed_evidence = {BEGIN_NAME, INTENT_NAME, RESULT_NAME, PASS_NAME, FAIL_NAME}
    observed_evidence = _fresh_directory_members(evidence.fd, "evidence")
    if not observed_evidence.issubset(allowed_evidence):
        raise PreflightError("evidence journal contains an undeclared member")
    immutable_leases.revalidate("transaction.entry")
    work_root.revalidate("transaction.work_root.entry")
    evidence.revalidate("transaction.evidence.entry")
    validate_transaction_directory_relationships(work_root, evidence)
    observed_work = _fresh_directory_members(work_root.fd, "work_root")
    if (
        EXPECTED_EVIDENCE_JOURNAL.name not in observed_work
        or not observed_work.issubset(allowed_work_root_members(auth_sha))
    ):
        raise PreflightError("work root contains an undeclared member")
    assert_production_absent(production_parent_fd)
    begin = read_evidence_record(evidence.fd, BEGIN_NAME, "BEGIN")
    intent = read_evidence_record(evidence.fd, INTENT_NAME, "INTENT")
    result_record = read_evidence_record(evidence.fd, RESULT_NAME, "RESULT")
    passed_terminal, failed_terminal = _terminal_pair(evidence.fd)
    terminal = passed_terminal if passed_terminal is not None else failed_terminal
    state = classify_recovery(begin, intent, terminal)
    if state in {"ALREADY_TERMINAL_PASS", "ALREADY_TERMINAL_FAIL"}:
        if state == "ALREADY_TERMINAL_PASS":
            if intent is None or result_record is None or terminal is None:
                raise PreflightError("existing PASS requires INTENT and RESULT")
            validate_result_record(result_record, request)
            actual_result_sha = sha256_bytes(
                canonical_json_bytes(dict(result_record))
            )
            if terminal["compatibility_result_sha256"] != actual_result_sha:
                raise PreflightError("existing PASS does not bind RESULT bytes")
        elif result_record is None:
            if (
                terminal is None
                or terminal["compatibility_result_sha256"]
                != "ABSENT_NO_COMPATIBILITY_RESULT"
            ):
                raise PreflightError("existing FAIL claims an absent RESULT")
        else:
            if intent is None or terminal is None:
                raise PreflightError("existing FAIL RESULT requires INTENT")
            validate_result_record(result_record, request)
            actual_result_sha = sha256_bytes(
                canonical_json_bytes(dict(result_record))
            )
            if terminal["compatibility_result_sha256"] != actual_result_sha:
                raise PreflightError("existing FAIL does not bind RESULT bytes")
        immutable_leases.revalidate("transaction.existing_terminal")
        work_root.revalidate("transaction.work_root.existing_terminal")
        evidence.revalidate("transaction.evidence.existing_terminal")
        assert_production_absent(production_parent_fd)
        terminal_name = PASS_NAME if state == "ALREADY_TERMINAL_PASS" else FAIL_NAME
        expected = {BEGIN_NAME, terminal_name}
        if intent is not None:
            expected.add(INTENT_NAME)
        if result_record is not None:
            expected.add(RESULT_NAME)
        if _fresh_directory_members(evidence.fd, "evidence.terminal") != expected:
            raise PreflightError("terminal evidence member set mismatch")
        terminal_work = _fresh_directory_members(work_root.fd, "work_root.terminal")
        if state == "ALREADY_TERMINAL_PASS":
            expected_work = {
                EXPECTED_EVIDENCE_JOURNAL.name,
                EXPECTED_COMPATIBILITY_ROOT.name,
                EXPECTED_COMPATIBILITY_JOURNAL.name,
            }
            if terminal_work != expected_work:
                raise PreflightError("PASS work-root member set mismatch")
        elif (
            EXPECTED_EVIDENCE_JOURNAL.name not in terminal_work
            or not terminal_work.issubset(allowed_work_root_members(auth_sha))
        ):
            raise PreflightError("FAIL work-root member set mismatch")
        revalidate_transaction_trust_through_terminal(
            immutable_leases=immutable_leases,
            work_root=work_root,
            evidence=evidence,
            production_parent_fd=production_parent_fd,
            phase="transaction.existing_terminal.final",
        )
        return state

    if begin is None:
        begin = make_begin(auth_sha)
        try:
            publisher(evidence.fd, BEGIN_NAME, canonical_json_bytes(begin))
        except BaseException as exc:
            durable_begin = read_evidence_record(evidence.fd, BEGIN_NAME, "BEGIN")
            if durable_begin is not None:
                validate_record(durable_begin)
                durable_begin_sha = sha256_bytes(
                    canonical_json_bytes(dict(durable_begin))
                )
                failed = make_terminal(
                    passed=False,
                    auth_sha=auth_sha,
                    begin_sha=durable_begin_sha,
                    intent_sha=None,
                    result_sha=None,
                    phase="exception-after-durable-begin-publication",
                    error_type=type(exc).__name__,
                    error_message=(
                        "exception occurred after durable BEGIN publication"
                    ),
                )
                publisher(evidence.fd, FAIL_NAME, canonical_json_bytes(failed))
                os.fsync(evidence.fd)
                if _fresh_directory_members(
                    evidence.fd, "evidence.begin_publish_exception"
                ) != {BEGIN_NAME, FAIL_NAME}:
                    raise PreflightError(
                        "BEGIN publication exception closure mismatch"
                    )
                revalidate_transaction_trust_through_terminal(
                    immutable_leases=immutable_leases,
                    work_root=work_root,
                    evidence=evidence,
                    production_parent_fd=production_parent_fd,
                    phase="transaction.begin_publish_exception.final",
                )
            raise
    validate_record(begin)
    begin_sha = sha256_bytes(canonical_json_bytes(dict(begin)))

    if state == "RECOVER_BEGIN_ONLY_PUBLISH_FAIL_TERMINAL":
        failed = make_terminal(
            passed=False, auth_sha=auth_sha, begin_sha=begin_sha,
            intent_sha=None, result_sha=None, phase="recovery-after-begin-only",
            error_type="InterruptedBeforeDurableIntent",
            error_message="durable BEGIN exists without durable INTENT",
        )
        publisher(evidence.fd, FAIL_NAME, canonical_json_bytes(failed))
        os.fsync(evidence.fd)
        if _fresh_directory_members(evidence.fd, "evidence.begin_only_fail") != {
            BEGIN_NAME, FAIL_NAME,
        }:
            raise PreflightError("BEGIN-only recovery evidence closure mismatch")
        revalidate_transaction_trust_through_terminal(
            immutable_leases=immutable_leases,
            work_root=work_root,
            evidence=evidence,
            production_parent_fd=production_parent_fd,
            phase="transaction.begin_only_fail.final",
        )
        return "RECOVERED_BEGIN_ONLY_TO_TERMINAL_FAIL"

    try:
        if intent is None:
            intent = make_intent(begin, request)
            publisher(evidence.fd, INTENT_NAME, canonical_json_bytes(intent))
        validate_record(intent)
        if intent["begin_sha256"] != begin_sha:
            raise PreflightError("durable INTENT does not bind durable BEGIN")
        intent_sha = sha256_bytes(canonical_json_bytes(dict(intent)))
        if result_record is None:
            raw_result = invoke_v10_scoped_native_compatibility(
                builder, request,
                parent_fd=production_parent_fd,
                work_root_fd=work_root.fd,
            )
            result_record = make_result_record(raw_result, request)
            publisher(evidence.fd, RESULT_NAME, canonical_json_bytes(result_record))
        validate_result_record(result_record, request)
        result_sha = sha256_bytes(canonical_json_bytes(dict(result_record)))
        immutable_leases.revalidate("transaction.before_pass")
        work_root.revalidate("transaction.work_root.before_pass")
        evidence.revalidate("transaction.evidence.before_pass")
        assert_production_absent(production_parent_fd)
        if _fresh_directory_members(work_root.fd, "work_root.before_pass") != {
            EXPECTED_EVIDENCE_JOURNAL.name,
            EXPECTED_COMPATIBILITY_ROOT.name,
            EXPECTED_COMPATIBILITY_JOURNAL.name,
        }:
            raise PreflightError("compatibility API did not publish exact work-root closure")
        passed = make_terminal(
            passed=True, auth_sha=auth_sha, begin_sha=begin_sha,
            intent_sha=intent_sha, result_sha=result_sha,
            phase="native-compatibility-complete",
        )
        publisher(evidence.fd, PASS_NAME, canonical_json_bytes(passed))
        os.fsync(evidence.fd)
        if _fresh_directory_members(evidence.fd, "evidence.pass") != {
            BEGIN_NAME, INTENT_NAME, RESULT_NAME, PASS_NAME,
        }:
            raise PreflightError("PASS evidence closure mismatch")
        revalidate_transaction_trust_through_terminal(
            immutable_leases=immutable_leases,
            work_root=work_root,
            evidence=evidence,
            production_parent_fd=production_parent_fd,
            phase="transaction.pass.final",
        )
        return "TERMINAL_PASS"
    except BaseException as exc:
        passed_now, failed_now = _terminal_pair(evidence.fd)
        if passed_now is not None:
            revalidate_transaction_trust_through_terminal(
                immutable_leases=immutable_leases,
                work_root=work_root,
                evidence=evidence,
                production_parent_fd=production_parent_fd,
                phase="transaction.pass_exception.final",
            )
            raise
        if failed_now is None:
            current_intent = read_evidence_record(evidence.fd, INTENT_NAME, "INTENT")
            current_result = read_evidence_record(evidence.fd, RESULT_NAME, "RESULT")
            current_intent_sha = (
                sha256_bytes(canonical_json_bytes(current_intent))
                if current_intent is not None else None
            )
            current_result_sha = (
                sha256_bytes(canonical_json_bytes(current_result))
                if current_result is not None else None
            )
            failed = make_terminal(
                passed=False, auth_sha=auth_sha, begin_sha=begin_sha,
                intent_sha=current_intent_sha, result_sha=current_result_sha,
                phase="native-compatibility-failed",
                error_type=type(exc).__name__,
                error_message=str(exc) or "native compatibility failure",
            )
            publisher(evidence.fd, FAIL_NAME, canonical_json_bytes(failed))
            os.fsync(evidence.fd)
        revalidate_transaction_trust_through_terminal(
            immutable_leases=immutable_leases,
            work_root=work_root,
            evidence=evidence,
            production_parent_fd=production_parent_fd,
            phase="transaction.fail_exception.final",
        )
        raise


def synthetic_blocked_status() -> dict[str, Any]:
    upstream_unbound = not FROZEN_BINDINGS.upstream_v10_is_fully_bound()
    return {
        "schema": PREFLIGHT_SCHEMA,
        "status": PREFLIGHT_STATUS,
        "unbound": {
            "v10_prepared_package": upstream_unbound,
            "v10_independent_qa": upstream_unbound,
            "preflight_prepared_receipt_manifest_index": True,
            "preflight_independent_qa_full_closure": True,
        },
        "authority": {
            "mars_access_authorized": False,
            "preflight_execution_authorized": False,
            "production_build_authorized": False,
            "result_access_authorized": False,
            "external_process_inspection_authorized": False,
            "signals_authorized": False,
            "controller_or_resume_authorized": False,
            "deployment_authorized": False,
        },
        "next_legal_action": (
            "FREEZE_THIS_PREFLIGHT_V3_THEN_RUN_FRESH_INDEPENDENT_QA_"
            "BEFORE_ANY_SEPARATE_ROOT_AUTHORIZATION"
        ),
    }


def validate_actual_held_bootstrap_fds(
    context: Mapping[str, Any], authorization_bytes: bytes,
) -> None:
    specs = (
        (HELD_INTERPRETER_FD, "interpreter", "interpreter_identity",
         "interpreter_sha256", None),
        (HELD_PREFLIGHT_SOURCE_FD, "preflight source", "source_identity",
         "source_sha256", 0o444),
        (HELD_AUTHORIZATION_FD, "authorization", "authorization_identity",
         "authorization_sha256", 0o444),
    )
    for fd, label, identity_key, sha_key, mode in specs:
        validate_inherited_held_fd(fd, label)
        data, identity = read_stable_fd(
            fd, label, limit=AUTH_READ_LIMIT if fd == HELD_AUTHORIZATION_FD else HELD_READ_LIMIT,
            mode=mode,
        )
        if identity.json() != context[identity_key]:
            raise PreflightError(f"{label}: held identity differs from bootstrap")
        if sha256_bytes(data) != context[sha_key]:
            raise PreflightError(f"{label}: held SHA differs from bootstrap")
        if fd == HELD_AUTHORIZATION_FD and data != authorization_bytes:
            raise PreflightError("authorization bytes differ from held FD199")


def validate_logical_bootstrap_argv(
    logical_argv: Sequence[str], context: Mapping[str, Any], auth_sha: str,
) -> None:
    expected = [
        "--trusted-authorization-sha256", auth_sha,
        "--trusted-preflight-source-sha256",
        context["source_sha256"],
        "--trusted-interpreter-sha256", context["interpreter_sha256"],
    ]
    if list(logical_argv) != expected:
        raise PreflightError("logical bootstrap argv is not exact")


def _one_immutable_directory(
    lease_set: EvidenceLeaseSet, path: Path, label: str,
) -> DirectoryLease:
    matches = [lease for lease in lease_set.directories if lease.path == path]
    if len(matches) != 1:
        raise PreflightError(f"{label}: exactly one immutable directory lease required")
    return matches[0]


def held_preflight_main(
    bootstrap_context: Mapping[str, Any],
    authorization_bytes: bytes,
    logical_argv: Sequence[str],
) -> int:
    """Trusted held-byte entry; executable only after all future bindings exist."""

    auth_sha = sha256_bytes(authorization_bytes)
    auth = strict_json_bytes(authorization_bytes, "root authorization")
    validate_authorization_payload(auth, auth_sha)
    bindings = effective_bindings_from_signed_authorization(auth, FROZEN_BINDINGS)
    validate_production_v10_binding_roots(bindings)
    validate_bootstrap_context(bootstrap_context, auth_sha)
    validate_logical_bootstrap_argv(logical_argv, bootstrap_context, auth_sha)
    validate_actual_held_bootstrap_fds(bootstrap_context, authorization_bytes)

    immutable = open_full_evidence_lease(
        bindings,
        preflight_source_sha256=bootstrap_context["source_sha256"],
        interpreter_sha256=bootstrap_context["interpreter_sha256"],
    )
    grandparent_fd = -1
    work_parent: MutableDirectoryLease | None = None
    work_root: MutableDirectoryLease | None = None
    evidence: MutableDirectoryLease | None = None
    try:
        production = _one_immutable_directory(
            immutable, EXPECTED_PRODUCTION_PARENT, "production_parent"
        )
        assert_production_absent(production.fd)
        grandparent_fd = open_absolute_directory_nofollow(
            EXPECTED_PREFLIGHT_WORK_PARENT.parent, "preflight_work_grandparent"
        )
        require_linux_xfs_directory_fd(
            grandparent_fd, "preflight_work_grandparent"
        )
        work_parent = MutableDirectoryLease.create_or_open_at(
            grandparent_fd, EXPECTED_PREFLIGHT_WORK_PARENT.parent,
            EXPECTED_PREFLIGHT_WORK_PARENT.name, "preflight_work_parent",
        )
        require_linux_xfs_directory_fd(work_parent.fd, "preflight_work_parent")
        work_root = MutableDirectoryLease.create_or_open_at(
            work_parent.fd, EXPECTED_PREFLIGHT_WORK_PARENT,
            EXPECTED_PREFLIGHT_WORK_ROOT.name, "preflight_work_root",
        )
        require_linux_xfs_directory_fd(work_root.fd, "preflight_work_root")
        evidence = MutableDirectoryLease.create_or_open_at(
            work_root.fd, EXPECTED_PREFLIGHT_WORK_ROOT,
            EXPECTED_EVIDENCE_JOURNAL.name, "evidence_journal",
        )
        require_linux_xfs_directory_fd(evidence.fd, "evidence_journal")
        acquire_exclusive_evidence_lock(evidence.fd, "evidence_journal")
        evidence.revalidate("evidence_journal.locked")
        if _fresh_directory_members(work_parent.fd, "preflight_work_parent") != {
            EXPECTED_PREFLIGHT_WORK_ROOT.name,
        }:
            raise PreflightError("preflight work parent contains an undeclared decision")
        allowed_work_members = allowed_work_root_members(auth_sha)
        if not _fresh_directory_members(work_root.fd, "preflight_work_root").issubset(
            allowed_work_members
        ):
            raise PreflightError("preflight work root contains an undeclared member")
        immutable.revalidate("held_main.before_builder_compile")
        builder = load_held_v10_builder(immutable, bindings)
        try:
            status = execute_preflight_transaction(
                builder=builder,
                auth_sha=auth_sha,
                production_parent_fd=production.fd,
                work_root=work_root,
                evidence=evidence,
                immutable_leases=immutable,
            )
        finally:
            immutable.revalidate("held_main.final.immutable")
            work_parent.revalidate("held_main.final.work_parent")
            work_root.revalidate("held_main.final.work_root")
            evidence.revalidate("held_main.final.evidence")
            validate_transaction_directory_relationships(work_root, evidence)
            assert_production_absent(production.fd)
            validate_actual_held_bootstrap_fds(
                bootstrap_context, authorization_bytes
            )
        if status not in {
            "TERMINAL_PASS", "ALREADY_TERMINAL_PASS",
            "ALREADY_TERMINAL_FAIL", "RECOVERED_BEGIN_ONLY_TO_TERMINAL_FAIL",
        }:
            raise PreflightError("unexpected transaction terminal status")
        return 0 if status in {"TERMINAL_PASS", "ALREADY_TERMINAL_PASS"} else 2
    finally:
        if evidence is not None:
            evidence.close()
        if work_root is not None:
            work_root.close()
        if work_parent is not None:
            work_parent.close()
        if grandparent_fd >= 0:
            os.close(grandparent_fd)
        immutable.close()


def main() -> int:
    raise PreflightBlocked(
        "direct pathname execution is forbidden; prepared-only preflight v3 "
        "requires fresh independent QA and a separate exact held FD197/198/199 "
        "root authorization before execution"
    )


ROOT_BOOTSTRAP_TEXT = r'''import fcntl,hashlib,json,os,stat,sys
IFD,SFD,AFD=197,198,199
PROTO="HELD_FD197_198_199_PRECOMPILE_ROOT_BOOTSTRAP_V3"
AUTH_SCHEMA="historical_200k_fixed10k_root_held_preflight_launch_authorization_v3"
AUTH_STATUS="AUTHORIZED_TRUSTED_HELD_PREFLIGHT_PACKAGE_AND_QA_ONLY"
DECISION="historical-200k-fixed10k-post-stage06-runtime-v10"
PREPARED_SCHEMA="historical_200k_fixed10k_result_free_mars_native_preflight_v3_bundle_manifest_v3"
PREPARED_RECEIPT_SCHEMA="historical_200k_fixed10k_result_free_mars_native_preflight_v3_prepared_receipt_v3"
PREPARED_STATUS="PASS_PREPARED_ONLY_NOT_AUTHORIZED_NOT_EXECUTED_AWAITING_INDEPENDENT_QA"
AUTH_KEYS={"schema","status","created_utc","decision_id","preflight_package_manifest_path","preflight_package_manifest_sha256","preflight_package_index_path","preflight_package_index_sha256","preflight_independent_audit_receipt_path","preflight_independent_audit_receipt_sha256","preflight_independent_audit_index_path","preflight_independent_audit_index_sha256","authority"}
AUTHORITY={"preflight_launch_authorized":True,"transport_runtime_layout_authorized":False,"result_access_authorized":False,"signals_authorized":False,"deployment_or_resume_authorized":False}
PACKAGE_TOP={"AUTHOR_COMPILE_V3_OUTPUT.json","AUTHOR_PREFLIGHT_V3_SYNTHETIC_OUTPUT.json","BUNDLE_MANIFEST.json","PREPARED_RESULT_FREE_RECEIPT.json","RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json","RESULT_FREE_MARS_NATIVE_PREFLIGHT_V3_CN.md","SHA256SUMS","UPSTREAM_EVIDENCE_BINDINGS_V3.json","run_result_free_mars_native_preflight_v3.py","test_result_free_mars_native_preflight_v3_synthetic.py"}
PACKAGE_INDEX=PACKAGE_TOP-{"SHA256SUMS"}
PAYLOAD=PACKAGE_TOP-{"BUNDLE_MANIFEST.json","PREPARED_RESULT_FREE_RECEIPT.json","SHA256SUMS"}
QA_AUTHORITY_KEYS={"mars_access_authorized","mars_write_authorized","preflight_execution_authorized","transport_build_or_smoke_authorized","production_root_or_journal_write_authorized","result_access_authorized","external_process_inspection_or_control_authorized","signals_authorized","controller_or_resume_authorized","deployment_authorized"}
def fail(msg):
 sys.stderr.write("FAIL_CLOSED_PREFLIGHT_V3_BOOTSTRAP: "+msg+"\n");raise SystemExit(2)
def ident(st):return {"device":st.st_dev,"inode":st.st_ino,"size_bytes":st.st_size,"mtime_ns":st.st_mtime_ns,"ctime_ns":st.st_ctime_ns,"mode":format(stat.S_IMODE(st.st_mode),"04o"),"nlink":st.st_nlink}
def canonical(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8")
def pread(fd,size,limit):
 if size<0 or size>limit:fail("size outside limit")
 out=[];off=0
 while off<size:
  b=os.pread(fd,min(1048576,size-off),off)
  if not b:fail("short pread")
  out.append(b);off+=len(b)
 if os.pread(fd,1,size)!=b"":fail("file grew")
 return b"".join(out)
def read(fd,label,limit,held):
 if fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY:fail(label+" not O_RDONLY")
 if held and fcntl.fcntl(fd,fcntl.F_GETFD)&fcntl.FD_CLOEXEC:fail(label+" CLOEXEC")
 a=os.fstat(fd)
 if not stat.S_ISREG(a.st_mode) or a.st_nlink!=1:fail(label+" identity")
 data=pread(fd,a.st_size,limit);b=os.fstat(fd)
 if ident(a)!=ident(b):fail(label+" changed")
 return data,ident(a),hashlib.sha256(data).hexdigest()
def openabsdir(path,label):
 if type(path)!=str or not path.startswith("/") or os.path.normpath(path)!=path:fail(label+" path")
 parts=path.split("/")[1:]
 if not parts or any(x in ("",".","..") for x in parts):fail(label+" components")
 fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  for part in parts:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd);os.close(fd);fd=nxt
  return fd
 except BaseException:
  os.close(fd);raise
def openmember(directory_fd,name,label):
 if type(name)!=str or not name or "/" in name or name in (".",".."):fail(label+" basename")
 return os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory_fd)
def parseindex(data):
 try:text=data.decode("ascii","strict")
 except UnicodeDecodeError:fail("package index ASCII")
 if not data.endswith(b"\n"):fail("package index terminal newline")
 lines=text.splitlines();result={};order=[]
 for line in lines:
  if len(line)<67 or line[64:66]!="  ":fail("package index line")
  digest,name=line[:64],line[66:]
  if len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest) or not name or "/" in name or name in result:fail("package index entry")
  result[name]=digest;order.append(name)
 if set(result)!=PACKAGE_INDEX or order!=sorted(order):fail("package index exact closure")
 return result
def jsonobject(data,label):
 try:value=json.loads(data.decode("utf-8","strict"))
 except Exception:fail(label+" JSON")
 if type(value)!=dict or canonical(value)!=data:fail(label+" canonical JSON")
 return value
def allfalse(value,keys,label):
 if type(value)!=dict or set(value)!=keys or any(type(v)is not bool or v for v in value.values()):fail(label+" authority")
argv=sys.argv[1:]
if len(argv)!=6 or argv[0]!="--trusted-authorization-sha256" or argv[2]!="--trusted-preflight-source-sha256" or argv[4]!="--trusted-interpreter-sha256":fail("bootstrap trusted SHA envelope")
expected_ash,expected_ssh,expected_ish=argv[1],argv[3],argv[5]
if any(len(x)!=64 or any(c not in "0123456789abcdef" for c in x) for x in (expected_ash,expected_ssh,expected_ish)):fail("bootstrap trusted SHA syntax")
pfd=os.open("/proc/self/cmdline",os.O_RDONLY|os.O_CLOEXEC);raw=b""
while True:
 b=os.read(pfd,65536)
 if not b:break
 raw+=b
os.close(pfd)
if not raw.endswith(b"\0"):fail("proc cmdline terminal NUL")
try:proc=[x.decode("utf-8","strict") for x in raw[:-1].split(b"\0")]
except UnicodeDecodeError:fail("proc cmdline UTF-8")
if proc[:5]!=["/proc/self/fd/197","-I","-B","-S","-c"] or proc[6:]!=argv:fail("exact proc argv")
boot_sha=hashlib.sha256(proc[5].encode("utf-8")).hexdigest()
ib,ii,ish=read(IFD,"interpreter",268435456,True)
sb,si,ssh=read(SFD,"preflight source",268435456,True)
ab,ai,ash=read(AFD,"authorization",16777216,True)
if ii["mode"] not in ("0555","0755") or si["mode"]!="0444" or ai["mode"]!="0444":fail("held mode contract")
if (ash,ssh,ish)!=(expected_ash,expected_ssh,expected_ish):fail("trusted SHA envelope mismatch")
efd=os.open("/proc/self/exe",os.O_RDONLY|os.O_CLOEXEC);ei=ident(os.fstat(efd));eb=pread(efd,ei["size_bytes"],268435456);os.close(efd)
if ei["device"]!=ii["device"] or ei["inode"]!=ii["inode"] or hashlib.sha256(eb).hexdigest()!=ish:fail("interpreter/proc-self-exe mismatch")
auth=jsonobject(ab,"authorization")
if set(auth)!=AUTH_KEYS or auth.get("schema")!=AUTH_SCHEMA or auth.get("status")!=AUTH_STATUS or auth.get("decision_id")!=DECISION or auth.get("authority")!=AUTHORITY:fail("authorization exact contract")
for key in AUTH_KEYS:
 if key.endswith("_sha256"):
  value=auth[key]
  if type(value)!=str or len(value)!=64 or any(c not in "0123456789abcdef" for c in value):fail("authorization SHA")
manifest_path=auth["preflight_package_manifest_path"];index_path=auth["preflight_package_index_path"]
if type(manifest_path)!=str or type(index_path)!=str or os.path.basename(manifest_path)!="BUNDLE_MANIFEST.json" or os.path.basename(index_path)!="SHA256SUMS" or os.path.dirname(manifest_path)!=os.path.dirname(index_path):fail("authorization package anchors")
pkgfd=openabsdir(os.path.dirname(index_path),"package root")
if stat.S_IMODE(os.fstat(pkgfd).st_mode)!=0o555 or set(os.listdir(pkgfd))!=PACKAGE_TOP:fail("package exact top-level closure")
opened={}
for name in sorted(PACKAGE_INDEX):
 fd=openmember(pkgfd,name,"package member");data,identity,digest=read(fd,"package member "+name,268435456,False);opened[name]=(fd,data,identity,digest)
idxfd=openmember(pkgfd,"SHA256SUMS","package index");index_data,index_identity,index_sha=read(idxfd,"package index",16777216,False)
index=parseindex(index_data)
if index_sha!=auth["preflight_package_index_sha256"]:fail("authorization/index SHA mismatch")
for name,(fd,data,identity,digest) in opened.items():
 if index[name]!=digest:fail("package indexed member SHA mismatch")
manifest=jsonobject(opened["BUNDLE_MANIFEST.json"][1],"package manifest")
if opened["BUNDLE_MANIFEST.json"][3]!=auth["preflight_package_manifest_sha256"] or index["BUNDLE_MANIFEST.json"]!=auth["preflight_package_manifest_sha256"]:fail("authorization/manifest SHA mismatch")
manifest_keys={"schema","status","created_utc","payload_file_count","files","closure_files_not_in_payload_manifest","authority"}
if set(manifest)!=manifest_keys or manifest.get("schema")!=PREPARED_SCHEMA or manifest.get("status")!=PREPARED_STATUS or manifest.get("payload_file_count")!=7 or manifest.get("closure_files_not_in_payload_manifest")!=["BUNDLE_MANIFEST.json","PREPARED_RESULT_FREE_RECEIPT.json","SHA256SUMS"]:fail("package manifest exact contract")
allfalse(manifest.get("authority"),QA_AUTHORITY_KEYS,"package manifest")
records=manifest.get("files")
if type(records)!=list or len(records)!=7:fail("package manifest records")
recorded={}
for record in records:
 if type(record)!=dict or set(record)!={"relative_path","role","sha256","size_bytes"}:fail("package manifest record shape")
 name=record["relative_path"]
 if name in recorded or name not in PAYLOAD or type(record["role"])!=str or not record["role"]:fail("package manifest record")
 if record["sha256"]!=opened[name][3] or record["size_bytes"]!=len(opened[name][1]):fail("package manifest member binding")
 recorded[name]=record
if set(recorded)!=PAYLOAD:fail("package manifest payload closure")
contract=jsonobject(opened["RESULT_FREE_MARS_NATIVE_PREFLIGHT_CONTRACT_V3.json"][1],"preflight contract")
if contract.get("root_bootstrap_sha256")!=boot_sha:fail("signed contract/bootstrap SHA mismatch")
receipt=jsonobject(opened["PREPARED_RESULT_FREE_RECEIPT.json"][1],"prepared receipt")
receipt_keys={"schema","status","created_utc","package_directory","package_closure","locked_tools","author_validation","scope","authority","next_legal_action"}
if set(receipt)!=receipt_keys or receipt.get("schema")!=PREPARED_RECEIPT_SCHEMA or receipt.get("status")!=PREPARED_STATUS or receipt.get("package_directory")!=os.path.basename(os.path.dirname(index_path)):fail("prepared receipt exact contract")
allfalse(receipt.get("authority"),QA_AUTHORITY_KEYS,"prepared receipt")
closure=receipt.get("package_closure")
if closure!={"bundle_manifest_sha256":opened["BUNDLE_MANIFEST.json"][3],"payload_file_count":7,"sha_index_listed_count_expected":9,"top_level_file_count_expected":10}:fail("prepared receipt closure binding")
locked=receipt.get("locked_tools")
if type(locked)!=dict or set(locked)!={"preflight","synthetic_test"}:fail("prepared receipt locked tools")
for stem,name in (("preflight","run_result_free_mars_native_preflight_v3.py"),("synthetic_test","test_result_free_mars_native_preflight_v3_synthetic.py")):
 record=locked[stem]
 if record!={"path":name,"sha256":opened[name][3],"line_count":len(opened[name][1].splitlines())}:fail("prepared receipt locked tool binding")
named_source=opened["run_result_free_mars_native_preflight_v3.py"]
if named_source[3]!=ssh or (named_source[2]["device"],named_source[2]["inode"])!=(si["device"],si["inode"]):fail("FD198 is not the authorized package source inode")
context={"protocol":PROTO,"bootstrap_sha256":boot_sha,"proc_argv":proc,"interpreter_fd":197,"source_fd":198,"authorization_fd":199,"interpreter_identity":ii,"source_identity":si,"authorization_identity":ai,"interpreter_sha256":ish,"source_sha256":ssh,"authorization_sha256":ash}
namespace={"__name__":"trusted_held_preflight_v3","__file__":"<held-fd198-preflight-v3>"}
try:
 exec(compile(sb,"<held-fd198-preflight-v3>","exec",dont_inherit=True),namespace,namespace)
 entry=namespace.get("held_preflight_main")
 if not callable(entry):fail("held entry absent")
 result=entry(context,ab,argv)
finally:
 try:
  ib2,ii2,ish2=read(IFD,"interpreter.final",268435456,True)
  sb2,si2,ssh2=read(SFD,"preflight source.final",268435456,True)
  ab2,ai2,ash2=read(AFD,"authorization.final",16777216,True)
  if (ii2,si2,ai2)!=(ii,si,ai) or (ish2,ssh2,ash2)!=(ish,ssh,ash):fail("held FD197/198/199 final identity or SHA drift")
  if ib2!=ib or sb2!=sb or ab2!=ab:fail("held FD197/198/199 final bytes drift")
 finally:
  for fd,data,identity,digest in opened.values():os.close(fd)
  os.close(idxfd);os.close(pkgfd)
raise SystemExit(result)
'''
ROOT_BOOTSTRAP_SHA256 = sha256_bytes(ROOT_BOOTSTRAP_TEXT.encode("utf-8"))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as exc:
        print(f"FAIL_CLOSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
