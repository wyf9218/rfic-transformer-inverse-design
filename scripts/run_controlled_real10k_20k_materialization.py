#!/usr/bin/env python3
"""Two-phase, result-blind production gate for nested real-EMX 10K/20K materialization.

``PREPARE`` freezes an immutable, no-clobber candidate.  It hashes and stats
the already-existing source/code/protocol files but does not parse CSV rows or
model-result JSON.  ``EXECUTE`` accepts exactly one fresh independent GO,
rehashes the entire frozen closure, enforces a Linux current-UID singleton,
and invokes ``build_controlled_real10k_20k_nested.main`` in this process.

This controller never starts training, evaluation, EMX, a subprocess, or a
process signal.  A successful materialization remains a data candidate that
requires the builder's own independent-QA gate before training.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import re
import socket
import stat
import sys
import traceback
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


MANIFEST_SCHEMA = "controlled_real10k_20k_materialization_gate_manifest_v2"
QA_REQUIRED_SCHEMA = (
    "controlled_real10k_20k_materialization_gate_independent_qa_required_v2"
)
PREPARED_RECEIPT_SCHEMA = (
    "controlled_real10k_20k_materialization_gate_prepared_receipt_v2"
)
GO_SCHEMA = "controlled_real10k_20k_materialization_exact_go_v2"
GO_SCOPE = "RESULT_BLIND_NESTED_10K_20K_MATERIALIZATION_ONLY"
INTENT_SCHEMA = "controlled_real10k_20k_materialization_intent_v2"
RUNNING_SCHEMA = "controlled_real10k_20k_materialization_running_v2"
COMPLETE_SCHEMA = "controlled_real10k_20k_materialization_complete_v3"
FAIL_SCHEMA = "controlled_real10k_20k_materialization_fail_no_go_v2"

MANIFEST_NAME = "MANIFEST.json"
QA_REQUIRED_NAME = "INDEPENDENT_QA_REQUIRED.json"
PREPARED_RECEIPT_NAME = "PREPARED_RECEIPT.json"
SHA_INDEX_NAME = "SHA256SUMS.txt"
INTENT_NAME = "INTENT.json"
RUNNING_NAME = "RUNNING.json"
GO_COPY_NAME = "GO_AUTHORITY.json"
COMPLETE_NAME = "COMPLETE.json"
FAIL_NAME = "FAIL_NO_GO.json"

FILE_MODE = 0o444
WORKING_DIRECTORY_MODE = 0o755
FROZEN_DIRECTORY_MODE = 0o555
MAX_GO_LIFETIME = timedelta(hours=24)
STRICT_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
EXPECTED_PRODUCTION_PYTHON_VERSION = "3.12.13"
EXPECTED_PRODUCTION_NUMPY_VERSION = "2.5.0"
MARS_PREFLIGHT_PREPARED_SCHEMA = "controlled_real10k_20k_mars_preflight_prepared_v3"
MARS_PREFLIGHT_QA_SCHEMA = (
    "controlled_real10k_20k_mars_preflight_execution_qa_required_v3"
)
MARS_PREFLIGHT_BODY_SCHEMA = "controlled_real10k_20k_mars_preflight_receipt_body_v3"
MARS_PREFLIGHT_COMMITTED_SCHEMA = "controlled_real10k_20k_mars_preflight_committed_v3"
MARS_PREFLIGHT_LEASE_SCHEMA = "controlled_real10k_20k_mars_preflight_one_use_lease_v3"
PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_body_v3"
)
PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
PACKAGE_COMMIT_SCHEMA = "controlled_real10k_20k_mars_package_commit_v2"
PACKAGE_BUILD_ATTEMPT_BODY_NAME = "PACKAGE_BUILD_ATTEMPT_RECEIPT.json"
PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME = "PACKAGE_BUILD_ATTEMPT_COMMITTED.json"
MARS_PREFLIGHT_PREPARED_NAME = "PREFLIGHT_PREPARED.json"
MARS_PREFLIGHT_QA_NAME = "PREFLIGHT_EXECUTION_QA_REQUIRED.json"
MARS_PREFLIGHT_PREPARE_INDEX_NAME = "PREPARE_SHA256SUMS.txt"
MARS_PREFLIGHT_BODY_NAME = "PREFLIGHT_RECEIPT_BODY.json"
MARS_PREFLIGHT_INDEX_NAME = "PREFLIGHT_SHA256SUMS.txt"
MARS_PREFLIGHT_COMMITTED_NAME = "PREFLIGHT_COMMITTED.json"
MARS_PREFLIGHT_FAILURE_NAME = "PREFLIGHT_FATAL_FAIL.json"
MARS_PREFLIGHT_FAILURE_INDEX_NAME = "FAILURE_SHA256SUMS.txt"
MARS_PREFLIGHT_SUCCESS_FILES = (
    MARS_PREFLIGHT_PREPARED_NAME,
    MARS_PREFLIGHT_QA_NAME,
    MARS_PREFLIGHT_PREPARE_INDEX_NAME,
    MARS_PREFLIGHT_BODY_NAME,
    MARS_PREFLIGHT_INDEX_NAME,
    MARS_PREFLIGHT_COMMITTED_NAME,
)

FROZEN_HISTORICAL_10K_SHA256 = (
    "3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8"
)
FROZEN_AUTHORITATIVE_100K_SHA256 = (
    "68468eb2d3678aa0793157c1c647e975f60e8ec1673c259050ababe9fd1ff08a"
)
FROZEN_HISTORICAL_SUMMARY_SHA256 = (
    "90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa"
)
FROZEN_PREREG_V1_SHA256 = (
    "19aca7778f4974fd3e7eadaca8b291783e8e08e99a53a9dca70b070a4bf16417"
)
FROZEN_PREREG_ADDENDUM_V1_1_SHA256 = (
    "9f1eb0e071ade0e5a42597b4242409282ed8d34cf159104f71df2d4d0d0a8633"
)
FROZEN_PREREG_ADDENDUM_V1_2_SHA256 = (
    "fb7c7d0f9e206e3743cf795a544004e570842f26495903ad0eafdd5f909f37a9"
)

SELECTION_SEED = 20260824
PAIRED_SEEDS = (20260711, 20260712, 20260713)
PHYSICAL_CELL_BINS = 4
PHYSICAL_CELL_ENCODING = "colon_separated_zero_based_bin_indices_v1"
INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)
OUTPUT_COLUMNS = (
    "controlled_source_row_number",
    "controlled_origin",
    "controlled_physical_cell_4d",
    "controlled_split_assignment",
    "canonical_geometry_identity_sha256",
    "portable_geometry_decimal12_sha256",
    "evaluation",
    "touchstone_path",
    "touchstone_sha256",
    *INPUT_COLUMNS,
    *GEOMETRY_COLUMNS,
)
INPUT_LOWER = (0.5, 0.5, 5.0, 0.0)
INPUT_UPPER = (3.0, 3.0, 25.0, 0.8)
GEOMETRY_LOWER = (160.0, 160.0, 160.0, 160.0, 3.0, 20.0, 20.0, -90.0, 100.0, 100.0)
GEOMETRY_UPPER = (520.0, 520.0, 520.0, 520.0, 12.0, 90.0, 90.0, 90.0, 320.0, 320.0)
COUNTS = {
    "historical_source": 10_000,
    "authoritative_source": 100_000,
    "small_gradient_train": 7_871,
    "large_gradient_train": 17_871,
    "validation": 1_227,
    "test": 902,
    "extra": 10_000,
    "large_source": 20_000,
}

BOUND_ROLE_ORDER = (
    "wrapper_code",
    "materialization_builder_code",
    "shared_contract_code",
    "splitter_code",
    "preregistration_v1",
    "preregistration_addendum_v1_1",
    "preregistration_addendum_v1_2",
    "package_build_attempt_body",
    "package_build_attempt_committed",
    "mars_preflight_prepared",
    "mars_preflight_execution_qa_required",
    "mars_preflight_prepare_sha_index",
    "mars_preflight_receipt_body",
    "mars_preflight_sha_index",
    "mars_preflight_committed",
    "mars_preflight_consumed_lease",
    "package_process_singleton_contract",
    "package_singleton_lock",
    "historical_10k_csv",
    "authoritative_100k_csv",
    "historical_model_summary_json",
)
CODE_ROLES = {
    "wrapper_code",
    "materialization_builder_code",
    "shared_contract_code",
    "splitter_code",
}
SOURCE_ROLES = {
    "historical_10k_csv",
    "authoritative_100k_csv",
    "historical_model_summary_json",
}
PROTOCOL_ROLES = {
    "preregistration_v1",
    "preregistration_addendum_v1_1",
    "preregistration_addendum_v1_2",
}
PREFLIGHT_PACKAGE_ROLE_TO_BINDING = {
    "materialization_gate_code": "wrapper_code",
    "materialization_builder_code": "materialization_builder_code",
    "shared_contract_code": "shared_contract_code",
    "splitter_code": "splitter_code",
    "preregistration_v1_json": "preregistration_v1",
    "preregistration_addendum_v1_1_json": "preregistration_addendum_v1_1",
    "preregistration_addendum_v1_2_json": "preregistration_addendum_v1_2",
    "historical_10k_csv": "historical_10k_csv",
    "authoritative_100k_csv": "authoritative_100k_csv",
    "historical_model_summary_json": "historical_model_summary_json",
}

CANDIDATE_AUTHORITIES = {
    "result_blind_data_materialization": False,
    "training": False,
    "evaluation": False,
    "common_test_access": False,
    "numerical_model_result_access": False,
    "fresh_emx": False,
    "emx_generation": False,
    "process_signals": False,
    "subprocess_spawn": False,
}
GO_AUTHORITIES = dict(CANDIDATE_AUTHORITIES)
GO_AUTHORITIES["result_blind_data_materialization"] = True

GO_TOP_LEVEL_KEYS = {
    "schema",
    "status",
    "scope",
    "issued_utc",
    "expires_utc",
    "challenge_nonce",
    "reviewer",
    "findings",
    "bindings",
    "authorities",
}
GO_REVIEWER_KEYS = {
    "reviewer_id",
    "independent",
    "result_blind",
    "reviewed_without_numerical_results",
}
GO_FINDING_KEYS = {"p0", "p1", "p2", "p3"}
GO_BINDING_KEYS = {
    "candidate_manifest_sha256",
    "candidate_sha256sums_sha256",
    "challenge_nonce",
    "artifact_sha256",
    "materialization_out_dir",
    "execution_receipt_dir",
    "runtime_identity_sha256",
    "host_identity_sha256",
    "materialization_contract_sha256",
    "sealed_runtime",
}

MATERIAL_OUTPUT_ORDER = (
    "arm_source_n10000.csv",
    "arm_source_n20000.csv",
    "fixed_common_holdout_manifest.json",
    "declared_midpoint_half_range_normalization_contract.json",
    "controlled_real10k_20k_nested_summary.json",
    "INDEPENDENT_QA_REQUIRED.json",
    "controlled_real10k_20k_nested_receipt.json",
)
MATERIAL_SHA_INDEX_NAME = "SHA256SUMS.txt"
PRODUCTION_EXACT_CHECKS = {
    "selection_seed_exact_20260824",
    "historical_10k_csv_identity_exact",
    "authoritative_100k_csv_identity_exact",
    "historical_model_summary_identity_exact",
    "historical_source_rows_exact_10000",
    "authoritative_source_rows_exact_100000",
    "historical_gradient_train_rows_exact_7871",
    "historical_validation_rows_exact_1227",
    "historical_test_rows_exact_902",
    "extra_rows_exact_10000",
    "new_gradient_train_rows_exact_17871",
}

VERIFIED_CONTEXT_SCHEMA = "controlled_real10k_20k_verified_materialization_inputs_v1"
VERIFIED_CONTEXT_ROLES = (
    "materialization_builder_code",
    "shared_contract_code",
    "splitter_code",
    "historical_10k_csv",
    "authoritative_100k_csv",
    "historical_model_summary_json",
)


class MaterializationGateError(RuntimeError):
    """The immutable candidate, exact GO, singleton, or material output is invalid."""


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


class _HeldSnapshot:
    """One no-follow open whose verified immutable bytes are the only consumer input."""

    def __init__(
        self,
        path: Path,
        label: str,
        *,
        expected_sha256: str | None = None,
        expected_mode: int | None = None,
        expected_record: Mapping[str, Any] | None = None,
        open_dir_fd: int | None = None,
        relative_name: str | None = None,
    ) -> None:
        self.path = _absolute_path(str(path), label)
        self.label = label
        _reject_symlink_chain(self.path, label)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            if open_dir_fd is None:
                self.fd = os.open(self.path, flags)
            else:
                if relative_name is None or Path(relative_name).name != relative_name:
                    raise MaterializationGateError(
                        f"unsafe held relative filename for {label}"
                    )
                self.fd = os.open(relative_name, flags, dir_fd=open_dir_fd)
        except OSError as exc:
            raise MaterializationGateError(f"cannot single-open {label}: {exc}") from exc
        try:
            before = os.fstat(self.fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise MaterializationGateError(
                    f"{label} must be a regular nlink=1 held file: {self.path}"
                )
            if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
                raise MaterializationGateError(
                    f"{label} mode must be {expected_mode:04o}: {self.path}"
                )
            if self.path.resolve(strict=True) != self.path:
                raise MaterializationGateError(f"{label} is not canonical: {self.path}")
            lexical = self.path.lstat()
            if _stat_identity(lexical) != _stat_identity(before):
                raise MaterializationGateError(
                    f"{label} path and held descriptor do not identify one file"
                )
            chunks: list[bytes] = []
            while True:
                block = os.read(self.fd, 1024 * 1024)
                if not block:
                    break
                chunks.append(block)
            self.raw = b"".join(chunks)
            after = os.fstat(self.fd)
            if _stat_identity(after) != _stat_identity(before):
                raise MaterializationGateError(f"{label} changed during held read")
            self._identity = _stat_identity(before)
            self.sha256 = hashlib.sha256(self.raw).hexdigest()
            if expected_sha256 is not None and self.sha256 != _normalized_sha(
                expected_sha256, f"{label} expected SHA-256"
            ):
                raise MaterializationGateError(f"{label} SHA-256 mismatch")
            if expected_record is not None:
                expected = {
                    "size_bytes": before.st_size,
                    "mode_octal": f"{stat.S_IMODE(before.st_mode):04o}",
                    "nlink": before.st_nlink,
                    "st_dev": before.st_dev,
                    "st_ino": before.st_ino,
                }
                if any(expected_record.get(key) != value for key, value in expected.items()):
                    raise MaterializationGateError(f"{label} bound stat identity drifted")
        except BaseException:
            os.close(self.fd)
            raise

    def assert_continuity(self) -> None:
        try:
            current_fd = os.fstat(self.fd)
            current_path = self.path.lstat()
        except OSError as exc:
            raise MaterializationGateError(
                f"{self.label} held/path continuity cannot be verified: {exc}"
            ) from exc
        if (
            _stat_identity(current_fd) != self._identity
            or _stat_identity(current_path) != self._identity
            or hashlib.sha256(self.raw).hexdigest() != self.sha256
        ):
            raise MaterializationGateError(f"{self.label} held snapshot continuity failed")

    def record(self) -> dict[str, Any]:
        metadata = os.fstat(self.fd)
        return {
            "logical_path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": metadata.st_size,
            "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
            "nlink": metadata.st_nlink,
            "st_dev": metadata.st_dev,
            "st_ino": metadata.st_ino,
            "bytes": self.raw,
        }

    def close(self) -> None:
        os.close(self.fd)


def _directory_identity(metadata: os.stat_result) -> dict[str, Any]:
    return {
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
        "st_uid": int(metadata.st_uid),
        "st_gid": int(metadata.st_gid),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


class _HeldDirectory:
    def __init__(self, path: Path, label: str) -> None:
        self.path = _absolute_path(str(path), label)
        self.label = label
        self.parent_path = self.path.parent
        _reject_symlink_chain(self.parent_path, f"{label} parent")
        self.parent_fd = os.open(
            self.parent_path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            self.root_fd = os.open(
                self.path.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.parent_fd,
            )
        except BaseException:
            os.close(self.parent_fd)
            raise
        self.parent_identity = _directory_identity(os.fstat(self.parent_fd))
        self.root_identity = _directory_identity(os.fstat(self.root_fd))
        self.assert_continuity()

    def assert_continuity(self) -> None:
        try:
            parent_path = self.parent_path.lstat()
            root_path = os.stat(
                self.path.name, dir_fd=self.parent_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise MaterializationGateError(
                f"{self.label} directory continuity failed: {exc}"
            ) from exc
        if (
            _directory_identity(os.fstat(self.parent_fd)) != self.parent_identity
            or _directory_identity(parent_path) != self.parent_identity
            or _directory_identity(os.fstat(self.root_fd)) != self.root_identity
            or _directory_identity(root_path) != self.root_identity
        ):
            raise MaterializationGateError(
                f"{self.label} held parent/root inode continuity failed"
            )

    def close(self) -> None:
        os.close(self.root_fd)
        os.close(self.parent_fd)


class _HeldClosure:
    def __init__(self) -> None:
        self.entries: dict[str, _HeldSnapshot] = {}
        self.directories: dict[str, _HeldDirectory] = {}

    def open(
        self,
        key: str,
        path: Path,
        label: str,
        *,
        expected_sha256: str | None = None,
        expected_mode: int | None = None,
        expected_record: Mapping[str, Any] | None = None,
    ) -> _HeldSnapshot:
        if key in self.entries:
            existing = self.entries[key]
            if existing.path != path:
                raise MaterializationGateError(f"held snapshot key reused for another path: {key}")
            existing.assert_continuity()
            if expected_sha256 is not None and existing.sha256 != _normalized_sha(
                expected_sha256, f"{label} expected SHA-256"
            ):
                raise MaterializationGateError(f"held snapshot SHA mismatch: {key}")
            return existing
        snapshot = _HeldSnapshot(
            path,
            label,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
            expected_record=expected_record,
        )
        self.entries[key] = snapshot
        return snapshot

    def hold_directory(self, key: str, path: Path, label: str) -> _HeldDirectory:
        if key in self.directories:
            directory = self.directories[key]
            if directory.path != path:
                raise MaterializationGateError(
                    f"held directory key reused for another path: {key}"
                )
            directory.assert_continuity()
            return directory
        directory = _HeldDirectory(path, label)
        self.directories[key] = directory
        return directory

    def open_at(
        self,
        key: str,
        directory: _HeldDirectory,
        name: str,
        label: str,
        *,
        expected_sha256: str | None = None,
        expected_mode: int | None = None,
        expected_record: Mapping[str, Any] | None = None,
        in_parent: bool = False,
    ) -> _HeldSnapshot:
        path = (directory.parent_path if in_parent else directory.path) / name
        if key in self.entries:
            existing = self.entries[key]
            if existing.path != path:
                raise MaterializationGateError(f"held snapshot key reused: {key}")
            existing.assert_continuity()
            if expected_sha256 is not None and existing.sha256 != _normalized_sha(
                expected_sha256, f"{label} expected SHA-256"
            ):
                raise MaterializationGateError(f"held snapshot SHA mismatch: {key}")
            return existing
        snapshot = _HeldSnapshot(
            path,
            label,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
            expected_record=expected_record,
            open_dir_fd=directory.parent_fd if in_parent else directory.root_fd,
            relative_name=name,
        )
        self.entries[key] = snapshot
        return snapshot

    def assert_continuity(self) -> None:
        for key in sorted(self.directories):
            self.directories[key].assert_continuity()
        for key in sorted(self.entries):
            self.entries[key].assert_continuity()

    def close(self) -> None:
        for key in reversed(tuple(self.entries)):
            self.entries[key].close()
        self.entries.clear()
        for key in reversed(tuple(self.directories)):
            self.directories[key].close()
        self.directories.clear()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    if type(value) is not str:
        return False
    raw = value
    return len(raw) == 64 and all(character in "0123456789abcdef" for character in raw)


def _normalized_sha(value: Any, label: str) -> str:
    if type(value) is not str or not _is_sha256(value):
        raise MaterializationGateError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_utc(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise MaterializationGateError(f"{label} must be a JSON string")
    raw = value
    if not STRICT_UTC_RE.fullmatch(raw):
        raise MaterializationGateError(f"{label} must be strict UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise MaterializationGateError(f"{label} is not a valid UTC timestamp") from exc


def _runtime_bootstrap_module() -> Any:
    try:
        return importlib.import_module(
            "rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap"
        )
    except (ImportError, AttributeError) as exc:
        raise MaterializationGateError(
            "sealed runtime bootstrap module is unavailable; raw-path fallback is forbidden"
        ) from exc


def _require_sealed_runtime(expected_runtime_closure_json_sha256: str) -> dict[str, Any]:
    expected_sha = _normalized_sha(
        expected_runtime_closure_json_sha256,
        "expected sealed runtime-closure JSON SHA-256",
    )
    bootstrap = _runtime_bootstrap_module()
    try:
        state = bootstrap.require_active_runtime("materialization", expected_sha)
    except BaseException as exc:
        raise MaterializationGateError(
            "materialization requires descriptor-bootstrapped sealed runtime"
        ) from exc
    expected_keys = {
        "schema",
        "entrypoint",
        "manifest_sha256",
        "pure_archive_sha256",
        "bootstrap_sha256",
    }
    if not isinstance(state, dict) or set(state) != expected_keys:
        raise MaterializationGateError("sealed runtime attestation keyset is not exact")
    if (
        state["schema"] != "controlled_real10k_20k_runtime_attestation_v1"
        or state["entrypoint"] != "materialization"
        or state["manifest_sha256"] != expected_sha
    ):
        raise MaterializationGateError("sealed runtime attestation identity is invalid")
    for key in ("manifest_sha256", "pure_archive_sha256", "bootstrap_sha256"):
        _normalized_sha(state[key], f"sealed runtime attestation {key}")
    return dict(state)


def _active_member_source(role: str, expected_sha256: str) -> tuple[bytes, str]:
    bootstrap = _runtime_bootstrap_module()
    try:
        payload, origin = bootstrap.active_member_source(
            role, _normalized_sha(expected_sha256, f"sealed member {role} SHA-256")
        )
    except BaseException as exc:
        raise MaterializationGateError(
            f"sealed runtime member is unavailable without fallback: {role}"
        ) from exc
    if type(payload) is not bytes or type(origin) is not str or not origin:
        raise MaterializationGateError(f"sealed runtime member API is invalid: {role}")
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise MaterializationGateError(f"sealed runtime member SHA mismatch: {role}")
    return payload, origin


def _reject_symlink_chain(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise MaterializationGateError(f"{label} traverses a symlink: {current}")


def _absolute_path(raw: str, label: str) -> Path:
    if not raw or "\x00" in raw:
        raise MaterializationGateError(f"{label} is empty or contains NUL")
    path = Path(raw).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise MaterializationGateError(f"{label} must be absolute without traversal: {raw!r}")
    return path


def _canonical_regular_file(raw: str | Path, label: str) -> Path:
    path = _absolute_path(str(raw), label)
    _reject_symlink_chain(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise MaterializationGateError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise MaterializationGateError(f"{label} must be a regular nlink=1 file: {path}")
    if path.resolve(strict=True) != path:
        raise MaterializationGateError(f"{label} is not canonical: {path}")
    return path


def _canonical_existing_dir(raw: str | Path, label: str) -> Path:
    path = _absolute_path(str(raw), label)
    _reject_symlink_chain(path, label)
    if not path.is_dir() or path.resolve(strict=True) != path:
        raise MaterializationGateError(f"{label} must be an existing canonical directory: {path}")
    return path


def _canonical_future_dir(raw: str, label: str) -> Path:
    path = _absolute_path(raw, label)
    _reject_symlink_chain(path.parent, f"{label} parent")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"{label} already exists (no-clobber): {path}")
    if not path.parent.is_dir() or path.parent.resolve(strict=True) != path.parent:
        raise MaterializationGateError(f"{label} parent must be an existing canonical directory")
    if path.resolve(strict=False) != path:
        raise MaterializationGateError(f"{label} is not canonical: {path}")
    return path


def _require_disjoint_dirs(paths: Mapping[str, Path]) -> None:
    items = list(paths.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise MaterializationGateError(
                    f"{left_name} and {right_name} must be disjoint: {left} / {right}"
                )


def _file_record(role: str, path: Path, expected_sha: str) -> dict[str, Any]:
    snapshot = _HeldSnapshot(path, role, expected_sha256=expected_sha)
    try:
        record = snapshot.record()
        return {
            "role": role,
            "path": record["logical_path"],
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "mode_octal": record["mode_octal"],
            "nlink": record["nlink"],
            "st_dev": record["st_dev"],
            "st_ino": record["st_ino"],
        }
    finally:
        snapshot.close()


def _binding_record(role: str, snapshot: _HeldSnapshot) -> dict[str, Any]:
    record = snapshot.record()
    return {
        "role": role,
        "path": record["logical_path"],
        "sha256": record["sha256"],
        "size_bytes": record["size_bytes"],
        "mode_octal": record["mode_octal"],
        "nlink": record["nlink"],
        "st_dev": record["st_dev"],
        "st_ino": record["st_ino"],
    }


def _active_numpy_identity() -> tuple[str, str]:
    try:
        numpy_module = importlib.import_module("numpy")
    except ImportError as exc:
        raise MaterializationGateError(
            "sealed runtime did not provide descriptor-bound NumPy"
        ) from exc
    numpy_version = getattr(numpy_module, "__version__", None)
    numpy_spec = getattr(numpy_module, "__spec__", None)
    numpy_origin = getattr(numpy_spec, "origin", None)
    if (
        type(numpy_version) is not str
        or type(numpy_origin) is not str
        or not (
            numpy_origin.startswith("descriptor-zip:/proc/self/fd/203!/")
            or numpy_origin.startswith("/proc/self/fd/")
        )
    ):
        raise MaterializationGateError(
            "NumPy is not sourced from the active descriptor-sealed runtime"
        )
    return numpy_version, numpy_origin


def _runtime_identity(raw_executable: str, expected_sha: str) -> dict[str, Any]:
    requested = _absolute_path(raw_executable, "--python-executable")
    try:
        canonical = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise MaterializationGateError("Python executable is missing") from exc
    _reject_symlink_chain(canonical, "resolved Python executable")
    metadata = canonical.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise MaterializationGateError("resolved Python executable is not a regular file")
    actual = _sha256(canonical)
    if actual != _normalized_sha(expected_sha, "Python executable expected SHA-256"):
        raise MaterializationGateError("Python executable SHA-256 mismatch")
    if canonical != Path(sys.executable).resolve(strict=True):
        raise MaterializationGateError(
            "PREPARE must run under the exact Python executable being bound"
        )
    numpy_version, numpy_origin = _active_numpy_identity()
    identity = {
        "requested_path": str(requested),
        "canonical_path": str(canonical),
        "sha256": actual,
        "size_bytes": metadata.st_size,
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "python_implementation": sys.implementation.name,
        "numpy_version": numpy_version,
        "numpy_origin": numpy_origin,
        "descriptor_sealed_runtime": True,
    }
    identity["identity_sha256"] = _canonical_json_sha(identity)
    return identity


def _optional_identity_file(path: Path) -> str | None:
    try:
        _reject_symlink_chain(path, str(path))
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, MaterializationGateError):
        return None


def _optional_text_file(path: Path) -> str | None:
    try:
        _reject_symlink_chain(path, str(path))
        if not path.is_file():
            return None
        value = path.read_text(encoding="ascii").strip().lower()
        return value or None
    except (OSError, UnicodeError, MaterializationGateError):
        return None


def _host_identity() -> dict[str, Any]:
    uname = os.uname()
    identity = {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "uid": os.getuid(),
        "gid": os.getgid(),
        "uname": {
            "sysname": uname.sysname,
            "nodename": uname.nodename,
            "release": uname.release,
            "version": uname.version,
            "machine": uname.machine,
        },
        "machine_id_sha256": _optional_identity_file(Path("/etc/machine-id")),
        "boot_id": _optional_text_file(Path("/proc/sys/kernel/random/boot_id")),
        "boot_id_sha256": _optional_identity_file(Path("/proc/sys/kernel/random/boot_id")),
    }
    identity["identity_sha256"] = _canonical_json_sha(identity)
    return identity


def _mars_preflight_authorities() -> dict[str, bool]:
    return {
        "direct_data_materialization_authorized": False,
        "training_authorized": False,
        "common_test_access_authorized": False,
        "numerical_metric_access_authorized": False,
        "fresh_emx_authorized": False,
        "process_signal_authorized": False,
    }


def _package_authorities() -> dict[str, bool]:
    return {
        "native_linux_test_execution": False,
        "data_materialization": False,
        "training": False,
        "common_test_access": False,
        "numerical_metric_access": False,
        "fresh_emx": False,
        "process_signal": False,
    }


def _strict_attempt_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", value
    ):
        raise MaterializationGateError(
            f"{label} must be strict UTC YYYY-MM-DDTHH:MM:SS+00:00"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MaterializationGateError(f"{label} is not a valid UTC timestamp") from exc
    if parsed.utcoffset() != timedelta(0) or parsed.microsecond != 0:
        raise MaterializationGateError(f"{label} is not exact second-resolution UTC")
    return parsed


def _attempt_directory_record(directory: _HeldDirectory, *, parent: bool) -> dict[str, Any]:
    identity = directory.parent_identity if parent else directory.root_identity
    path = directory.parent_path if parent else directory.path
    return {
        "path": str(path),
        "st_dev": identity["st_dev"],
        "st_ino": identity["st_ino"],
        "mode_octal": identity["mode_octal"],
    }


def _validate_package_attempt_invocation(
    invocation: Any,
    *,
    package_root: str,
    attempt_root: Path,
    package_spec_sha256: str,
    builder_sha256: str,
) -> None:
    if type(invocation) is not dict or set(invocation) != {
        "argv",
        "cwd",
        "output_dir",
        "failure_receipt_dir",
        "package_spec",
        "builder",
        "python",
        "runtime",
        "environment",
    }:
        raise MaterializationGateError("package attempt invocation keyset is invalid")
    if (
        type(invocation["argv"]) is not list
        or not invocation["argv"]
        or any(type(value) is not str for value in invocation["argv"])
        or invocation["output_dir"] != package_root
        or invocation["failure_receipt_dir"] != str(attempt_root)
    ):
        raise MaterializationGateError("package attempt invocation paths/argv are invalid")
    cwd = invocation["cwd"]
    if (
        type(cwd) is not dict
        or set(cwd) != {"lexical", "resolved", "device", "inode"}
        or type(cwd["lexical"]) is not str
        or type(cwd["resolved"]) is not str
        or type(cwd["device"]) is not int
        or type(cwd["inode"]) is not int
    ):
        raise MaterializationGateError("package attempt invocation cwd is invalid")
    package_spec = invocation["package_spec"]
    builder = invocation["builder"]
    if (
        type(package_spec) is not dict
        or set(package_spec) != {"path", "expected_sha256"}
        or type(package_spec["path"]) is not str
        or package_spec["expected_sha256"] != package_spec_sha256
        or type(builder) is not dict
        or set(builder) != {"path", "expected_sha256"}
        or type(builder["path"]) is not str
        or builder["expected_sha256"] != builder_sha256
    ):
        raise MaterializationGateError("package attempt invocation source identity is invalid")
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
        raise MaterializationGateError("package attempt invocation Python keyset is invalid")
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
        raise MaterializationGateError("package attempt invocation Python identity is invalid")
    _normalized_sha(python["executable_sha256"], "package attempt Python executable")
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
        raise MaterializationGateError("package attempt invocation runtime is invalid")
    environment = invocation["environment"]
    if type(environment) is not dict or set(environment) != {
        "raw_values_recorded",
        "key_count",
        "keys",
        "keyset_sha256",
        "key_value_map_sha256",
    }:
        raise MaterializationGateError("package attempt invocation environment keyset is invalid")
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
        raise MaterializationGateError("package attempt invocation environment is invalid")
    _normalized_sha(environment["key_value_map_sha256"], "package attempt environment map")


def _audit_package_build_attempt_from_preflight(
    package: Mapping[str, Any], *, held: _HeldClosure
) -> dict[str, _HeldSnapshot]:
    exact_package_keys = {
        "root",
        "manifest_sha256",
        "sha_index_sha256",
        "receipt_sha256",
        "independent_qa_required_sha256",
        "commit_sha256",
        "build_attempt_body_path",
        "build_attempt_body_sha256",
        "build_attempt_committed_path",
        "build_attempt_committed_sha256",
        "role_sha256",
        "role_identity",
        "runtime_dependency_closure",
        "runtime_entrypoints",
    }
    if type(package) is not dict or set(package) != exact_package_keys:
        raise MaterializationGateError("MARS preflight package keyset is not exact v5")
    for key in (
        "manifest_sha256",
        "sha_index_sha256",
        "receipt_sha256",
        "independent_qa_required_sha256",
        "commit_sha256",
        "build_attempt_body_sha256",
        "build_attempt_committed_sha256",
    ):
        _normalized_sha(package[key], f"MARS preflight package {key}")
    body_path = _canonical_regular_file(
        package["build_attempt_body_path"], "package build-attempt body"
    )
    committed_path = _canonical_regular_file(
        package["build_attempt_committed_path"], "package build-attempt committed"
    )
    if (
        body_path.name != PACKAGE_BUILD_ATTEMPT_BODY_NAME
        or committed_path.name != PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
        or body_path.parent != committed_path.parent
    ):
        raise MaterializationGateError("package build-attempt paths are not exact siblings")
    directory = held.hold_directory(
        "package_build_attempt_root", body_path.parent, "package build-attempt root"
    )
    if directory.root_identity["mode_octal"] != "0555":
        raise MaterializationGateError("package build-attempt root mode must be 0555")
    exact_names = {
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
    }
    names = set(os.listdir(directory.root_fd))
    if names != exact_names:
        raise MaterializationGateError(
            "package build-attempt success closure is not exact: "
            f"missing={sorted(exact_names-names)} extra={sorted(names-exact_names)}"
        )
    body_snapshot = held.open_at(
        "bound:package_build_attempt_body",
        directory,
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        "package build-attempt body",
        expected_sha256=package["build_attempt_body_sha256"],
        expected_mode=FILE_MODE,
    )
    committed_snapshot = held.open_at(
        "bound:package_build_attempt_committed",
        directory,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
        "package build-attempt committed marker",
        expected_sha256=package["build_attempt_committed_sha256"],
        expected_mode=FILE_MODE,
    )
    body = _json_from_bytes(body_snapshot.raw, "package build-attempt body")
    if set(body) != {
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
        raise MaterializationGateError("package build-attempt body keyset is invalid")
    if (
        body["schema"] != PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA
        or body["status"] != "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
        or body["partial_output_preserved"] is not False
        or body["execution_authorized"] is not False
    ):
        raise MaterializationGateError("package build-attempt body status is invalid")
    started = _strict_attempt_utc(body["started_utc"], "package attempt started_utc")
    completed = _strict_attempt_utc(
        body["completed_utc"], "package attempt completed_utc"
    )
    if completed < started:
        raise MaterializationGateError("package build-attempt completed before it started")
    _require_exact_json_equal(
        body["authorities"], _package_authorities(), "package attempt body authorities"
    )
    body_package = body.get("package")
    if type(body_package) is not dict or set(body_package) != {
        "path",
        "manifest_sha256",
        "receipt_sha256",
        "independent_qa_required_sha256",
        "sha256sums_sha256",
        "package_commit_sha256",
        "file_count",
    }:
        raise MaterializationGateError("package build-attempt package keyset is invalid")
    _require_exact_json_equal(
        {key: body_package[key] for key in body_package if key != "file_count"},
        {
            "path": package["root"],
            "manifest_sha256": package["manifest_sha256"],
            "receipt_sha256": package["receipt_sha256"],
            "independent_qa_required_sha256": package[
                "independent_qa_required_sha256"
            ],
            "sha256sums_sha256": package["sha_index_sha256"],
            "package_commit_sha256": package["commit_sha256"],
        },
        "package attempt body package closure",
    )
    if type(body_package["file_count"]) is not int or body_package["file_count"] < 1:
        raise MaterializationGateError("package build-attempt file_count is invalid")
    observed = body.get("observed_identity")
    if type(observed) is not dict or set(observed) != {
        "package_spec_sha256",
        "builder_sha256",
        "package_output_device",
        "package_output_inode",
    }:
        raise MaterializationGateError("package build-attempt observed identity is invalid")
    _normalized_sha(observed["package_spec_sha256"], "package build spec SHA")
    _normalized_sha(observed["builder_sha256"], "package builder SHA")
    if (
        type(observed["package_output_device"]) is not int
        or type(observed["package_output_inode"]) is not int
        or type(package["role_sha256"]) is not dict
        or observed["builder_sha256"]
        != package["role_sha256"].get("package_builder_code")
    ):
        raise MaterializationGateError("package build-attempt observed identity is invalid")
    _validate_package_attempt_invocation(
        body["invocation"],
        package_root=package["root"],
        attempt_root=directory.path,
        package_spec_sha256=observed["package_spec_sha256"],
        builder_sha256=observed["builder_sha256"],
    )

    terminal = _json_from_bytes(
        committed_snapshot.raw, "package build-attempt committed marker"
    )
    if set(terminal) != {
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
    }:
        raise MaterializationGateError("package build-attempt committed keyset is invalid")
    if (
        terminal["schema"] != PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA
        or terminal["status"] != "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED"
        or terminal["execution_authorized"] is not False
    ):
        raise MaterializationGateError("package build-attempt committed status is invalid")
    _strict_attempt_utc(terminal["committed_utc"], "package attempt committed_utc")
    _require_exact_json_equal(
        terminal["body"],
        {
            "path": str(body_path),
            "sha256": body_snapshot.sha256,
            "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        },
        "package attempt committed body binding",
    )
    package_commit_path = Path(package["root"]) / "PACKAGE_COMMIT.json"
    _require_exact_json_equal(
        terminal["package_commit"],
        {
            "path": str(package_commit_path),
            "sha256": package["commit_sha256"],
            "schema": PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        },
        "package attempt committed package binding",
    )
    _require_exact_json_equal(
        terminal["package_root"],
        {
            "path": package["root"],
            "st_dev": observed["package_output_device"],
            "st_ino": observed["package_output_inode"],
            "mode_octal": "0555",
        },
        "package attempt committed package-root identity",
    )
    _require_exact_json_equal(
        terminal["attempt_root"],
        _attempt_directory_record(directory, parent=False),
        "package attempt root identity",
    )
    _require_exact_json_equal(
        terminal["attempt_parent"],
        _attempt_directory_record(directory, parent=True),
        "package attempt parent identity",
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
        "package attempt publication",
    )
    _require_exact_json_equal(
        terminal["authorities"], _package_authorities(), "package attempt authorities"
    )
    directory.assert_continuity()
    body_snapshot.assert_continuity()
    committed_snapshot.assert_continuity()
    if set(os.listdir(directory.root_fd)) != exact_names:
        raise MaterializationGateError("package build-attempt closure changed during audit")
    return {
        "package_build_attempt_body": body_snapshot,
        "package_build_attempt_committed": committed_snapshot,
    }


def _audit_mars_preflight_body(
    payload: Any,
    *,
    held: _HeldClosure,
    bindings: Mapping[str, Mapping[str, Any]],
    runtime: Mapping[str, Any],
    host: Mapping[str, Any],
    candidate_dir: Path,
    materialization_out_dir: Path,
    execution_receipt_dir: Path,
) -> dict[str, _HeldSnapshot]:
    if not isinstance(payload, dict):
        raise MaterializationGateError("MARS preflight receipt must be a JSON object")
    if set(payload) != {
        "schema",
        "status",
        "started_utc",
        "body_generated_utc",
        "package",
        "external_code_go",
        "receipt_transaction",
        "host_identity",
        "runtime_identity",
        "process_singleton",
        "candidate_output_dirs",
        "candidate_output_dirs_absent_before_and_after",
        "native_tests",
        "host_load_snapshot",
        "checks",
        "preflight_pass",
        "committed_terminal_marker_required",
        "authorities",
        "next_legal_action",
    }:
        raise MaterializationGateError("MARS preflight receipt keyset is not exact v3")
    if (
        payload.get("schema") != MARS_PREFLIGHT_BODY_SCHEMA
        or payload.get("status") != "PASS_BODY_AWAITING_DURABLE_COMMIT"
        or payload.get("preflight_pass") is not False
    ):
        raise MaterializationGateError("MARS preflight body is not a non-authoritative PASS body")
    _require_exact_json_equal(
        payload.get("authorities"),
        _mars_preflight_authorities(),
        "MARS preflight body authorities",
    )
    if payload.get("committed_terminal_marker_required") != MARS_PREFLIGHT_COMMITTED_NAME:
        raise MaterializationGateError("MARS preflight receipt grants a direct authority")
    if payload.get("next_legal_action") != "NO_ACTION_UNTIL_DURABLE_COMMITTED_MARKER_IS_VERIFIED":
        raise MaterializationGateError("MARS preflight next legal action is invalid")
    observed_host = payload.get("host_identity")
    expected_host = {
        "hostname": host["hostname"],
        "uid": host["uid"],
        "boot_id": host["boot_id"],
    }
    if observed_host != expected_host:
        raise MaterializationGateError("MARS preflight host identity does not match candidate host")
    package = payload.get("package")
    attempt_snapshots = _audit_package_build_attempt_from_preflight(
        package, held=held
    )
    role_sha = package.get("role_sha256") if isinstance(package, dict) else None
    role_identity = package.get("role_identity") if isinstance(package, dict) else None
    runtime_closure = (
        package.get("runtime_dependency_closure")
        if isinstance(package, dict)
        else None
    )
    if (
        not isinstance(role_sha, dict)
        or not isinstance(role_identity, dict)
        or not isinstance(runtime_closure, dict)
    ):
        raise MaterializationGateError(
            "MARS preflight package/runtime role identity is missing"
        )
    try:
        expected_active_runtime = {
            "schema": "controlled_real10k_20k_runtime_attestation_v1",
            "entrypoint": "native_smoke",
            "manifest_sha256": role_identity[
                "runtime_dependency_closure_json"
            ]["sha256"],
            "pure_archive_sha256": runtime_closure["pure_archive"]["sha256"],
            "bootstrap_sha256": role_identity["runtime_bootstrap_code"]["sha256"],
        }
        system_library_allowlist = runtime_closure["system_library_allowlist"]
    except (KeyError, TypeError) as exc:
        raise MaterializationGateError(
            "MARS preflight package lacks descriptor-runtime closure bindings"
        ) from exc
    observed_runtime = payload.get("runtime_identity")
    runtime_keys = {
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
    if not isinstance(observed_runtime, dict) or set(observed_runtime) != runtime_keys:
        raise MaterializationGateError("MARS preflight runtime identity is not exact production")
    expected_code_roles = {
        role: role_sha[role]
        for role in (
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
        )
    }
    expected_runtime_core = {
        "schema": "controlled_real10k_20k_preflight_runtime_identity_v2",
        "python_executable_path": runtime["canonical_path"],
        "python_executable_sha256": runtime["sha256"],
        "python_version": EXPECTED_PRODUCTION_PYTHON_VERSION,
        "numpy_version": EXPECTED_PRODUCTION_NUMPY_VERSION,
        "active_runtime": expected_active_runtime,
        "compiled_role_count": len(expected_code_roles) + 1,
        "consumed_code_role_sha256": expected_code_roles,
        "descriptor_closed": True,
        "raw_runtime_fallback_authorized": False,
    }
    for key, expected in expected_runtime_core.items():
        _require_exact_json_equal(
            observed_runtime[key], expected, f"MARS preflight runtime {key}"
        )
    for key in ("native_smoke_result_sha256", "native_smoke_attestation_sha256"):
        _normalized_sha(observed_runtime[key], f"MARS preflight runtime {key}")
    startup = observed_runtime["startup_attestation"]
    terminal = observed_runtime["terminal_attestation"]
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
    if (
        not isinstance(startup, dict)
        or set(startup) != startup_keys
        or not isinstance(terminal, dict)
        or set(terminal) != terminal_keys
        or startup["status"] != "PASS_DESCRIPTOR_CLOSED_STARTUP"
        or terminal["status"] != "PASS_DESCRIPTOR_CLOSED_TERMINAL"
        or type(terminal["exit_code"]) is not int
        or terminal["exit_code"] != 0
        or startup["entrypoint_sha256"] != role_sha["native_smoke_test"]
        or startup["python"]
        != {
            key: runtime_closure["python"][key]
            for key in ("implementation", "version", "abi_tag", "platform")
        }
        or startup["python_flags"]
        != {"isolated": 1, "no_site": 1, "dont_write_bytecode": True}
        or startup["numpy_version"] != runtime_closure["numpy"]["version"]
        or startup["native_library_sha256"]
        != {
            record["soname"]: record["sha256"]
            for record in runtime_closure["native_libraries"]
        }
        or startup["native_extension_sha256"]
        != {
            record["module"]: record["sha256"]
            for record in runtime_closure["native_extensions"]
        }
        or startup["system_library_allowlist"] != system_library_allowlist
        or terminal["system_library_allowlist"] != system_library_allowlist
        or startup["external_package_fallback_allowed"] is not False
        or terminal["external_package_fallback_allowed"] is not False
        or startup["site_initialization_disabled"] is not True
    ):
        raise MaterializationGateError(
            "MARS preflight descriptor-runtime terminal closure is invalid"
        )
    for field, expected in expected_active_runtime.items():
        if startup[field] != expected or terminal[field] != expected:
            raise MaterializationGateError(
                f"MARS preflight runtime attestation mismatch: {field}"
            )
    for label, origins in (
        ("startup", startup["module_origins"]),
        ("terminal", terminal["module_origins"]),
    ):
        if not isinstance(origins, dict) or not origins:
            raise MaterializationGateError(
                f"MARS preflight {label} module origins are empty"
            )
        for module_name, record in origins.items():
            if (
                type(module_name) is not str
                or not isinstance(record, dict)
                or set(record) != {"kind", "origin", "sha256"}
                or record["kind"]
                not in {"sealed_pure_zip", "sealed_native_extension"}
                or type(record["origin"]) is not str
                or not (
                    record["origin"].startswith(
                        "descriptor-zip:/proc/self/fd/203!/"
                    )
                    or record["origin"].startswith("/proc/self/fd/")
                )
                or not _is_sha256(record["sha256"])
            ):
                raise MaterializationGateError(
                    f"MARS preflight {label} module origin is not descriptor-bound"
                )
    native_tests = payload.get("native_tests")
    native_test_keys = {
        "requested",
        "roles",
        "returncode",
        "stdout_sha256",
        "stdout_size_bytes",
        "stderr_sha256",
        "stderr_size_bytes",
        "elapsed_seconds",
        "attestation_sha256",
        "attestation_size_bytes",
        "attestation_ephemeral_no_clobber_file",
        "protocol_schema",
        "test_id",
        "executed_test_count",
        "exact_structured_pass",
        "isolated_python_flags",
        "environment_keyset",
        "runtime_identity",
    }
    elapsed = native_tests.get("elapsed_seconds") if isinstance(native_tests, dict) else None
    if (
        not isinstance(native_tests, dict)
        or set(native_tests) != native_test_keys
        or native_tests["requested"] is not True
        or type(native_tests["returncode"]) is not int
        or native_tests["returncode"] != 0
        or native_tests["roles"] != ["native_smoke_test"]
        or native_tests["protocol_schema"]
        != "controlled_real10k_20k_native_smoke_result_v3"
        or native_tests["test_id"] != "descriptor_closed_package_consumer_graph_v5"
        or type(native_tests["executed_test_count"]) is not int
        or native_tests["executed_test_count"] != 1
        or native_tests["exact_structured_pass"] is not True
        or native_tests["isolated_python_flags"] != ["-I", "-B", "-S"]
        or native_tests["attestation_ephemeral_no_clobber_file"] is not True
        or type(native_tests["stdout_size_bytes"]) is not int
        or native_tests["stdout_size_bytes"] < 1
        or type(native_tests["stderr_size_bytes"]) is not int
        or native_tests["stderr_size_bytes"] != 0
        or type(native_tests["attestation_size_bytes"]) is not int
        or native_tests["attestation_size_bytes"] < 1
        or native_tests["stderr_sha256"]
        != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        or native_tests["stdout_sha256"]
        != observed_runtime["native_smoke_result_sha256"]
        or native_tests["attestation_sha256"]
        != observed_runtime["native_smoke_attestation_sha256"]
        or type(elapsed) not in {int, float}
        or not math.isfinite(elapsed)
        or elapsed < 0
        or native_tests["environment_keyset"]
        != [
            "CONTROLLED_REAL10K_20K_PREFLIGHT_ONLY",
            "LANG",
            "LC_ALL",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
        ]
    ):
        raise MaterializationGateError("MARS preflight native tests did not pass exactly")
    _require_exact_json_equal(
        native_tests["runtime_identity"],
        observed_runtime,
        "MARS native-smoke/preflight runtime identity",
    )
    expected_dirs = {
        str(candidate_dir),
        str(materialization_out_dir),
        str(execution_receipt_dir),
    }
    observed_dirs = payload.get("candidate_output_dirs")
    if (
        not isinstance(observed_dirs, list)
        or len(observed_dirs) != 3
        or set(observed_dirs) != expected_dirs
        or payload.get("candidate_output_dirs_absent_before_and_after") is not True
    ):
        raise MaterializationGateError("MARS preflight candidate output directories are not exact")
    singleton = payload.get("process_singleton")
    if not isinstance(singleton, dict) or (
        singleton.get("all_counts_zero") is not True
        or singleton.get("current_uid_only") is not True
    ):
        raise MaterializationGateError("MARS preflight singleton proof is invalid")
    for package_role, binding_role in PREFLIGHT_PACKAGE_ROLE_TO_BINDING.items():
        if role_sha.get(package_role) != bindings[binding_role]["sha256"]:
            raise MaterializationGateError(
                f"MARS preflight package role does not match candidate: {package_role}"
            )
    checks = payload.get("checks")
    if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
        raise MaterializationGateError("MARS preflight checks are not all exact true")
    return attempt_snapshots


def _preflight_snapshot_record(
    snapshot: _HeldSnapshot, path_value: str
) -> dict[str, Any]:
    metadata = os.fstat(snapshot.fd)
    return {
        "path": path_value,
        "sha256": snapshot.sha256,
        "identity": {
            **_directory_identity(metadata),
            "nlink": int(metadata.st_nlink),
            "size_bytes": int(metadata.st_size),
        },
    }


def _preflight_index_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        f"{record['sha256']}  {record['path']}\n" for record in records
    ).encode("ascii")


def _audit_committed_preflight_closure(
    root_raw: str | Path,
    expected_committed_sha256: str,
    *,
    held: _HeldClosure,
    bindings: Mapping[str, Mapping[str, Any]],
    runtime: Mapping[str, Any],
    host: Mapping[str, Any],
    candidate_dir: Path,
    materialization_out_dir: Path,
    execution_receipt_dir: Path,
) -> dict[str, _HeldSnapshot]:
    root = _canonical_existing_dir(str(root_raw), "MARS preflight receipt root")
    directory = held.hold_directory(
        "mars_preflight_receipt_root", root, "MARS preflight receipt root"
    )
    if stat.S_IMODE(os.fstat(directory.root_fd).st_mode) != FROZEN_DIRECTORY_MODE:
        raise MaterializationGateError("committed MARS preflight root mode must be 0555")
    names = set(os.listdir(directory.root_fd))
    if MARS_PREFLIGHT_FAILURE_NAME in names or MARS_PREFLIGHT_FAILURE_INDEX_NAME in names:
        raise MaterializationGateError(
            "MARS preflight failure closure has absolute precedence"
        )
    if names != set(MARS_PREFLIGHT_SUCCESS_FILES):
        raise MaterializationGateError(
            "MARS preflight committed closure is not exact: "
            f"missing={sorted(set(MARS_PREFLIGHT_SUCCESS_FILES)-names)} "
            f"extra={sorted(names-set(MARS_PREFLIGHT_SUCCESS_FILES))}"
        )
    committed_snapshot = held.open_at(
        "bound:mars_preflight_committed",
        directory,
        MARS_PREFLIGHT_COMMITTED_NAME,
        "MARS preflight committed marker",
        expected_sha256=expected_committed_sha256,
        expected_mode=FILE_MODE,
    )
    committed = _json_from_bytes(
        committed_snapshot.raw, "MARS preflight committed marker"
    )
    exact_committed_keys = {
        "schema",
        "status",
        "committed_utc",
        "preflight_pass",
        "receipt_root",
        "receipt_parent",
        "prepared_artifacts",
        "receipt_body",
        "sha256_index",
        "external_code_go",
        "consumed_external_one_use_lease",
        "process_singleton",
        "exact_root_filenames",
        "failure_marker_absent_at_commit",
        "failure_marker_has_absolute_precedence",
        "body_is_not_authority",
        "authorities",
        "next_legal_action",
    }
    if set(committed) != exact_committed_keys:
        raise MaterializationGateError("MARS committed marker keyset is not exact")
    if (
        committed.get("schema") != MARS_PREFLIGHT_COMMITTED_SCHEMA
        or committed.get("status") != "COMMITTED_PASS_PREFLIGHT_ONLY"
        or committed.get("preflight_pass") is not True
        or committed.get("failure_marker_absent_at_commit") is not True
        or committed.get("failure_marker_has_absolute_precedence") is not True
        or committed.get("body_is_not_authority") is not True
        or committed.get("exact_root_filenames") != list(MARS_PREFLIGHT_SUCCESS_FILES)
        or committed.get("next_legal_action")
        != "SEPARATE_RESULT_BLIND_MATERIALIZATION_RECEIPT_AND_EXACT_AUTHORIZATION_REQUIRED"
    ):
        raise MaterializationGateError("MARS committed marker terminal contract is invalid")
    _strict_utc(committed["committed_utc"], "MARS preflight committed_utc")
    _require_exact_json_equal(
        committed.get("authorities"),
        _mars_preflight_authorities(),
        "MARS committed authorities",
    )
    root_binding = committed.get("receipt_root")
    parent_binding = committed.get("receipt_parent")
    if not isinstance(root_binding, dict) or set(root_binding) != {
        "path",
        "prepared_identity",
        "committed_identity",
    }:
        raise MaterializationGateError("MARS committed receipt-root binding is invalid")
    if not isinstance(parent_binding, dict) or set(parent_binding) != {"path", "identity"}:
        raise MaterializationGateError("MARS committed receipt-parent binding is invalid")
    _require_exact_json_equal(root_binding["path"], str(root), "MARS receipt-root path")
    _require_exact_json_equal(
        root_binding["committed_identity"],
        directory.root_identity,
        "MARS committed receipt-root inode",
    )
    expected_prepared_root = dict(directory.root_identity)
    expected_prepared_root["mode_octal"] = "0700"
    _require_exact_json_equal(
        root_binding["prepared_identity"],
        expected_prepared_root,
        "MARS prepared receipt-root inode",
    )
    _require_exact_json_equal(
        parent_binding,
        {"path": str(root.parent), "identity": directory.parent_identity},
        "MARS receipt-parent inode",
    )
    artifact_specs = (
        (
            "mars_preflight_prepared",
            MARS_PREFLIGHT_PREPARED_NAME,
            committed.get("prepared_artifacts", {}).get("prepared_receipt"),
        ),
        (
            "mars_preflight_execution_qa_required",
            MARS_PREFLIGHT_QA_NAME,
            committed.get("prepared_artifacts", {}).get("execution_qa_required"),
        ),
        (
            "mars_preflight_prepare_sha_index",
            MARS_PREFLIGHT_PREPARE_INDEX_NAME,
            committed.get("prepared_artifacts", {}).get("prepare_sha256sums"),
        ),
        (
            "mars_preflight_receipt_body",
            MARS_PREFLIGHT_BODY_NAME,
            committed.get("receipt_body"),
        ),
        (
            "mars_preflight_sha_index",
            MARS_PREFLIGHT_INDEX_NAME,
            committed.get("sha256_index"),
        ),
    )
    snapshots: dict[str, _HeldSnapshot] = {
        "mars_preflight_committed": committed_snapshot
    }
    records: dict[str, dict[str, Any]] = {}
    for role, filename, expected_record in artifact_specs:
        if not isinstance(expected_record, dict):
            raise MaterializationGateError(f"MARS committed record missing: {role}")
        snapshot = held.open_at(
            f"bound:{role}",
            directory,
            filename,
            role,
            expected_sha256=expected_record.get("sha256"),
            expected_mode=FILE_MODE,
        )
        record = _preflight_snapshot_record(snapshot, filename)
        _require_exact_json_equal(record, expected_record, f"MARS committed {role} record")
        snapshots[role] = snapshot
        records[role] = record
    prepared = _json_from_bytes(
        snapshots["mars_preflight_prepared"].raw, "MARS prepared receipt"
    )
    if (
        prepared.get("schema") != MARS_PREFLIGHT_PREPARED_SCHEMA
        or prepared.get("status")
        != "PREPARED_AWAITING_INDEPENDENT_EXACT_CODE_GO"
        or prepared.get("required_execute_schema")
        != "controlled_real10k_20k_mars_code_go_v3"
        or prepared.get("required_execute_scope")
        != "MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY"
    ):
        raise MaterializationGateError("MARS prepared receipt schema or gate is invalid")
    _require_exact_json_equal(
        prepared.get("authorities"),
        _mars_preflight_authorities(),
        "MARS prepared authorities",
    )
    _require_exact_json_equal(
        prepared.get("receipt_root"),
        {"path": str(root), "identity": root_binding["prepared_identity"]},
        "MARS prepared receipt-root binding",
    )
    _require_exact_json_equal(
        prepared.get("receipt_parent"), parent_binding, "MARS prepared parent binding"
    )
    qa = _json_from_bytes(
        snapshots["mars_preflight_execution_qa_required"].raw,
        "MARS preflight execution QA requirement",
    )
    if (
        qa.get("schema") != MARS_PREFLIGHT_QA_SCHEMA
        or qa.get("status") != "INDEPENDENT_QA_REQUIRED"
        or qa.get("verdict") != "NO_GO_PENDING_EXACT_CODE_GO_V3"
    ):
        raise MaterializationGateError("MARS execution QA requirement is invalid")
    _require_exact_json_equal(
        qa.get("prepared_receipt"),
        records["mars_preflight_prepared"],
        "MARS QA prepared receipt binding",
    )
    prepare_index_expected = _preflight_index_bytes(
        (
            records["mars_preflight_prepared"],
            records["mars_preflight_execution_qa_required"],
        )
    )
    if snapshots["mars_preflight_prepare_sha_index"].raw != prepare_index_expected:
        raise MaterializationGateError("MARS PREPARE SHA index is not exact")
    body = _json_from_bytes(
        snapshots["mars_preflight_receipt_body"].raw, "MARS preflight body"
    )
    attempt_snapshots = _audit_mars_preflight_body(
        body,
        held=held,
        bindings=bindings,
        runtime=runtime,
        host=host,
        candidate_dir=candidate_dir,
        materialization_out_dir=materialization_out_dir,
        execution_receipt_dir=execution_receipt_dir,
    )
    snapshots.update(attempt_snapshots)
    success_index_expected = _preflight_index_bytes(
        (
            records["mars_preflight_prepared"],
            records["mars_preflight_execution_qa_required"],
            records["mars_preflight_prepare_sha_index"],
            records["mars_preflight_receipt_body"],
        )
    )
    if snapshots["mars_preflight_sha_index"].raw != success_index_expected:
        raise MaterializationGateError("MARS committed SHA index is not exact")
    lease_binding = committed.get("consumed_external_one_use_lease")
    if not isinstance(lease_binding, dict):
        raise MaterializationGateError("MARS consumed external lease binding is missing")
    expected_lease_name = f".{root.name}.controlled_real10k_20k_preflight_once_lease.json"
    if lease_binding.get("path") != str(root.parent / expected_lease_name):
        raise MaterializationGateError("MARS external lease path is not exact sibling")
    lease_snapshot = held.open_at(
        "bound:mars_preflight_consumed_lease",
        directory,
        expected_lease_name,
        "MARS consumed external one-use lease",
        expected_sha256=lease_binding.get("sha256"),
        expected_mode=FILE_MODE,
        in_parent=True,
    )
    try:
        fcntl.flock(lease_snapshot.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as exc:
        raise MaterializationGateError("MARS consumed lease is still exclusively held") from exc
    observed_lease_record = {
        **_preflight_snapshot_record(lease_snapshot, str(root.parent / expected_lease_name)),
        "schema": MARS_PREFLIGHT_LEASE_SCHEMA,
        "state": "CONSUMED",
    }
    _require_exact_json_equal(
        observed_lease_record, lease_binding, "MARS consumed lease live binding"
    )
    lease = _json_from_bytes(lease_snapshot.raw, "MARS consumed external lease")
    if (
        lease.get("schema") != MARS_PREFLIGHT_LEASE_SCHEMA
        or lease.get("state") != "CONSUMED"
        or lease.get("single_use") is not True
        or lease.get("retry_authorized") is not False
    ):
        raise MaterializationGateError("MARS consumed external lease state is invalid")
    _strict_utc(lease.get("created_utc"), "MARS lease created_utc")
    _strict_utc(lease.get("consumed_utc"), "MARS lease consumed_utc")
    _require_exact_json_equal(
        lease.get("receipt_root"), prepared.get("receipt_root"), "MARS lease root binding"
    )
    _require_exact_json_equal(
        lease.get("authorities"),
        _mars_preflight_authorities(),
        "MARS lease authorities",
    )
    initial_lease = prepared.get("external_one_use_lease")
    if not isinstance(initial_lease, dict):
        raise MaterializationGateError("MARS prepared lease binding is missing")
    for key in ("path", "schema"):
        _require_exact_json_equal(
            lease_binding.get(key), initial_lease.get(key), f"MARS lease {key} continuity"
        )
    for key in ("st_dev", "st_ino", "st_uid", "st_gid", "nlink", "size_bytes"):
        _require_exact_json_equal(
            lease_binding["identity"].get(key),
            initial_lease["identity"].get(key),
            f"MARS lease inode continuity {key}",
        )
    _require_exact_json_equal(
        body.get("receipt_transaction", {}).get("consumed_external_one_use_lease"),
        lease_binding,
        "MARS body consumed lease binding",
    )
    _require_exact_json_equal(
        committed.get("external_code_go"),
        {
            "path": body["external_code_go"]["path"],
            "sha256": body["external_code_go"]["sha256"],
            "schema": "controlled_real10k_20k_mars_code_go_v3",
            "scope": "MARS_NATIVE_PREFLIGHT_AND_REVIEWED_TESTS_ONLY",
        },
        "MARS committed external CODE_GO binding",
    )
    singleton = committed.get("process_singleton")
    if not isinstance(singleton, dict) or set(singleton) != {
        "contract",
        "contract_payload",
        "lock",
        "lock_operation",
        "lock_held_for_full_execute_lifetime",
        "protected_entrypoints",
        "proc_audit_contract",
        "before",
        "after",
        "all_counts_zero",
        "current_uid_only",
    }:
        raise MaterializationGateError("committed process-singleton binding is invalid")
    if (
        singleton["lock_operation"] != "LOCK_EX|LOCK_NB"
        or singleton["lock_held_for_full_execute_lifetime"] is not True
        or singleton["all_counts_zero"] is not True
        or singleton["current_uid_only"] is not True
    ):
        raise MaterializationGateError("committed process-singleton proof is invalid")
    if not isinstance(singleton["contract_payload"], dict):
        raise MaterializationGateError("committed singleton contract payload is invalid")
    _require_exact_json_equal(
        singleton["protected_entrypoints"],
        singleton["contract_payload"].get("protected_entrypoints"),
        "committed singleton protected-entrypoint contract",
    )
    _require_exact_json_equal(
        singleton["proc_audit_contract"],
        singleton["contract_payload"].get("proc_audit"),
        "committed singleton exact process-audit contract",
    )
    _require_exact_json_equal(
        body.get("process_singleton"), singleton, "body/commit process-singleton binding"
    )
    contract_record = singleton["contract"]
    lock_record = singleton["lock"]
    if not isinstance(contract_record, dict) or not isinstance(lock_record, dict):
        raise MaterializationGateError("process-singleton file bindings are missing")
    contract_path = _canonical_regular_file(
        contract_record.get("path", ""), "package process-singleton contract"
    )
    lock_path = _canonical_regular_file(
        lock_record.get("path", ""), "package process-singleton lock"
    )
    contract_snapshot = held.open(
        "bound:package_process_singleton_contract",
        contract_path,
        "package process-singleton contract",
        expected_sha256=contract_record.get("sha256"),
        expected_mode=FILE_MODE,
    )
    lock_snapshot = held.open(
        "bound:package_singleton_lock",
        lock_path,
        "package process-singleton lock",
        expected_sha256=lock_record.get("sha256"),
        expected_mode=FILE_MODE,
    )
    _require_exact_json_equal(
        _preflight_snapshot_record(contract_snapshot, str(contract_path)),
        contract_record,
        "live process-singleton contract binding",
    )
    _require_exact_json_equal(
        _preflight_snapshot_record(lock_snapshot, str(lock_path)),
        lock_record,
        "live package singleton-lock binding",
    )
    contract_payload = _json_from_bytes(
        contract_snapshot.raw, "package process-singleton contract"
    )
    _require_exact_json_equal(
        contract_payload,
        singleton["contract_payload"],
        "live process-singleton contract payload",
    )
    if (
        contract_payload.get("schema")
        != "controlled_real10k_20k_process_singleton_contract_v1"
        or contract_payload.get("lock", {}).get("relative_path")
        != "CONTROLLED_SINGLETON.lock"
        or contract_payload.get("lock", {}).get("operation") != "LOCK_EX|LOCK_NB"
        or contract_payload.get("proc_audit", {}).get("substring_matching_allowed")
        is not False
        or contract_payload.get("lifetime", {}).get("full_lifetime_required") is not True
    ):
        raise MaterializationGateError("package process-singleton contract semantics are invalid")
    try:
        fcntl.flock(lock_snapshot.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise MaterializationGateError("package singleton lock is already held") from exc
    snapshots["package_process_singleton_contract"] = contract_snapshot
    snapshots["package_singleton_lock"] = lock_snapshot
    snapshots["mars_preflight_consumed_lease"] = lease_snapshot
    directory.assert_continuity()
    held.assert_continuity()
    return snapshots


def _write_bytes_exclusive(path: Path, payload: bytes, *, mode: int = FILE_MODE) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(mode)


def _write_json_exclusive(path: Path, value: Any) -> None:
    _write_bytes_exclusive(path, _json_bytes(value))


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _strict_json_loads(raw: str, label: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MaterializationGateError(
                    f"{label} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise MaterializationGateError(
            f"{label} contains non-finite JSON constant {value}"
        )

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except MaterializationGateError:
        raise
    except json.JSONDecodeError as exc:
        raise MaterializationGateError(f"cannot parse {label}: {exc}") from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeError) as exc:
        raise MaterializationGateError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise MaterializationGateError(f"{label} must be a JSON object")
    return value


def _json_from_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(raw.decode("utf-8"), label)
    except UnicodeError as exc:
        raise MaterializationGateError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise MaterializationGateError(f"{label} must be a JSON object")
    return value


def _require_exact_json_equal(actual: Any, expected: Any, label: str) -> None:
    """Require recursive JSON equality without Python bool/int coercion."""

    def compare(left: Any, right: Any, location: str) -> None:
        if type(left) is not type(right):
            raise MaterializationGateError(
                f"{label} exact JSON type mismatch at {location}: "
                f"{type(left).__name__} != {type(right).__name__}"
            )
        if isinstance(right, dict):
            if any(type(key) is not str for key in left) or any(
                type(key) is not str for key in right
            ):
                raise MaterializationGateError(
                    f"{label} has a non-string JSON key at {location}"
                )
            if set(left) != set(right):
                raise MaterializationGateError(
                    f"{label} exact JSON keyset mismatch at {location}"
                )
            for key in sorted(right):
                compare(left[key], right[key], f"{location}.{key}")
            return
        if isinstance(right, list):
            if len(left) != len(right):
                raise MaterializationGateError(
                    f"{label} exact JSON length mismatch at {location}"
                )
            for index, (left_value, right_value) in enumerate(zip(left, right)):
                compare(left_value, right_value, f"{location}[{index}]")
            return
        if left != right:
            raise MaterializationGateError(
                f"{label} exact JSON value mismatch at {location}"
            )

    compare(actual, expected, "$")


def _materialization_contract(
    bindings: Mapping[str, Mapping[str, Any]], materialization_out_dir: Path
) -> dict[str, Any]:
    return {
        "selection_seed": SELECTION_SEED,
        "paired_seeds": list(PAIRED_SEEDS),
        "physical_cell_bins": PHYSICAL_CELL_BINS,
        "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
        "input_columns": list(INPUT_COLUMNS),
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "output_columns": list(OUTPUT_COLUMNS),
        "input_lower": list(INPUT_LOWER),
        "input_upper": list(INPUT_UPPER),
        "geometry_lower": list(GEOMETRY_LOWER),
        "geometry_upper": list(GEOMETRY_UPPER),
        "counts": dict(COUNTS),
        "builder_argv": [
            "--historical-10k-csv",
            bindings["historical_10k_csv"]["path"],
            "--historical-10k-sha256",
            bindings["historical_10k_csv"]["sha256"],
            "--authoritative-100k-csv",
            bindings["authoritative_100k_csv"]["path"],
            "--authoritative-100k-sha256",
            bindings["authoritative_100k_csv"]["sha256"],
            "--historical-model-summary-json",
            bindings["historical_model_summary_json"]["path"],
            "--historical-model-summary-sha256",
            bindings["historical_model_summary_json"]["sha256"],
            "--out-dir",
            str(materialization_out_dir),
            "--extra-count",
            str(COUNTS["extra"]),
            "--selection-seed",
            str(SELECTION_SEED),
            "--expected-historical-rows",
            str(COUNTS["historical_source"]),
            "--expected-authoritative-rows",
            str(COUNTS["authoritative_source"]),
            "--expected-train-rows",
            str(COUNTS["small_gradient_train"]),
            "--expected-validation-rows",
            str(COUNTS["validation"]),
            "--expected-test-rows",
            str(COUNTS["test"]),
        ],
        "builder_invocation": "IN_PROCESS_MAIN_ONLY",
        "new_emx_generation": False,
        "fixed10k_regeneration": False,
        "training": False,
        "evaluation": False,
        "numerical_metric_access": False,
    }


def _challenge_core(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": manifest["schema"],
        "candidate_dir": manifest["candidate_dir"],
        "bindings": manifest["bindings"],
        "materialization_contract": manifest["materialization_contract"],
        "materialization_contract_sha256": manifest["materialization_contract_sha256"],
        "runtime_identity": manifest["runtime_identity"],
        "host_identity": manifest["host_identity"],
        "sealed_runtime": manifest["sealed_runtime"],
        "future_paths": manifest["future_paths"],
        "authorities": manifest["authorities"],
    }


def _expected_go_bindings(
    manifest: Mapping[str, Any], manifest_sha: str, index_sha: str
) -> dict[str, Any]:
    return {
        "candidate_manifest_sha256": manifest_sha,
        "candidate_sha256sums_sha256": index_sha,
        "challenge_nonce": manifest["challenge_nonce"],
        "artifact_sha256": {
            role: manifest["bindings"][role]["sha256"] for role in BOUND_ROLE_ORDER
        },
        "materialization_out_dir": manifest["future_paths"]["materialization_out_dir"],
        "execution_receipt_dir": manifest["future_paths"]["execution_receipt_dir"],
        "runtime_identity_sha256": manifest["runtime_identity"]["identity_sha256"],
        "host_identity_sha256": manifest["host_identity"]["identity_sha256"],
        "materialization_contract_sha256": manifest["materialization_contract_sha256"],
        "sealed_runtime": manifest["sealed_runtime"],
    }


def _prepare(args: argparse.Namespace) -> dict[str, Path]:
    sealed_runtime_attestation = _require_sealed_runtime(
        args.expected_runtime_closure_json_sha256
    )
    candidate = _canonical_future_dir(args.candidate_dir, "--candidate-dir")
    material_out = _canonical_future_dir(
        args.materialization_out_dir, "--materialization-out-dir"
    )
    receipt_dir = _canonical_future_dir(
        args.execution_receipt_dir, "--execution-receipt-dir"
    )
    _require_disjoint_dirs(
        {
            "candidate_dir": candidate,
            "materialization_out_dir": material_out,
            "execution_receipt_dir": receipt_dir,
        }
    )

    supplied = {
        "materialization_builder_code": (args.builder_script, args.builder_sha256),
        "shared_contract_code": (args.shared_contract, args.shared_contract_sha256),
        "splitter_code": (args.splitter_source, args.splitter_sha256),
        "preregistration_v1": (args.prereg_v1, args.prereg_v1_sha256),
        "preregistration_addendum_v1_1": (
            args.prereg_addendum_v1_1,
            args.prereg_addendum_v1_1_sha256,
        ),
        "preregistration_addendum_v1_2": (
            args.prereg_addendum_v1_2,
            args.prereg_addendum_v1_2_sha256,
        ),
        "historical_10k_csv": (args.historical_10k_csv, args.historical_10k_sha256),
        "authoritative_100k_csv": (
            args.authoritative_100k_csv,
            args.authoritative_100k_sha256,
        ),
        "historical_model_summary_json": (
            args.historical_model_summary_json,
            args.historical_model_summary_sha256,
        ),
    }
    frozen_expected = {
        "preregistration_v1": FROZEN_PREREG_V1_SHA256,
        "preregistration_addendum_v1_1": FROZEN_PREREG_ADDENDUM_V1_1_SHA256,
        "preregistration_addendum_v1_2": FROZEN_PREREG_ADDENDUM_V1_2_SHA256,
        "historical_10k_csv": FROZEN_HISTORICAL_10K_SHA256,
        "authoritative_100k_csv": FROZEN_AUTHORITATIVE_100K_SHA256,
        "historical_model_summary_json": FROZEN_HISTORICAL_SUMMARY_SHA256,
    }
    for role, frozen_sha in frozen_expected.items():
        if _normalized_sha(supplied[role][1], f"{role} SHA-256") != frozen_sha:
            raise MaterializationGateError(f"{role} is not the frozen production identity")

    wrapper = _canonical_regular_file(Path(__file__).resolve(), "materialization wrapper")
    prepare_held = _HeldClosure()
    try:
        wrapper_snapshot = prepare_held.open(
            "bound:wrapper_code", wrapper, "wrapper_code"
        )
        bindings: dict[str, dict[str, Any]] = {
            "wrapper_code": _binding_record("wrapper_code", wrapper_snapshot)
        }
        for role in BOUND_ROLE_ORDER[1:]:
            if role.startswith("mars_preflight_") or role in {
                "package_build_attempt_body",
                "package_build_attempt_committed",
                "package_process_singleton_contract",
                "package_singleton_lock",
            }:
                continue
            raw_path, expected_sha = supplied[role]
            path = _canonical_regular_file(raw_path, role)
            snapshot = prepare_held.open(
                f"bound:{role}",
                path,
                role,
                expected_sha256=expected_sha,
            )
            bindings[role] = _binding_record(role, snapshot)

        runtime = _runtime_identity(args.python_executable, args.python_executable_sha256)
        host = _host_identity()
        if (
            runtime["python_version"] != EXPECTED_PRODUCTION_PYTHON_VERSION
            or runtime["numpy_version"] != EXPECTED_PRODUCTION_NUMPY_VERSION
        ):
            raise MaterializationGateError(
                "PREPARE requires exact production Python 3.12.13 and NumPy 2.5.0"
            )
        if args.expected_hostname is not None and host["hostname"] != args.expected_hostname:
            raise MaterializationGateError(
                f"hostname mismatch: expected={args.expected_hostname} actual={host['hostname']}"
            )
        if args.expected_uid is not None and host["uid"] != int(args.expected_uid):
            raise MaterializationGateError(
                f"UID mismatch: expected={args.expected_uid} actual={host['uid']}"
            )
        if (
            args.expected_python_version is not None
            and runtime["python_version"] != args.expected_python_version
        ):
            raise MaterializationGateError(
                "Python version mismatch: "
                f"expected={args.expected_python_version} actual={runtime['python_version']}"
            )
        preflight_snapshots = _audit_committed_preflight_closure(
            args.mars_preflight_root,
            args.mars_preflight_committed_sha256,
            held=prepare_held,
            bindings=bindings,
            runtime=runtime,
            host=host,
            candidate_dir=candidate,
            materialization_out_dir=material_out,
            execution_receipt_dir=receipt_dir,
        )
        preflight_body = _json_from_bytes(
            preflight_snapshots["mars_preflight_receipt_body"].raw,
            "MARS preflight body runtime-closure binding",
        )
        preflight_active_runtime = preflight_body["runtime_identity"][
            "active_runtime"
        ]
        expected_materialization_runtime = dict(preflight_active_runtime)
        expected_materialization_runtime["entrypoint"] = "materialization"
        _require_exact_json_equal(
            sealed_runtime_attestation,
            expected_materialization_runtime,
            "preflight/materialization active descriptor-runtime identity",
        )
        observed_runtime_closure_sha = (
            preflight_body.get("package", {})
            .get("role_identity", {})
            .get("runtime_dependency_closure_json", {})
            .get("sha256")
        )
        _require_exact_json_equal(
            observed_runtime_closure_sha,
            _normalized_sha(
                args.expected_runtime_closure_json_sha256,
                "expected sealed runtime-closure JSON SHA-256",
            ),
            "preflight/sealed runtime-closure identity",
        )
        package_role_identity = preflight_body["package"]["role_identity"]
        runtime_manifest_role_identity = package_role_identity[
            "runtime_dependency_closure_json"
        ]
        runtime_tree_role_identity = package_role_identity[
            "runtime_dependency_closure_tree"
        ]
        for role in BOUND_ROLE_ORDER:
            if not (
                role.startswith("mars_preflight_")
                or role
                in {
                    "package_build_attempt_body",
                    "package_build_attempt_committed",
                    "package_process_singleton_contract",
                    "package_singleton_lock",
                }
            ):
                continue
            bindings[role] = _binding_record(role, preflight_snapshots[role])
        prepare_held.assert_continuity()
    finally:
        prepare_held.close()

    contract = _materialization_contract(bindings, material_out)
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_utc": _utc_now(),
        "status": "PREPARED_IMMUTABLE_RESULT_BLIND_NO_AUTHORITY",
        "result_blind": True,
        "candidate_dir": str(candidate),
        "challenge_nonce": "",
        "bindings": bindings,
        "bound_role_order": list(BOUND_ROLE_ORDER),
        "materialization_contract": contract,
        "materialization_contract_sha256": _canonical_json_sha(contract),
        "runtime_identity": runtime,
        "host_identity": host,
        "sealed_runtime": {
            "expected_runtime_closure_json_sha256": _normalized_sha(
                args.expected_runtime_closure_json_sha256,
                "expected sealed runtime-closure JSON SHA-256",
            ),
            "attestation": sealed_runtime_attestation,
            "runtime_manifest_role_identity": runtime_manifest_role_identity,
            "runtime_tree_role_identity": runtime_tree_role_identity,
            "required_external_entrypoint": "materialization",
            "raw_runtime_fallback_authorized": False,
        },
        "host_constraints_asserted": {
            "hostname": args.expected_hostname,
            "uid": args.expected_uid,
            "python_version": args.expected_python_version,
        },
        "future_paths": {
            "materialization_out_dir": str(material_out),
            "execution_receipt_dir": str(receipt_dir),
        },
        "authorities": dict(CANDIDATE_AUTHORITIES),
        "result_or_row_access": {
            "csv_rows_read": False,
            "model_summary_json_parsed": False,
            "numerical_model_results_accessed": False,
            "scientific_source_files_sha256_and_stat_only": True,
            "protocol_and_provenance_json_parsed": True,
            "descriptor_sealed_runtime_imports_executed": True,
        },
        "next_legal_gate": GO_SCHEMA,
    }
    manifest["challenge_nonce"] = _canonical_json_sha(_challenge_core(manifest))[:32]
    if not NONCE_RE.fullmatch(manifest["challenge_nonce"]):
        raise AssertionError("deterministic challenge nonce construction failed")

    candidate.mkdir(mode=WORKING_DIRECTORY_MODE)
    manifest_path = candidate / MANIFEST_NAME
    qa_path = candidate / QA_REQUIRED_NAME
    prepared_path = candidate / PREPARED_RECEIPT_NAME
    index_path = candidate / SHA_INDEX_NAME
    _write_json_exclusive(manifest_path, manifest)
    manifest_sha = _sha256(manifest_path)
    qa_required = {
        "schema": QA_REQUIRED_SCHEMA,
        "status": "INDEPENDENT_QA_REQUIRED",
        "verdict": "NO_GO_PENDING_EXTERNAL_EXACT_GO",
        "challenge_nonce": manifest["challenge_nonce"],
        "manifest_sha256": manifest_sha,
        "required_go_schema": GO_SCHEMA,
        "required_go_scope": GO_SCOPE,
        "required_top_level_keys": sorted(GO_TOP_LEVEL_KEYS),
        "required_reviewer_keys": sorted(GO_REVIEWER_KEYS),
        "required_finding_keys": sorted(GO_FINDING_KEYS),
        "required_binding_keys": sorted(GO_BINDING_KEYS),
        "required_zero_findings": {"p0": 0, "p1": 0},
        "required_authorities": dict(GO_AUTHORITIES),
        "freshness": {
            "strict_utc": True,
            "issued_lte_now_lt_expires": True,
            "maximum_lifetime_seconds": 86400,
        },
        "single_use_bindings": {
            "materialization_out_dir": str(material_out),
            "execution_receipt_dir": str(receipt_dir),
        },
        "authorities_before_exact_go": dict(CANDIDATE_AUTHORITIES),
        "next_legal_action": "EXTERNAL_RESULT_BLIND_INDEPENDENT_QA_ONLY",
    }
    _write_json_exclusive(qa_path, qa_required)
    prepared = {
        "schema": PREPARED_RECEIPT_SCHEMA,
        "status": "PASS_PREPARED_AWAITING_EXTERNAL_EXACT_GO",
        "manifest": {"path": MANIFEST_NAME, "sha256": manifest_sha},
        "independent_qa_required": {
            "path": QA_REQUIRED_NAME,
            "sha256": _sha256(qa_path),
        },
        "challenge_nonce": manifest["challenge_nonce"],
        "bound_artifact_sha256": {
            role: bindings[role]["sha256"] for role in BOUND_ROLE_ORDER
        },
        "checks": {
            "production_source_identities_exact": True,
            "preregistration_v1_and_addenda_exact": True,
            "fixed_counts_seed_columns_and_bounds": True,
            "canonical_future_paths_absent_and_disjoint": True,
            "runtime_and_host_identity_bound": True,
            "csv_rows_not_read": True,
            "model_results_not_accessed": True,
            "no_training_evaluation_emx_signal_or_subprocess": True,
        },
        "authorities": dict(CANDIDATE_AUTHORITIES),
        "sha256_index": {
            "path": SHA_INDEX_NAME,
            "self_hash_included": False,
            "exact_entry_order": [MANIFEST_NAME, QA_REQUIRED_NAME, PREPARED_RECEIPT_NAME],
        },
    }
    _write_json_exclusive(prepared_path, prepared)
    lines = "".join(
        f"{_sha256(path)}  {path.name}\n"
        for path in (manifest_path, qa_path, prepared_path)
    )
    _write_bytes_exclusive(index_path, lines.encode("ascii"))
    _fsync_dir(candidate)
    candidate.chmod(FROZEN_DIRECTORY_MODE)
    _audit_candidate_closure(
        candidate,
        expected_manifest_sha=manifest_sha,
        expected_index_sha=_sha256(index_path),
    )
    _fsync_dir(candidate.parent)
    return {
        "candidate_dir": candidate,
        "manifest": manifest_path,
        "independent_qa_required": qa_path,
        "prepared_receipt": prepared_path,
        "sha_index": index_path,
    }


def _parse_sha_index(
    path: Path,
    root: Path,
    *,
    required_mode: int | None = None,
    index_snapshot: _HeldSnapshot | None = None,
    held: _HeldClosure | None = None,
) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    try:
        text = (
            index_snapshot.raw.decode("ascii")
            if index_snapshot is not None
            else path.read_text(encoding="ascii")
        )
    except (OSError, UnicodeError) as exc:
        raise MaterializationGateError(f"cannot read SHA index: {exc}") from exc
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.count("  ") != 1:
            raise MaterializationGateError(f"SHA index line {line_number} is malformed")
        digest, name = line.split("  ", 1)
        digest = _normalized_sha(digest, f"SHA index line {line_number}")
        if name in seen_paths or "/" in name or name in {"", ".", ".."}:
            raise MaterializationGateError(f"SHA index path is invalid or duplicate: {name!r}")
        artifact = _canonical_regular_file(root / name, f"indexed artifact {name}")
        if held is None:
            if required_mode is not None and stat.S_IMODE(artifact.lstat().st_mode) != required_mode:
                raise MaterializationGateError(
                    f"indexed artifact mode is not {required_mode:04o}: {name}"
                )
            if _sha256(artifact) != digest:
                raise MaterializationGateError(f"indexed artifact SHA mismatch: {name}")
        else:
            held.open(
                f"candidate:{name}",
                artifact,
                f"indexed artifact {name}",
                expected_sha256=digest,
                expected_mode=required_mode,
            )
        records.append((name, digest))
        seen_paths.add(name)
    return records


def _audit_bound_records(
    manifest: Mapping[str, Any], *, held: _HeldClosure | None = None
) -> dict[str, dict[str, Any]]:
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != set(BOUND_ROLE_ORDER):
        raise MaterializationGateError("manifest bound role set is not exact")
    if manifest.get("bound_role_order") != list(BOUND_ROLE_ORDER):
        raise MaterializationGateError("manifest bound role order is not exact")
    result: dict[str, dict[str, Any]] = {}
    expected_record_keys = {
        "role",
        "path",
        "sha256",
        "size_bytes",
        "mode_octal",
        "nlink",
        "st_dev",
        "st_ino",
    }
    for role in BOUND_ROLE_ORDER:
        record = bindings[role]
        if not isinstance(record, dict) or set(record) != expected_record_keys:
            raise MaterializationGateError(f"bound record keyset is invalid for {role}")
        if (
            type(record.get("role")) is not str
            or record.get("role") != role
            or type(record.get("path")) is not str
            or type(record.get("sha256")) is not str
            or type(record.get("mode_octal")) is not str
            or any(
                type(record.get(key)) is not int
                for key in ("size_bytes", "nlink", "st_dev", "st_ino")
            )
        ):
            raise MaterializationGateError(f"bound role self-label mismatch for {role}")
        path = _canonical_regular_file(record.get("path", ""), f"bound role {role}")
        if held is None:
            metadata = path.lstat()
            actual_sha = _sha256(path)
            if (
                actual_sha != _normalized_sha(record.get("sha256"), f"{role} SHA-256")
                or metadata.st_size != record.get("size_bytes")
                or f"{stat.S_IMODE(metadata.st_mode):04o}" != record.get("mode_octal")
                or metadata.st_nlink != record.get("nlink")
                or metadata.st_dev != record.get("st_dev")
                or metadata.st_ino != record.get("st_ino")
            ):
                raise MaterializationGateError(f"bound role identity drifted: {role}")
        else:
            held.open(
                f"bound:{role}",
                path,
                f"bound role {role}",
                expected_sha256=str(record.get("sha256")),
                expected_record=record,
            )
        result[role] = dict(record)
    return result


def _assert_exact_static_contract(contract: Any, bindings: Mapping[str, Mapping[str, Any]]) -> None:
    if not isinstance(contract, dict):
        raise MaterializationGateError("materialization contract must be an object")
    argv = contract.get("builder_argv")
    if not isinstance(argv, list) or argv.count("--out-dir") != 1:
        raise MaterializationGateError("materialization builder argv is invalid")
    out_index = argv.index("--out-dir") + 1
    if out_index >= len(argv):
        raise MaterializationGateError("materialization builder argv lacks an out-dir value")
    out_dir = _absolute_path(str(argv[out_index]), "frozen out dir")
    expected = _materialization_contract(bindings, out_dir)
    _require_exact_json_equal(
        contract, expected, "materialization exact production contract"
    )


def _audit_candidate_closure(
    candidate: Path,
    *,
    expected_manifest_sha: str,
    expected_index_sha: str,
    require_future_absent: bool = True,
    held: _HeldClosure | None = None,
) -> dict[str, Any]:
    owned = held is None
    closure = held or _HeldClosure()
    try:
        return _audit_candidate_closure_held(
            candidate,
            expected_manifest_sha=expected_manifest_sha,
            expected_index_sha=expected_index_sha,
            require_future_absent=require_future_absent,
            held=closure,
        )
    finally:
        if owned:
            closure.close()


def _audit_candidate_closure_held(
    candidate: Path,
    *,
    expected_manifest_sha: str,
    expected_index_sha: str,
    require_future_absent: bool,
    held: _HeldClosure,
) -> dict[str, Any]:
    root = _canonical_existing_dir(candidate, "candidate directory")
    if stat.S_IMODE(root.lstat().st_mode) != FROZEN_DIRECTORY_MODE:
        raise MaterializationGateError("candidate directory mode must be 0555")
    observed = {path.name for path in root.iterdir()}
    exact = {MANIFEST_NAME, QA_REQUIRED_NAME, PREPARED_RECEIPT_NAME, SHA_INDEX_NAME}
    if observed != exact:
        raise MaterializationGateError(
            f"candidate closure mismatch: missing={sorted(exact-observed)} extra={sorted(observed-exact)}"
        )
    manifest_path = root / MANIFEST_NAME
    qa_path = root / QA_REQUIRED_NAME
    prepared_path = root / PREPARED_RECEIPT_NAME
    index_path = root / SHA_INDEX_NAME
    manifest_snapshot = held.open(
        f"candidate:{MANIFEST_NAME}",
        manifest_path,
        "candidate manifest",
        expected_sha256=expected_manifest_sha,
        expected_mode=FILE_MODE,
    )
    index_snapshot = held.open(
        f"candidate:{SHA_INDEX_NAME}",
        index_path,
        "candidate SHA index",
        expected_sha256=expected_index_sha,
        expected_mode=FILE_MODE,
    )
    manifest_sha = manifest_snapshot.sha256
    index_sha = index_snapshot.sha256
    index = _parse_sha_index(
        index_path,
        root,
        required_mode=FILE_MODE,
        index_snapshot=index_snapshot,
        held=held,
    )
    if [name for name, _ in index] != [MANIFEST_NAME, QA_REQUIRED_NAME, PREPARED_RECEIPT_NAME]:
        raise MaterializationGateError("candidate SHA index entry order is not exact")
    manifest = _json_from_bytes(manifest_snapshot.raw, "candidate manifest")
    exact_manifest_keys = {
        "schema",
        "generated_utc",
        "status",
        "result_blind",
        "candidate_dir",
        "challenge_nonce",
        "bindings",
        "bound_role_order",
        "materialization_contract",
        "materialization_contract_sha256",
        "runtime_identity",
        "host_identity",
        "sealed_runtime",
        "host_constraints_asserted",
        "future_paths",
        "authorities",
        "result_or_row_access",
        "next_legal_gate",
    }
    if set(manifest) != exact_manifest_keys:
        raise MaterializationGateError("candidate manifest top-level keyset is not exact")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("status") != "PREPARED_IMMUTABLE_RESULT_BLIND_NO_AUTHORITY"
        or manifest.get("result_blind") is not True
        or manifest.get("candidate_dir") != str(root)
        or manifest.get("authorities") != CANDIDATE_AUTHORITIES
        or manifest.get("next_legal_gate") != GO_SCHEMA
    ):
        raise MaterializationGateError("candidate manifest status or boundary is invalid")
    access = manifest.get("result_or_row_access")
    if access != {
        "csv_rows_read": False,
        "model_summary_json_parsed": False,
        "numerical_model_results_accessed": False,
        "scientific_source_files_sha256_and_stat_only": True,
        "protocol_and_provenance_json_parsed": True,
        "descriptor_sealed_runtime_imports_executed": True,
    }:
        raise MaterializationGateError("candidate result-blind access declaration is invalid")
    bindings = _audit_bound_records(manifest, held=held)
    _assert_exact_static_contract(manifest["materialization_contract"], bindings)
    if manifest.get("materialization_contract_sha256") != _canonical_json_sha(
        manifest["materialization_contract"]
    ):
        raise MaterializationGateError("materialization contract SHA binding is invalid")
    nonce = str(manifest.get("challenge_nonce"))
    if not NONCE_RE.fullmatch(nonce) or nonce != _canonical_json_sha(
        _challenge_core(manifest)
    )[:32]:
        raise MaterializationGateError("candidate deterministic challenge nonce is invalid")
    expected_frozen = {
        "historical_10k_csv": FROZEN_HISTORICAL_10K_SHA256,
        "authoritative_100k_csv": FROZEN_AUTHORITATIVE_100K_SHA256,
        "historical_model_summary_json": FROZEN_HISTORICAL_SUMMARY_SHA256,
        "preregistration_v1": FROZEN_PREREG_V1_SHA256,
        "preregistration_addendum_v1_1": FROZEN_PREREG_ADDENDUM_V1_1_SHA256,
        "preregistration_addendum_v1_2": FROZEN_PREREG_ADDENDUM_V1_2_SHA256,
    }
    for role, digest in expected_frozen.items():
        if bindings[role]["sha256"] != digest:
            raise MaterializationGateError(f"candidate frozen identity mismatch: {role}")
    runtime = manifest.get("runtime_identity")
    host = manifest.get("host_identity")
    if not isinstance(runtime, dict) or not isinstance(host, dict):
        raise MaterializationGateError("runtime or host identity is invalid")
    runtime_core = dict(runtime)
    runtime_digest = runtime_core.pop("identity_sha256", None)
    if runtime_digest != _canonical_json_sha(runtime_core):
        raise MaterializationGateError("runtime identity digest is invalid")
    host_core = dict(host)
    host_digest = host_core.pop("identity_sha256", None)
    if host_digest != _canonical_json_sha(host_core):
        raise MaterializationGateError("host identity digest is invalid")
    sealed_runtime = manifest.get("sealed_runtime")
    if not isinstance(sealed_runtime, dict) or set(sealed_runtime) != {
        "expected_runtime_closure_json_sha256",
        "attestation",
        "runtime_manifest_role_identity",
        "runtime_tree_role_identity",
        "required_external_entrypoint",
        "raw_runtime_fallback_authorized",
    }:
        raise MaterializationGateError("candidate sealed-runtime binding is invalid")
    if (
        sealed_runtime["required_external_entrypoint"] != "materialization"
        or sealed_runtime["raw_runtime_fallback_authorized"] is not False
    ):
        raise MaterializationGateError("candidate permits an invalid runtime launch path")
    expected_runtime_closure_sha = _normalized_sha(
        sealed_runtime["expected_runtime_closure_json_sha256"],
        "candidate sealed runtime-closure SHA-256",
    )
    runtime_manifest_role_identity = sealed_runtime[
        "runtime_manifest_role_identity"
    ]
    runtime_tree_role_identity = sealed_runtime["runtime_tree_role_identity"]
    if (
        type(runtime_manifest_role_identity) is not dict
        or set(runtime_manifest_role_identity) != {"kind", "path", "sha256"}
        or runtime_manifest_role_identity["kind"] != "file"
        or runtime_manifest_role_identity["sha256"] != expected_runtime_closure_sha
        or type(runtime_tree_role_identity) is not dict
        or set(runtime_tree_role_identity) != {"kind", "path", "sha256"}
        or runtime_tree_role_identity["kind"] != "tree"
    ):
        raise MaterializationGateError(
            "candidate sealed-runtime manifest/tree role identities are invalid"
        )
    _normalized_sha(
        runtime_tree_role_identity["sha256"],
        "candidate sealed runtime-tree SHA-256",
    )
    expected_attestation = {
        "schema": "controlled_real10k_20k_runtime_attestation_v1",
        "entrypoint": "materialization",
        "manifest_sha256": expected_runtime_closure_sha,
        "pure_archive_sha256": sealed_runtime["attestation"].get("pure_archive_sha256")
        if isinstance(sealed_runtime["attestation"], dict)
        else None,
        "bootstrap_sha256": sealed_runtime["attestation"].get("bootstrap_sha256")
        if isinstance(sealed_runtime["attestation"], dict)
        else None,
    }
    _require_exact_json_equal(
        sealed_runtime["attestation"],
        expected_attestation,
        "candidate sealed runtime attestation",
    )
    for key in ("pure_archive_sha256", "bootstrap_sha256"):
        _normalized_sha(expected_attestation[key], f"candidate runtime attestation {key}")
    future = manifest.get("future_paths")
    if not isinstance(future, dict) or set(future) != {
        "materialization_out_dir",
        "execution_receipt_dir",
    }:
        raise MaterializationGateError("future path binding is invalid")
    if require_future_absent:
        material_out = _canonical_future_dir(
            future["materialization_out_dir"], "material output"
        )
        receipt_dir = _canonical_future_dir(
            future["execution_receipt_dir"], "execution receipt"
        )
    else:
        material_out = _absolute_path(future["materialization_out_dir"], "material output")
        receipt_dir = _absolute_path(future["execution_receipt_dir"], "execution receipt")
        for label, path in (("material output", material_out), ("execution receipt", receipt_dir)):
            _reject_symlink_chain(path, label)
            if not path.parent.is_dir() or path.parent.resolve(strict=True) != path.parent:
                raise MaterializationGateError(f"{label} parent is no longer canonical")
    _require_disjoint_dirs(
        {"candidate": root, "material_output": material_out, "execution_receipt": receipt_dir}
    )
    committed_path = Path(bindings["mars_preflight_committed"]["path"])
    preflight_snapshots = _audit_committed_preflight_closure(
        committed_path.parent,
        bindings["mars_preflight_committed"]["sha256"],
        held=held,
        bindings=bindings,
        runtime=runtime,
        host=host,
        candidate_dir=root,
        materialization_out_dir=material_out,
        execution_receipt_dir=receipt_dir,
    )
    for role, snapshot in preflight_snapshots.items():
        _require_exact_json_equal(
            _binding_record(role, snapshot),
            bindings[role],
            f"candidate committed-preflight bound role {role}",
        )

    qa = _json_from_bytes(
        held.entries[f"candidate:{QA_REQUIRED_NAME}"].raw,
        "candidate QA-required",
    )
    if set(qa) != {
        "schema",
        "status",
        "verdict",
        "challenge_nonce",
        "manifest_sha256",
        "required_go_schema",
        "required_go_scope",
        "required_top_level_keys",
        "required_reviewer_keys",
        "required_finding_keys",
        "required_binding_keys",
        "required_zero_findings",
        "required_authorities",
        "freshness",
        "single_use_bindings",
        "authorities_before_exact_go",
        "next_legal_action",
    }:
        raise MaterializationGateError("QA-required top-level keyset is not exact")
    if (
        qa.get("schema") != QA_REQUIRED_SCHEMA
        or qa.get("status") != "INDEPENDENT_QA_REQUIRED"
        or qa.get("verdict") != "NO_GO_PENDING_EXTERNAL_EXACT_GO"
        or qa.get("challenge_nonce") != nonce
        or qa.get("manifest_sha256") != manifest_sha
        or qa.get("required_go_schema") != GO_SCHEMA
        or qa.get("required_go_scope") != GO_SCOPE
        or qa.get("required_top_level_keys") != sorted(GO_TOP_LEVEL_KEYS)
        or qa.get("required_reviewer_keys") != sorted(GO_REVIEWER_KEYS)
        or qa.get("required_finding_keys") != sorted(GO_FINDING_KEYS)
        or qa.get("required_binding_keys") != sorted(GO_BINDING_KEYS)
        or qa.get("required_zero_findings") != {"p0": 0, "p1": 0}
        or qa.get("required_authorities") != GO_AUTHORITIES
        or qa.get("authorities_before_exact_go") != CANDIDATE_AUTHORITIES
        or qa.get("freshness")
        != {
            "strict_utc": True,
            "issued_lte_now_lt_expires": True,
            "maximum_lifetime_seconds": 86400,
        }
        or qa.get("single_use_bindings")
        != {
            "materialization_out_dir": str(material_out),
            "execution_receipt_dir": str(receipt_dir),
        }
        or qa.get("next_legal_action") != "EXTERNAL_RESULT_BLIND_INDEPENDENT_QA_ONLY"
    ):
        raise MaterializationGateError("QA-required exact GO contract is invalid")
    prepared = _json_from_bytes(
        held.entries[f"candidate:{PREPARED_RECEIPT_NAME}"].raw,
        "candidate prepared receipt",
    )
    exact_prepared_keys = {
        "schema",
        "status",
        "manifest",
        "independent_qa_required",
        "challenge_nonce",
        "bound_artifact_sha256",
        "checks",
        "authorities",
        "sha256_index",
    }
    exact_prepared_checks = {
        "production_source_identities_exact": True,
        "preregistration_v1_and_addenda_exact": True,
        "fixed_counts_seed_columns_and_bounds": True,
        "canonical_future_paths_absent_and_disjoint": True,
        "runtime_and_host_identity_bound": True,
        "csv_rows_not_read": True,
        "model_results_not_accessed": True,
        "no_training_evaluation_emx_signal_or_subprocess": True,
    }
    if (
        set(prepared) != exact_prepared_keys
        or prepared.get("schema") != PREPARED_RECEIPT_SCHEMA
        or prepared.get("status") != "PASS_PREPARED_AWAITING_EXTERNAL_EXACT_GO"
        or prepared.get("manifest")
        != {"path": MANIFEST_NAME, "sha256": manifest_sha}
        or prepared.get("independent_qa_required")
        != {
            "path": QA_REQUIRED_NAME,
            "sha256": held.entries[f"candidate:{QA_REQUIRED_NAME}"].sha256,
        }
        or prepared.get("challenge_nonce") != nonce
        or prepared.get("bound_artifact_sha256")
        != {role: bindings[role]["sha256"] for role in BOUND_ROLE_ORDER}
        or prepared.get("checks") != exact_prepared_checks
        or prepared.get("authorities") != CANDIDATE_AUTHORITIES
        or prepared.get("sha256_index")
        != {
            "path": SHA_INDEX_NAME,
            "self_hash_included": False,
            "exact_entry_order": [MANIFEST_NAME, QA_REQUIRED_NAME, PREPARED_RECEIPT_NAME],
        }
    ):
        raise MaterializationGateError("prepared receipt binding is invalid")
    held.assert_continuity()
    return {
        "root": root,
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "sha_index_sha256": index_sha,
        "bindings": bindings,
        "materialization_out_dir": material_out,
        "execution_receipt_dir": receipt_dir,
        "held_snapshot_sha256": {
            key: snapshot.sha256 for key, snapshot in sorted(held.entries.items())
        },
    }


def _validate_go(
    go_path: Path,
    expected_go_sha256: str,
    candidate: Mapping[str, Any],
    *,
    now: datetime | None = None,
    go_snapshot: _HeldSnapshot | None = None,
) -> tuple[dict[str, Any], bytes, str]:
    owned = go_snapshot is None
    snapshot = go_snapshot
    if snapshot is None:
        try:
            snapshot = _HeldSnapshot(
                go_path,
                "external exact GO",
                expected_sha256=expected_go_sha256,
            )
        except MaterializationGateError as exc:
            if "SHA-256 mismatch" in str(exc):
                raise MaterializationGateError(
                    "external exact GO SHA-256 mismatch before parsing"
                ) from exc
            raise
    try:
        raw = snapshot.raw
        raw_sha = snapshot.sha256
        if raw_sha != _normalized_sha(
            expected_go_sha256, "expected external exact GO SHA-256"
        ):
            raise MaterializationGateError(
                "external exact GO SHA-256 mismatch before parsing"
            )
        try:
            go = _strict_json_loads(raw.decode("utf-8"), "external exact GO")
        except (UnicodeError, MaterializationGateError) as exc:
            raise MaterializationGateError("external exact GO is invalid JSON") from exc
    finally:
        if owned:
            snapshot.close()
    if not isinstance(go, dict) or set(go) != GO_TOP_LEVEL_KEYS:
        raise MaterializationGateError("external exact GO top-level keyset is not exact")
    if (
        go.get("schema") != GO_SCHEMA
        or go.get("status") != "GO"
        or go.get("scope") != GO_SCOPE
        or go.get("challenge_nonce") != candidate["manifest"]["challenge_nonce"]
    ):
        raise MaterializationGateError("external exact GO schema, scope, or nonce is invalid")
    reviewer = go.get("reviewer")
    if not isinstance(reviewer, dict) or set(reviewer) != GO_REVIEWER_KEYS:
        raise MaterializationGateError("external exact GO reviewer keyset is not exact")
    if (
        not isinstance(reviewer.get("reviewer_id"), str)
        or not reviewer["reviewer_id"].strip()
        or reviewer.get("independent") is not True
        or reviewer.get("result_blind") is not True
        or reviewer.get("reviewed_without_numerical_results") is not True
    ):
        raise MaterializationGateError("external reviewer is not independently result-blind")
    findings = go.get("findings")
    if not isinstance(findings, dict) or set(findings) != GO_FINDING_KEYS:
        raise MaterializationGateError("external exact GO finding keyset is not exact")
    if any(type(findings[key]) is not int or findings[key] < 0 for key in GO_FINDING_KEYS):
        raise MaterializationGateError("external exact GO findings must be nonnegative integers")
    if findings["p0"] != 0 or findings["p1"] != 0:
        raise MaterializationGateError("external exact GO requires zero P0 and zero P1 findings")
    bindings = go.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != GO_BINDING_KEYS:
        raise MaterializationGateError("external exact GO binding keyset is not exact")
    expected_bindings = _expected_go_bindings(
        candidate["manifest"],
        candidate["manifest_sha256"],
        candidate["sha_index_sha256"],
    )
    _require_exact_json_equal(bindings, expected_bindings, "external exact GO bindings")
    _require_exact_json_equal(
        go.get("authorities"), GO_AUTHORITIES, "external exact GO authorities"
    )
    issued = _strict_utc(go.get("issued_utc"), "GO issued_utc")
    expires = _strict_utc(go.get("expires_utc"), "GO expires_utc")
    observed_now = now or _now_utc()
    if issued > observed_now:
        raise MaterializationGateError("external exact GO is future-issued")
    if observed_now >= expires:
        raise MaterializationGateError("external exact GO is stale or expired")
    if expires <= issued or expires - issued > MAX_GO_LIFETIME:
        raise MaterializationGateError("external exact GO lifetime is invalid or exceeds 24 hours")
    return go, raw, raw_sha


def _current_runtime_and_host_match(manifest: Mapping[str, Any]) -> None:
    runtime = manifest["runtime_identity"]
    current_executable = Path(sys.executable).resolve(strict=True)
    current_numpy_version, current_numpy_origin = _active_numpy_identity()
    if (
        str(current_executable) != runtime["canonical_path"]
        or _sha256(current_executable) != runtime["sha256"]
        or ".".join(str(value) for value in sys.version_info[:3]) != runtime["python_version"]
        or sys.implementation.name != runtime["python_implementation"]
        or current_numpy_version != runtime["numpy_version"]
        or current_numpy_origin != runtime["numpy_origin"]
        or runtime["descriptor_sealed_runtime"] is not True
    ):
        raise MaterializationGateError(
            "current descriptor-sealed Python/NumPy runtime does not match the frozen identity"
        )
    current_host = _host_identity()
    if current_host != manifest["host_identity"]:
        raise MaterializationGateError("current host identity does not match the frozen identity")


def _proc_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scan_linux_current_uid_processes(
    sealed_runtime: Mapping[str, Any], runtime_identity: Mapping[str, Any]
) -> dict[str, Any]:
    if sys.platform != "linux" or not Path("/proc").is_dir():
        raise MaterializationGateError("production materialization EXECUTE requires Linux /proc")
    current_uid = os.geteuid()
    expected_manifest_sha = _normalized_sha(
        sealed_runtime["expected_runtime_closure_json_sha256"],
        "process audit runtime manifest SHA-256",
    )
    attestation = sealed_runtime["attestation"]
    expected_bootstrap_sha = _normalized_sha(
        attestation["bootstrap_sha256"], "process audit bootstrap SHA-256"
    )
    expected_pure_sha = _normalized_sha(
        attestation["pure_archive_sha256"], "process audit pure archive SHA-256"
    )
    expected_executable = runtime_identity["canonical_path"]
    expected_executable_sha = _normalized_sha(
        runtime_identity["sha256"], "process audit Python SHA-256"
    )
    entrypoint_roles = {
        "materialization": "materialization_controller",
        "runner": "paired_runner",
        "trainer": "trainer",
        "evaluator": "evaluator",
        "native_smoke": "native_smoke",
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
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status_text = (entry / "status").read_text(encoding="utf-8", errors="strict")
            uid_line = next(line for line in status_text.splitlines() if line.startswith("Uid:"))
            uid_fields = uid_line.split()
            uid = int(uid_fields[2])
            if uid != current_uid:
                continue
            raw = (entry / "cmdline").read_bytes()
            argv_bytes = raw[:-1].split(b"\0") if raw.endswith(b"\0") else []
            argv = [
                part.decode("utf-8", errors="surrogateescape") for part in argv_bytes
            ]
        except (
            FileNotFoundError,
            ProcessLookupError,
            PermissionError,
            StopIteration,
            ValueError,
            IndexError,
            UnicodeError,
            OSError,
        ):
            continue
        if (
            len(argv) != 9
            or argv[0] != expected_executable
            or argv[1:7]
            != ["-I", "-B", "-S", "/proc/self/fd/200", "--request-fd", "201"]
            or argv[7] != "--entrypoint"
            or argv[8] not in entrypoint_roles
        ):
            continue
        identity_valid = False
        request: dict[str, Any] | None = None
        observed: dict[str, Any] = {}
        try:
            request_payload = (entry / "fd" / "201").read_bytes()
            request = _strict_json_loads(
                request_payload.decode("utf-8"), f"process {entry.name} sealed request"
            )
            observed = {
                "executable_sha256": _proc_file_sha256(entry / "exe"),
                "bootstrap_fd_200_sha256": _proc_file_sha256(entry / "fd" / "200"),
                "request_fd_201_sha256": hashlib.sha256(request_payload).hexdigest(),
                "manifest_fd_202_sha256": _proc_file_sha256(entry / "fd" / "202"),
                "pure_archive_fd_203_sha256": _proc_file_sha256(entry / "fd" / "203"),
            }
            identity_valid = (
                type(request) is dict
                and set(request) == request_keys
                and request["schema"]
                == "controlled_real10k_20k_runtime_launch_request_v1"
                and request["entrypoint"] == argv[8]
                and request["expected_bootstrap_sha256"] == expected_bootstrap_sha
                and request["expected_manifest_sha256"] == expected_manifest_sha
                and request["expected_pure_archive_sha256"] == expected_pure_sha
                and request["bootstrap_fd"] == 200
                and request["manifest_fd"] == 202
                and request["pure_archive_fd"] == 203
                and request["attestation_fd"] == 204
                and observed["executable_sha256"] == expected_executable_sha
                and observed["bootstrap_fd_200_sha256"] == expected_bootstrap_sha
                and observed["manifest_fd_202_sha256"] == expected_manifest_sha
                and observed["pure_archive_fd_203_sha256"] == expected_pure_sha
            )
        except (OSError, UnicodeError, MaterializationGateError):
            identity_valid = False
        role = (
            entrypoint_roles[argv[8]]
            if identity_valid
            else "identity_invalid_sealed_candidate"
        )
        matches.append(
            {
                "pid": int(entry.name),
                "roles": [role],
                "argv": argv,
                "argv_bytes_sha256": hashlib.sha256(raw).hexdigest(),
                "identity_valid": identity_valid,
                "sealed_request": request,
                "observed_descriptor_identity": observed,
            }
        )
    matches.sort(key=lambda record: record["pid"])
    return {
        "schema": "controlled_real10k_20k_materialization_process_audit_v1",
        "uid": current_uid,
        "current_pid": os.getpid(),
        "substring_matching_used": False,
        "exact_descriptor_runtime_identity_required": True,
        "matches": matches,
        "match_count": len(matches),
    }


def _validate_singleton(audit: Mapping[str, Any]) -> None:
    if set(audit) != {
        "schema",
        "uid",
        "current_pid",
        "substring_matching_used",
        "exact_descriptor_runtime_identity_required",
        "matches",
        "match_count",
    } or (
        audit.get("schema")
        != "controlled_real10k_20k_materialization_process_audit_v1"
        or type(audit.get("uid")) is not int
        or audit["uid"] != os.geteuid()
        or type(audit.get("current_pid")) is not int
        or audit["current_pid"] != os.getpid()
        or audit.get("substring_matching_used") is not False
        or audit.get("exact_descriptor_runtime_identity_required") is not True
        or type(audit.get("match_count")) is not int
    ):
        raise MaterializationGateError("process audit contract is not exact")
    matches = audit.get("matches")
    if not isinstance(matches, list):
        raise MaterializationGateError("process audit matches are invalid")
    if audit["match_count"] != len(matches):
        raise MaterializationGateError("process audit match count is inconsistent")
    if len(matches) != 1:
        raise MaterializationGateError(
            f"controlled current-UID process count must be exactly one, observed {len(matches)}"
        )
    current = matches[0]
    if not isinstance(current, dict) or set(current) != {
        "pid",
        "roles",
        "argv",
        "argv_bytes_sha256",
        "identity_valid",
        "sealed_request",
        "observed_descriptor_identity",
    } or (
        type(current.get("pid")) is not int
        or current["pid"] != os.getpid()
        or current.get("roles") != ["materialization_controller"]
        or current.get("identity_valid") is not True
        or not isinstance(current.get("argv"), list)
        or any(type(value) is not str for value in current["argv"])
        or not _is_sha256(current.get("argv_bytes_sha256"))
        or not isinstance(current.get("sealed_request"), dict)
        or not isinstance(current.get("observed_descriptor_identity"), dict)
    ):
        raise MaterializationGateError(
            "only the current materialization controller may be present"
        )


def _rehash_frozen_closure(
    candidate: Mapping[str, Any],
    held: _HeldClosure,
    go_snapshot: _HeldSnapshot,
    expected_go_sha256: str,
) -> dict[str, Any]:
    held.assert_continuity()
    role_sha = {
        role: held.entries[f"bound:{role}"].sha256
        for role in BOUND_ROLE_ORDER
    }
    expected = {
        role: candidate["bindings"][role]["sha256"] for role in BOUND_ROLE_ORDER
    }
    if role_sha != expected:
        raise MaterializationGateError("a frozen bound artifact changed during execution")
    go_snapshot.assert_continuity()
    go_sha = go_snapshot.sha256
    if go_sha != _normalized_sha(expected_go_sha256, "expected external exact GO SHA-256"):
        raise MaterializationGateError("external exact GO SHA changed during execution")
    return {
        "candidate_manifest_sha256": candidate["manifest_sha256"],
        "candidate_sha256sums_sha256": candidate["sha_index_sha256"],
        "artifact_sha256": role_sha,
        "go_sha256": go_sha,
        "held_snapshot_consumption": True,
        "path_reopen_for_consumed_inputs": False,
    }


def _load_builder_main(
    builder_snapshot: _HeldSnapshot,
    shared_snapshot: _HeldSnapshot,
    splitter_snapshot: _HeldSnapshot,
) -> Callable[..., int]:
    package_name = "rfic_transformer_inverse_design"
    shared_name = f"{package_name}.controlled_real10k_20k_contract"
    splitter_name = f"{package_name}.model_splitting"

    def snapshot_module(
        name: str, snapshot: _HeldSnapshot, role: str
    ) -> tuple[types.ModuleType, str]:
        payload, origin = _active_member_source(role, snapshot.sha256)
        module = types.ModuleType(name)
        module.__file__ = origin
        module.__package__ = name.rpartition(".")[0]
        module.__verified_snapshot_sha256__ = snapshot.sha256
        module.__verified_snapshot_logical_path__ = origin
        sys.modules[name] = module
        try:
            code = compile(
                payload,
                origin,
                "exec",
                dont_inherit=True,
            )
            exec(code, module.__dict__)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module, origin

    previous_package = sys.modules.get(package_name)
    previous_shared = sys.modules.get(shared_name)
    previous_splitter = sys.modules.get(splitter_name)
    missing = object()
    previous_shared_attr = (
        getattr(previous_package, "controlled_real10k_20k_contract", missing)
        if previous_package is not None
        else missing
    )
    previous_splitter_attr = (
        getattr(previous_package, "model_splitting", missing)
        if previous_package is not None
        else missing
    )
    package = previous_package
    if package is None:
        package = types.ModuleType(package_name)
        package.__package__ = package_name
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules[package_name] = package
    shared_module: types.ModuleType | None = None
    splitter_module: types.ModuleType | None = None
    module_name = f"_controlled_real10k_20k_builder_{os.getpid()}"
    try:
        shared_module, shared_origin = snapshot_module(
            shared_name, shared_snapshot, "shared_contract_code"
        )
        setattr(package, "controlled_real10k_20k_contract", shared_module)
        splitter_module, splitter_origin = snapshot_module(
            splitter_name, splitter_snapshot, "splitter_code"
        )
        setattr(package, "model_splitting", splitter_module)
        module = types.ModuleType(module_name)
        builder_payload, builder_origin = _active_member_source(
            "materialization_builder_code", builder_snapshot.sha256
        )
        module.__file__ = builder_origin
        module.__package__ = module_name
        module.__verified_snapshot_sha256__ = builder_snapshot.sha256
        module.__verified_snapshot_logical_path__ = builder_origin
        sys.modules[module_name] = module
        code = compile(
            builder_payload,
            builder_origin,
            "exec",
            dont_inherit=True,
        )
        exec(code, module.__dict__)
        if (
            module.controlled_contract is not shared_module
            or module.model_splitting is not splitter_module
            or module.canonical_physical_cell_id
            is not shared_module.canonical_physical_cell_id
            or module.split_physical_feature_indices
            is not splitter_module.split_physical_feature_indices
            or getattr(shared_module, "__verified_snapshot_sha256__", None)
            != shared_snapshot.sha256
            or getattr(splitter_module, "__verified_snapshot_sha256__", None)
            != splitter_snapshot.sha256
            or shared_module.canonical_physical_cell_id.__code__.co_filename
            != shared_origin
            or splitter_module.split_physical_feature_indices.__code__.co_filename
            != splitter_origin
        ):
            raise MaterializationGateError(
                "builder callable graph is not the exact verified shared/splitter snapshot"
            )
        main = getattr(module, "main", None)
        if (
            not callable(main)
            or getattr(module, "__verified_snapshot_sha256__", None)
            != builder_snapshot.sha256
            or getattr(module, "__verified_snapshot_logical_path__", None)
            != builder_origin
            or getattr(getattr(main, "__code__", None), "co_filename", None)
            != builder_origin
        ):
            raise MaterializationGateError("frozen materialization builder has no callable main")
        return main
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    finally:
        if previous_shared is None:
            sys.modules.pop(shared_name, None)
        else:
            sys.modules[shared_name] = previous_shared
        if previous_splitter is None:
            sys.modules.pop(splitter_name, None)
        else:
            sys.modules[splitter_name] = previous_splitter
        if previous_package is None:
            sys.modules.pop(package_name, None)
        else:
            sys.modules[package_name] = previous_package
            for attribute, previous_value in (
                ("controlled_real10k_20k_contract", previous_shared_attr),
                ("model_splitting", previous_splitter_attr),
            ):
                if previous_value is missing:
                    try:
                        delattr(previous_package, attribute)
                    except AttributeError:
                        pass
                else:
                    setattr(previous_package, attribute, previous_value)


def _verified_builder_context(held: _HeldClosure) -> dict[str, Any]:
    role_to_held_key = {
        role: f"bound:{role}" for role in VERIFIED_CONTEXT_ROLES
    }
    if set(role_to_held_key) != set(VERIFIED_CONTEXT_ROLES) or any(
        key not in held.entries for key in role_to_held_key.values()
    ):
        raise MaterializationGateError("verified builder context role closure is not exact")
    return {
        "schema": VERIFIED_CONTEXT_SCHEMA,
        "entries": {
            role: held.entries[key].record()
            for role, key in role_to_held_key.items()
        },
    }


def _artifact_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    if not root.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            records[relative] = {"entry_type": "symlink", "authorized": False}
            continue
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            records[relative] = {
                "entry_type": "directory",
                "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
            }
        elif stat.S_ISREG(metadata.st_mode):
            records[relative] = {
                "entry_type": "regular_file",
                "sha256": _sha256(path),
                "size_bytes": metadata.st_size,
                "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
                "nlink": metadata.st_nlink,
            }
        else:
            records[relative] = {"entry_type": "other", "authorized": False}
    return records


def _decimal12(value: float) -> str:
    token = format(float(value), ".12f")
    return "0.000000000000" if token == "-0.000000000000" else token


def _geometry_identity(values: Sequence[float], *, portable: bool) -> str:
    payload = {
        "schema": (
            "ordered_inverse_geometry_decimal12_v1"
            if portable
            else "ordered_inverse_geometry_float64_v1"
        ),
        "columns": list(GEOMETRY_COLUMNS),
        "values": (
            [_decimal12(value) for value in values]
            if portable
            else [format(float(value), ".17g") for value in values]
        ),
    }
    return _canonical_json_sha(payload)


def _physical_cell(values: Sequence[float]) -> str:
    indices: list[int] = []
    for value, lower, upper in zip(values, INPUT_LOWER, INPUT_UPPER):
        scaled = (value - lower) / (upper - lower)
        index = int(math.floor(scaled * PHYSICAL_CELL_BINS))
        indices.append(max(0, min(PHYSICAL_CELL_BINS - 1, index)))
    return ":".join(str(value) for value in indices)


def _read_and_validate_arm(path: Path, expected_rows: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(OUTPUT_COLUMNS):
            raise MaterializationGateError(f"material arm schema mismatch: {path.name}")
        for row_number, row in enumerate(reader, start=1):
            if set(row) != set(OUTPUT_COLUMNS) or None in row or any(
                value is None for value in row.values()
            ):
                raise MaterializationGateError(f"material arm row schema mismatch: {path.name}:{row_number}")
            try:
                inputs = tuple(float(row[column]) for column in INPUT_COLUMNS)
                geometry = tuple(float(row[column]) for column in GEOMETRY_COLUMNS)
            except ValueError as exc:
                raise MaterializationGateError(
                    f"material arm numeric parse failed: {path.name}:{row_number}"
                ) from exc
            if any(not math.isfinite(value) for value in inputs + geometry):
                raise MaterializationGateError("material arm contains non-finite data")
            if any(
                value < lower or value > upper
                for value, lower, upper in zip(inputs, INPUT_LOWER, INPUT_UPPER)
            ) or any(
                value < lower or value > upper
                for value, lower, upper in zip(geometry, GEOMETRY_LOWER, GEOMETRY_UPPER)
            ):
                raise MaterializationGateError("material arm contains data outside frozen bounds")
            if row["controlled_physical_cell_4d"] != _physical_cell(inputs):
                raise MaterializationGateError("material arm physical-cell identity mismatch")
            if row["canonical_geometry_identity_sha256"] != _geometry_identity(
                geometry, portable=False
            ) or row["portable_geometry_decimal12_sha256"] != _geometry_identity(
                geometry, portable=True
            ):
                raise MaterializationGateError("material arm geometry identity mismatch")
            if not _is_sha256(row["touchstone_sha256"]):
                raise MaterializationGateError("material arm Touchstone SHA-256 is invalid")
            if row["controlled_split_assignment"] not in {"train", "validation", "test"}:
                raise MaterializationGateError("material arm split label is invalid")
            rows.append(dict(row))
    if len(rows) != expected_rows:
        raise MaterializationGateError(
            f"material arm row count mismatch: {path.name} expected={expected_rows} actual={len(rows)}"
        )
    return rows


def _line_set_sha(values: Iterable[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in sorted(values)).encode("ascii")).hexdigest()


def _row_record_set_sha(rows: Sequence[Mapping[str, str]]) -> str:
    return _line_set_sha(
        json.dumps(
            {column: row[column] for column in OUTPUT_COLUMNS},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        for row in rows
    )


def _artifact_map_exact(root: Path, names: Sequence[str]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": str(root / name),
            "sha256": _sha256(root / name),
            "size_bytes": (root / name).stat().st_size,
        }
        for name in names
    }


def _validate_material_output(candidate: Mapping[str, Any]) -> dict[str, Any]:
    root = _canonical_existing_dir(candidate["materialization_out_dir"], "material output")
    observed = {path.name for path in root.iterdir()}
    expected = set(MATERIAL_OUTPUT_ORDER) | {MATERIAL_SHA_INDEX_NAME}
    if observed != expected:
        raise MaterializationGateError(
            f"material output closure mismatch: missing={sorted(expected-observed)} extra={sorted(observed-expected)}"
        )
    for name in expected:
        _canonical_regular_file(root / name, f"material output {name}")
    index_records = _parse_sha_index(root / MATERIAL_SHA_INDEX_NAME, root)
    if [name for name, _ in index_records] != list(MATERIAL_OUTPUT_ORDER):
        raise MaterializationGateError("material output SHA index order is not exact")

    small = _read_and_validate_arm(root / MATERIAL_OUTPUT_ORDER[0], COUNTS["historical_source"])
    large = _read_and_validate_arm(root / MATERIAL_OUTPUT_ORDER[1], COUNTS["large_source"])
    if small != large[: len(small)]:
        raise MaterializationGateError("small arm is not the exact ordered full-row prefix of large")
    extra = large[len(small) :]
    if any(row["controlled_split_assignment"] != "train" for row in extra):
        raise MaterializationGateError("an appended large-arm row is not train-only")
    split_counts = {
        split: sum(row["controlled_split_assignment"] == split for row in small)
        for split in ("train", "validation", "test")
    }
    if split_counts != {
        "train": COUNTS["small_gradient_train"],
        "validation": COUNTS["validation"],
        "test": COUNTS["test"],
    }:
        raise MaterializationGateError("small-arm split counts are not exact")
    all_cells = {
        split: {
            row["controlled_physical_cell_4d"]
            for row in small
            if row["controlled_split_assignment"] == split
        }
        for split in ("train", "validation", "test")
    }
    if (
        all_cells["train"] & all_cells["validation"]
        or all_cells["train"] & all_cells["test"]
        or all_cells["validation"] & all_cells["test"]
    ):
        raise MaterializationGateError("train/validation/test physical cells overlap")
    if {row["controlled_physical_cell_4d"] for row in extra} - all_cells["train"]:
        raise MaterializationGateError("an appended row lies outside historical train cells")
    exact_ids = [row["canonical_geometry_identity_sha256"] for row in large]
    portable_ids = [row["portable_geometry_decimal12_sha256"] for row in large]
    touchstone_ids = [row["touchstone_sha256"] for row in large]
    if any(len(set(values)) != len(values) for values in (exact_ids, portable_ids, touchstone_ids)):
        raise MaterializationGateError("material arm identity uniqueness failed")

    holdout_path = root / MATERIAL_OUTPUT_ORDER[2]
    norm_path = root / MATERIAL_OUTPUT_ORDER[3]
    summary_path = root / MATERIAL_OUTPUT_ORDER[4]
    qa_path = root / MATERIAL_OUTPUT_ORDER[5]
    receipt_path = root / MATERIAL_OUTPUT_ORDER[6]
    holdout = _read_json(holdout_path, "material holdout manifest")
    holdout_expected_keys = {
        "schema",
        "identity_kind",
        "historical_model_summary_sha256",
        "shared_contract_sha256",
        "selection_method",
        "selection_uses_model_results",
        "stratification",
        "physical_cell_encoding",
        "physical_cell_bins",
        "physical_lower",
        "physical_upper",
        "validation_count",
        "test_count",
        "validation_geometry_identities",
        "test_geometry_identities",
        "validation_portable_decimal12_geometry_identities",
        "test_portable_decimal12_geometry_identities",
        "train_cell_ids",
        "validation_cell_ids",
        "test_cell_ids",
        "physical_cell_partition_fingerprint_sha256",
        "complete_cell_isolation",
        "common_holdout_fingerprint_sha256",
        "boundary",
    }
    if set(holdout) != holdout_expected_keys:
        raise MaterializationGateError("material holdout keyset is not exact")
    validation_rows = [row for row in small if row["controlled_split_assignment"] == "validation"]
    test_rows = [row for row in small if row["controlled_split_assignment"] == "test"]
    if (
        holdout.get("schema") != "fixed_common_holdout_geometry_identity_v1"
        or holdout.get("identity_kind") != "canonical_geometry_sha256"
        or holdout.get("historical_model_summary_sha256")
        != FROZEN_HISTORICAL_SUMMARY_SHA256
        or holdout.get("shared_contract_sha256")
        != candidate["bindings"]["shared_contract_code"]["sha256"]
        or holdout.get("selection_uses_model_results") is not False
        or holdout.get("physical_cell_encoding") != PHYSICAL_CELL_ENCODING
        or holdout.get("physical_cell_bins") != PHYSICAL_CELL_BINS
        or holdout.get("physical_lower") != list(INPUT_LOWER)
        or holdout.get("physical_upper") != list(INPUT_UPPER)
        or holdout.get("validation_count") != COUNTS["validation"]
        or holdout.get("test_count") != COUNTS["test"]
        or holdout.get("validation_geometry_identities")
        != sorted(row["canonical_geometry_identity_sha256"] for row in validation_rows)
        or holdout.get("test_geometry_identities")
        != sorted(row["canonical_geometry_identity_sha256"] for row in test_rows)
        or holdout.get("validation_portable_decimal12_geometry_identities")
        != sorted(row["portable_geometry_decimal12_sha256"] for row in validation_rows)
        or holdout.get("test_portable_decimal12_geometry_identities")
        != sorted(row["portable_geometry_decimal12_sha256"] for row in test_rows)
        or holdout.get("train_cell_ids") != sorted(all_cells["train"])
        or holdout.get("validation_cell_ids") != sorted(all_cells["validation"])
        or holdout.get("test_cell_ids") != sorted(all_cells["test"])
    ):
        raise MaterializationGateError("material common holdout identity contract failed")
    complete_isolation = holdout.get("complete_cell_isolation")
    if not isinstance(complete_isolation, dict) or (
        complete_isolation.get("all_historical_rows_assigned_once") is not True
        or complete_isolation.get("every_cell_assigned_to_exactly_one_split") is not True
        or complete_isolation.get("train_validation_test_cell_overlap_count") != 0
        or complete_isolation.get("appended_rows_restricted_to_train_cells") is not True
        or complete_isolation.get("appended_train_row_count") != COUNTS["extra"]
    ):
        raise MaterializationGateError("material complete-cell isolation proof failed")

    normalization = _read_json(norm_path, "material normalization contract")
    expected_normalization = {
        "schema": "declared_midpoint_half_range_normalization_v1",
        "input_columns": list(INPUT_COLUMNS),
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "input_lower": list(INPUT_LOWER),
        "input_upper": list(INPUT_UPPER),
        "geometry_lower": list(GEOMETRY_LOWER),
        "geometry_upper": list(GEOMETRY_UPPER),
        "input_midpoint": [(a + b) * 0.5 for a, b in zip(INPUT_LOWER, INPUT_UPPER)],
        "input_half_range": [(b - a) * 0.5 for a, b in zip(INPUT_LOWER, INPUT_UPPER)],
        "geometry_midpoint": [(a + b) * 0.5 for a, b in zip(GEOMETRY_LOWER, GEOMETRY_UPPER)],
        "geometry_half_range": [(b - a) * 0.5 for a, b in zip(GEOMETRY_LOWER, GEOMETRY_UPPER)],
        "train_arm_specific_statistics_used": False,
        "large_arm_empirical_statistics_used": False,
        "all_loaded_rows_required_inside_declared_bounds": True,
        "boundary": (
            "Both arms use identical declared midpoint/half-range arrays and the identical sigmoid decoder "
            "envelope. No arm-specific empirical mean, variance, minimum, or maximum is used."
        ),
    }
    if normalization != expected_normalization:
        raise MaterializationGateError("material declared normalization contract is not exact")

    summary = _read_json(summary_path, "material summary")
    if (
        summary.get("schema") != "controlled_real10k_20k_nested_materialization_v2"
        or summary.get("status") != "PASS"
        or summary.get("decision") != "PREPARED_FOR_INDEPENDENT_QA"
        or summary.get("result_accessed") is not False
        or summary.get("model_training_performed") is not False
        or summary.get("emx_performed") is not False
        or summary.get("training_launch_authorized") is not False
        or summary.get("independent_qa_required") is not True
        or summary.get("production_exact_checks")
        != {key: True for key in PRODUCTION_EXACT_CHECKS}
        or summary.get("arm_counts")
        != {
            "n10000": {
                "source_table_rows": COUNTS["historical_source"],
                "gradient_train_rows": COUNTS["small_gradient_train"],
                "validation_rows": COUNTS["validation"],
                "test_rows": COUNTS["test"],
            },
            "n20000": {
                "source_table_rows": COUNTS["large_source"],
                "gradient_train_rows": COUNTS["large_gradient_train"],
                "validation_rows": COUNTS["validation"],
                "test_rows": COUNTS["test"],
            },
        }
    ):
        raise MaterializationGateError("material production summary decision is invalid")
    expected_verified_consumption = {
        "mode": "GATE_VERIFIED_HELD_BYTES_ONLY",
        "verified_context_schema": VERIFIED_CONTEXT_SCHEMA,
        "exact_role_order": list(VERIFIED_CONTEXT_ROLES),
        "role_sha256": {
            role: candidate["bindings"][role]["sha256"]
            for role in VERIFIED_CONTEXT_ROLES
        },
        "path_reopen_for_consumed_inputs": False,
    }
    if summary.get("verified_input_consumption") != expected_verified_consumption:
        raise MaterializationGateError(
            "material output lacks exact verified held-byte consumption proof"
        )
    expected_impl = {
        "builder": candidate["bindings"]["materialization_builder_code"],
        "shared_contract": candidate["bindings"]["shared_contract_code"],
        "splitter_source": candidate["bindings"]["splitter_code"],
    }
    for role, bound in expected_impl.items():
        observed_record = summary.get("implementation_identities", {}).get(role)
        if not isinstance(observed_record, dict) or (
            observed_record.get("path") != bound["path"]
            or observed_record.get("sha256") != bound["sha256"]
            or observed_record.get("size_bytes") != bound["size_bytes"]
        ):
            raise MaterializationGateError(f"material implementation identity mismatch: {role}")
    source_records = {
        "historical_10k_csv": ("historical_10k_csv", COUNTS["historical_source"]),
        "authoritative_100k_csv": ("authoritative_100k_csv", COUNTS["authoritative_source"]),
        "historical_model_summary_json": ("historical_model_summary_json", None),
    }
    for summary_role, (bound_role, rows) in source_records.items():
        observed = summary.get("source_identities", {}).get(summary_role)
        bound = candidate["bindings"][bound_role]
        if not isinstance(observed, dict) or observed.get("path") != bound["path"] or observed.get(
            "sha256"
        ) != bound["sha256"]:
            raise MaterializationGateError(f"material source identity mismatch: {summary_role}")
        if rows is not None and observed.get("rows") != rows:
            raise MaterializationGateError(f"material source row count mismatch: {summary_role}")
    nested = summary.get("nested_identity_contract")
    if not isinstance(nested, dict) or (
        nested.get("arm_n10000_is_exact_ordered_prefix_and_row_subset_of_arm_n20000") is not True
        or nested.get("common_output_schema") != list(OUTPUT_COLUMNS)
        or nested.get("historical_row_record_set_sha256") != _row_record_set_sha(small)
        or nested.get("extra_row_record_set_sha256") != _row_record_set_sha(extra)
        or nested.get("common_validation_and_test_unchanged") is not True
        or nested.get("geometry_identity_overlap_historical_vs_extra") != 0
        or nested.get("touchstone_identity_overlap_historical_vs_extra") != 0
    ):
        raise MaterializationGateError("material nested identity summary failed")
    artifacts4 = _artifact_map_exact(root, MATERIAL_OUTPUT_ORDER[:4])
    if summary.get("artifacts") != artifacts4:
        raise MaterializationGateError("material summary artifact map mismatch")
    fixed = summary.get("fixed_contracts")
    if not isinstance(fixed, dict) or (
        fixed.get("common_holdout")
        != {"path": str(holdout_path), "sha256": _sha256(holdout_path)}
        or fixed.get("declared_midpoint_half_range_normalization", {}).get("path")
        != str(norm_path)
        or fixed.get("declared_midpoint_half_range_normalization", {}).get("sha256")
        != _sha256(norm_path)
        or fixed.get("declared_midpoint_half_range_normalization", {}).get(
            "train_arm_specific_statistics_used"
        )
        is not False
        or fixed.get("declared_midpoint_half_range_normalization", {}).get(
            "large_arm_empirical_statistics_used"
        )
        is not False
    ):
        raise MaterializationGateError("material fixed-contract summary failed")

    qa = _read_json(qa_path, "material independent-QA-required")
    if (
        qa.get("schema") != "controlled_real10k_20k_independent_qa_required_v2"
        or qa.get("status") != "INDEPENDENT_QA_REQUIRED"
        or qa.get("verdict") != "NO_GO_PENDING_FRESH_INDEPENDENT_QA"
        or qa.get("training_authorized") is not False
        or qa.get("result_access_authorized") is not False
        or qa.get("fresh_emx_authorized") is not False
        or qa.get("implementation_identities") != summary.get("implementation_identities")
        or qa.get("frozen_artifacts") != artifacts4
        or qa.get("materialization_summary")
        != {"path": str(summary_path), "sha256": _sha256(summary_path)}
    ):
        raise MaterializationGateError("material independent-QA-required record failed")
    frozen_science = qa.get("frozen_scientific_contract")
    if not isinstance(frozen_science, dict) or frozen_science != {
        "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
        "physical_cell_bins": PHYSICAL_CELL_BINS,
        "extra_selection_seed": SELECTION_SEED,
        "paired_seeds": list(PAIRED_SEEDS),
        "source_table_rows": {"n10000": COUNTS["historical_source"], "n20000": COUNTS["large_source"]},
        "gradient_train_rows": {
            "n10000": COUNTS["small_gradient_train"],
            "n20000": COUNTS["large_gradient_train"],
        },
        "validation_rows_common": COUNTS["validation"],
        "test_rows_common": COUNTS["test"],
    }:
        raise MaterializationGateError("material frozen scientific contract failed")

    artifacts6 = _artifact_map_exact(root, MATERIAL_OUTPUT_ORDER[:6])
    receipt = _read_json(receipt_path, "material receipt")
    if (
        receipt.get("schema") != "controlled_real10k_20k_nested_materialization_receipt_v2"
        or receipt.get("status") != "PASS"
        or receipt.get("verdict") != "PREPARED_FOR_INDEPENDENT_QA"
        or receipt.get("source_sha256")
        != {
            "historical_10k_csv": FROZEN_HISTORICAL_10K_SHA256,
            "authoritative_100k_csv": FROZEN_AUTHORITATIVE_100K_SHA256,
            "historical_model_summary_json": FROZEN_HISTORICAL_SUMMARY_SHA256,
        }
        or receipt.get("implementation_identities") != summary.get("implementation_identities")
        or receipt.get("artifact_identities") != artifacts6
        or receipt.get("arm_source_rows")
        != {"n10000": COUNTS["historical_source"], "n20000": COUNTS["large_source"]}
        or receipt.get("gradient_train_rows")
        != {"n10000": COUNTS["small_gradient_train"], "n20000": COUNTS["large_gradient_train"]}
        or receipt.get("validation_rows_common") != COUNTS["validation"]
        or receipt.get("test_rows_common") != COUNTS["test"]
        or receipt.get("production_exact_checks")
        != {key: True for key in PRODUCTION_EXACT_CHECKS}
        or receipt.get("training_launch_authorized") is not False
        or receipt.get("independent_qa_required") is not True
        or receipt.get("independent_qa_required_record")
        != {"path": str(qa_path), "sha256": _sha256(qa_path)}
        or receipt.get("next_legal_gate") != "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO"
    ):
        raise MaterializationGateError("material terminal receipt failed")
    closure = _artifact_snapshot(root)
    if any(record.get("entry_type") != "regular_file" for record in closure.values()):
        raise MaterializationGateError("material output closure contains a non-regular file")
    return {
        "status": "PASS_MATERIALIZATION_DEEP_VALIDATED_RESULT_BLIND",
        "root": str(root),
        "arm_rows": {"n10000": len(small), "n20000": len(large)},
        "gradient_train_rows": {
            "n10000": split_counts["train"],
            "n20000": split_counts["train"] + len(extra),
        },
        "validation_rows_common": split_counts["validation"],
        "test_rows_common": split_counts["test"],
        "artifact_closure": closure,
        "sha256sums_sha256": _sha256(root / MATERIAL_SHA_INDEX_NAME),
        "training_authorized": False,
        "evaluation_authorized": False,
        "fresh_emx_authorized": False,
    }


def _freeze_execution_receipt_dir(path: Path) -> None:
    for child in path.iterdir():
        if child.is_file() and not child.is_symlink():
            child.chmod(FILE_MODE)
    _fsync_dir(path)
    path.chmod(FROZEN_DIRECTORY_MODE)
    _fsync_dir(path.parent)


def _write_failure(
    receipt_dir: Path,
    *,
    candidate: Mapping[str, Any],
    go_sha: str,
    exc: BaseException,
    phase: str,
) -> None:
    failure = {
        "schema": FAIL_SCHEMA,
        "generated_utc": _utc_now(),
        "status": "FAIL_NO_GO",
        "phase": phase,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": traceback.format_exc(),
        "candidate_manifest_sha256": candidate["manifest_sha256"],
        "candidate_sha256sums_sha256": candidate["sha_index_sha256"],
        "go_sha256": go_sha,
        "materialization_out_dir": str(candidate["materialization_out_dir"]),
        "partial_material_output_preserved": True,
        "partial_material_output_closure": _artifact_snapshot(
            candidate["materialization_out_dir"]
        ),
        "execution_receipt_precursor_closure": _artifact_snapshot(receipt_dir),
        "sealed_runtime": candidate["manifest"]["sealed_runtime"],
        "retry_authorized": False,
        "training_authorized": False,
        "evaluation_authorized": False,
        "numerical_metric_access_authorized": False,
        "fresh_emx_authorized": False,
        "emx_generation_authorized": False,
        "process_signal_authorized": False,
    }
    _write_json_exclusive(receipt_dir / FAIL_NAME, failure)
    _freeze_execution_receipt_dir(receipt_dir)


def _execute(args: argparse.Namespace) -> dict[str, Path]:
    held = _HeldClosure()
    try:
        return _execute_with_held(args, held)
    finally:
        held.close()


def _execute_with_held(
    args: argparse.Namespace, held: _HeldClosure
) -> dict[str, Path]:
    candidate_root = _canonical_existing_dir(args.candidate_dir, "--candidate-dir")
    candidate = _audit_candidate_closure(
        candidate_root,
        expected_manifest_sha=args.candidate_manifest_sha256,
        expected_index_sha=args.candidate_sha256sums_sha256,
        held=held,
    )
    observed_runtime_attestation = _require_sealed_runtime(
        candidate["manifest"]["sealed_runtime"][
            "expected_runtime_closure_json_sha256"
        ]
    )
    _require_exact_json_equal(
        observed_runtime_attestation,
        candidate["manifest"]["sealed_runtime"]["attestation"],
        "EXECUTE sealed runtime attestation",
    )
    go_path = _absolute_path(args.go_json, "--go-json")
    go_snapshot = held.open(
        "external_exact_go",
        go_path,
        "external exact GO",
        expected_sha256=args.go_sha256,
    )
    _go, go_raw, go_sha = _validate_go(
        go_path,
        args.go_sha256,
        candidate,
        go_snapshot=go_snapshot,
    )
    _current_runtime_and_host_match(candidate["manifest"])
    # Both paths were already checked by the candidate audit.  Recheck directly
    # before consuming the GO so an earlier partial or replay can never launch.
    material_out = _canonical_future_dir(
        str(candidate["materialization_out_dir"]), "materialization output"
    )
    receipt_dir = _canonical_future_dir(
        str(candidate["execution_receipt_dir"]), "execution receipt"
    )
    process_audit = _scan_linux_current_uid_processes(
        candidate["manifest"]["sealed_runtime"],
        candidate["manifest"]["runtime_identity"],
    )
    _validate_singleton(process_audit)
    pre_intent_snapshot = _rehash_frozen_closure(
        candidate, held, go_snapshot, args.go_sha256
    )

    receipt_dir.mkdir(mode=WORKING_DIRECTORY_MODE)
    try:
        _write_bytes_exclusive(receipt_dir / GO_COPY_NAME, go_raw)
        intent = {
            "schema": INTENT_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "INTENT_RESULT_BLIND_MATERIALIZATION_ONLY",
            "candidate_manifest_sha256": candidate["manifest_sha256"],
            "candidate_sha256sums_sha256": candidate["sha_index_sha256"],
            "go_sha256": go_sha,
            "challenge_nonce": candidate["manifest"]["challenge_nonce"],
            "materialization_out_dir": str(material_out),
            "execution_receipt_dir": str(receipt_dir),
            "process_audit": process_audit,
            "pre_intent_frozen_closure": pre_intent_snapshot,
            "sealed_runtime": candidate["manifest"]["sealed_runtime"],
            "single_use": True,
            "subprocess_spawned": False,
            "process_signal_sent": False,
            "training_authorized": False,
            "evaluation_authorized": False,
            "numerical_metric_access_authorized": False,
            "fresh_emx_authorized": False,
        }
        _write_json_exclusive(receipt_dir / INTENT_NAME, intent)
        builder_snapshot = held.entries["bound:materialization_builder_code"]
        shared_snapshot = held.entries["bound:shared_contract_code"]
        splitter_snapshot = held.entries["bound:splitter_code"]
        builder_main = _load_builder_main(
            builder_snapshot, shared_snapshot, splitter_snapshot
        )
        verified_context = _verified_builder_context(held)
        immediately_before = _rehash_frozen_closure(
            candidate, held, go_snapshot, args.go_sha256
        )
        if immediately_before != pre_intent_snapshot:
            raise MaterializationGateError("frozen closure changed between intent and invocation")
        invocation_process_audit = _scan_linux_current_uid_processes(
            candidate["manifest"]["sealed_runtime"],
            candidate["manifest"]["runtime_identity"],
        )
        _validate_singleton(invocation_process_audit)
        running = {
            "schema": RUNNING_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "RUNNING_IN_PROCESS_RESULT_BLIND_MATERIALIZATION",
            "pid": os.getpid(),
            "builder_path": str(builder_snapshot.path),
            "builder_sha256": candidate["bindings"]["materialization_builder_code"]["sha256"],
            "verified_context_schema": VERIFIED_CONTEXT_SCHEMA,
            "verified_context_exact_role_order": list(VERIFIED_CONTEXT_ROLES),
            "verified_context_role_sha256": {
                role: verified_context["entries"][role]["sha256"]
                for role in VERIFIED_CONTEXT_ROLES
            },
            "builder_shared_splitter_loaded_from_verified_bytes": True,
            "source_rows_and_summary_consumed_from_verified_bytes": True,
            "path_reopen_for_consumed_inputs": False,
            "immediately_before_invocation_closure": immediately_before,
            "process_audit_immediately_before_invocation": invocation_process_audit,
            "sealed_runtime": candidate["manifest"]["sealed_runtime"],
            "builder_invocation": "IN_PROCESS_MAIN_ONLY",
            "subprocess_spawned": False,
            "process_signal_sent": False,
            "training_authorized": False,
            "evaluation_authorized": False,
            "fresh_emx_authorized": False,
        }
        _write_json_exclusive(receipt_dir / RUNNING_NAME, running)
        return_code = builder_main(
            list(candidate["manifest"]["materialization_contract"]["builder_argv"]),
            verified_context=verified_context,
        )
        if return_code not in {None, 0}:
            raise MaterializationGateError(
                f"in-process materialization builder returned nonzero: {return_code}"
            )
        validation = _validate_material_output(candidate)
        post_validation_closure = _rehash_frozen_closure(
            candidate, held, go_snapshot, args.go_sha256
        )
        if post_validation_closure != pre_intent_snapshot:
            raise MaterializationGateError("frozen source/code/protocol closure changed during materialization")
        complete = {
            "schema": COMPLETE_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "COMPLETE_RESULT_BLIND_MATERIALIZATION_DEEP_VALIDATED",
            "candidate_manifest_sha256": candidate["manifest_sha256"],
            "candidate_sha256sums_sha256": candidate["sha_index_sha256"],
            "go_sha256": go_sha,
            "challenge_nonce": candidate["manifest"]["challenge_nonce"],
            "candidate_manifest": {
                "path": str(candidate["root"] / MANIFEST_NAME),
                "sha256": candidate["manifest_sha256"],
            },
            "candidate_sha_index": {
                "path": str(candidate["root"] / SHA_INDEX_NAME),
                "sha256": candidate["sha_index_sha256"],
            },
            "materialization_go_authority": {
                "path": str(receipt_dir / GO_COPY_NAME),
                "sha256": go_sha,
            },
            "materialization_output": {
                "path": str(material_out),
                "sha256sums": {
                    "path": str(material_out / MATERIAL_SHA_INDEX_NAME),
                    "sha256": validation["sha256sums_sha256"],
                },
                "artifact_closure": validation["artifact_closure"],
            },
            "materialization_validation": validation,
            "frozen_closure_after_materialization": post_validation_closure,
            "sealed_runtime": candidate["manifest"]["sealed_runtime"],
            "execution_precursor_closure": _artifact_snapshot(receipt_dir),
            "retry_authorized": False,
            "training_authorized": False,
            "evaluation_authorized": False,
            "common_test_access_authorized": False,
            "numerical_metric_access_authorized": False,
            "fresh_emx_authorized": False,
            "emx_generation_authorized": False,
            "process_signal_sent": False,
            "subprocess_spawned": False,
            "next_legal_gate": "FRESH_INDEPENDENT_QA_OF_MATERIALIZED_DATA_AND_TRAINING_CONTRACT",
        }
        _write_json_exclusive(receipt_dir / COMPLETE_NAME, complete)
        _freeze_execution_receipt_dir(receipt_dir)
        return {
            "execution_receipt_dir": receipt_dir,
            "complete_receipt": receipt_dir / COMPLETE_NAME,
            "materialization_out_dir": material_out,
        }
    except BaseException as exc:
        if not (receipt_dir / COMPLETE_NAME).exists() and not (receipt_dir / FAIL_NAME).exists():
            _write_failure(
                receipt_dir,
                candidate=candidate,
                go_sha=go_sha,
                exc=exc,
                phase="POST_INTENT_IN_PROCESS_MATERIALIZATION_OR_DEEP_VALIDATION",
            )
        raise


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("PREPARE", "EXECUTE"), required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--materialization-out-dir")
    parser.add_argument("--execution-receipt-dir")
    parser.add_argument("--historical-10k-csv")
    parser.add_argument("--historical-10k-sha256")
    parser.add_argument("--authoritative-100k-csv")
    parser.add_argument("--authoritative-100k-sha256")
    parser.add_argument("--historical-model-summary-json")
    parser.add_argument("--historical-model-summary-sha256")
    parser.add_argument("--builder-script")
    parser.add_argument("--builder-sha256")
    parser.add_argument("--shared-contract")
    parser.add_argument("--shared-contract-sha256")
    parser.add_argument("--splitter-source")
    parser.add_argument("--splitter-sha256")
    parser.add_argument("--prereg-v1")
    parser.add_argument("--prereg-v1-sha256")
    parser.add_argument("--prereg-addendum-v1-1")
    parser.add_argument("--prereg-addendum-v1-1-sha256")
    parser.add_argument("--prereg-addendum-v1-2")
    parser.add_argument("--prereg-addendum-v1-2-sha256")
    parser.add_argument("--mars-preflight-root")
    parser.add_argument("--mars-preflight-committed-sha256")
    parser.add_argument("--python-executable")
    parser.add_argument("--python-executable-sha256")
    parser.add_argument("--expected-runtime-closure-json-sha256")
    parser.add_argument("--expected-hostname")
    parser.add_argument("--expected-uid", type=int)
    parser.add_argument("--expected-python-version")
    parser.add_argument("--candidate-manifest-sha256")
    parser.add_argument("--candidate-sha256sums-sha256")
    parser.add_argument("--go-json")
    parser.add_argument("--go-sha256")
    args = parser.parse_args(argv)
    prepare_required = (
        "materialization_out_dir",
        "execution_receipt_dir",
        "historical_10k_csv",
        "historical_10k_sha256",
        "authoritative_100k_csv",
        "authoritative_100k_sha256",
        "historical_model_summary_json",
        "historical_model_summary_sha256",
        "builder_script",
        "builder_sha256",
        "shared_contract",
        "shared_contract_sha256",
        "splitter_source",
        "splitter_sha256",
        "prereg_v1",
        "prereg_v1_sha256",
        "prereg_addendum_v1_1",
        "prereg_addendum_v1_1_sha256",
        "prereg_addendum_v1_2",
        "prereg_addendum_v1_2_sha256",
        "mars_preflight_root",
        "mars_preflight_committed_sha256",
        "python_executable",
        "python_executable_sha256",
        "expected_runtime_closure_json_sha256",
    )
    execute_required = (
        "candidate_manifest_sha256",
        "candidate_sha256sums_sha256",
        "go_json",
        "go_sha256",
    )
    required = prepare_required if args.phase == "PREPARE" else execute_required
    forbidden = execute_required if args.phase == "PREPARE" else prepare_required + (
        "expected_hostname",
        "expected_uid",
        "expected_python_version",
    )
    missing = [name for name in required if getattr(args, name) in {None, ""}]
    supplied_forbidden = [name for name in forbidden if getattr(args, name) is not None]
    if missing:
        parser.error(f"{args.phase} missing required arguments: {missing}")
    if supplied_forbidden:
        parser.error(f"{args.phase} received forbidden arguments: {supplied_forbidden}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.phase == "PREPARE":
        result = _prepare(args)
        print("status=PASS_PREPARED_AWAITING_EXTERNAL_EXACT_GO")
        print(f"candidate_dir={result['candidate_dir']}")
        print(f"manifest_sha256={_sha256(result['manifest'])}")
        print(f"sha256sums_sha256={_sha256(result['sha_index'])}")
    else:
        result = _execute(args)
        print("status=COMPLETE_RESULT_BLIND_MATERIALIZATION_DEEP_VALIDATED")
        print(f"materialization_out_dir={result['materialization_out_dir']}")
        print(f"complete_receipt={result['complete_receipt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
