#!/usr/bin/env python3
"""Prepare or execute the gated nested real-EMX 10K/20K paired training.

The default ``prepare`` phase performs result-blind materialization closure
checks and freezes an immutable run contract, six exact commands, a prepared
receipt, a SHA-256 package index, and ``INDEPENDENT_QA_REQUIRED.json``.  It
never spawns the trainer.

The ``execute`` phase accepts only a fresh external independent-QA exact-GO
receipt whose SHA-256 and every frozen binding match.  Each paired seed is run
serially (10K then 20K) with validation-only evaluation.  Existing incomplete
attempts are ambiguous and fail closed; they are never automatically resumed
or duplicated.  Completed attempts are deeply reverified before being reused.

This controller never runs EMX, never evaluates the common test split, and
never reads or releases model-quality numerical statistics.  Six completed
validation-only arms are only READY_FOR_ONE_TIME_COMMON_TEST_EVALUATION.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import json
import math
import os
import platform
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rfic_transformer_inverse_design.controlled_real10k_20k_contract as shared_contract
import rfic_transformer_inverse_design.controlled_real10k_20k_runtime_bootstrap as runtime_bootstrap
from rfic_transformer_inverse_design.controlled_real10k_20k_contract import (
    EXACT_EXTRA_SELECTION_SEED,
    EXACT_PAIRED_SEEDS,
    GEOMETRY_COLUMNS,
    GEOMETRY_LOWER,
    GEOMETRY_UPPER,
    INPUT_COLUMNS,
    INPUT_LOWER,
    INPUT_UPPER,
    OUTPUT_COLUMNS,
    PHYSICAL_CELL_BINS,
    PHYSICAL_CELL_ENCODING,
    canonical_physical_cell_id,
)


RUN_SCHEMA = "controlled_real10k_20k_paired_controller_v4"
MATERIAL_SCHEMA = "controlled_real10k_20k_nested_materialization_v2"
MATERIAL_RECEIPT_SCHEMA = "controlled_real10k_20k_nested_materialization_receipt_v2"
MATERIAL_QA_REQUIRED_SCHEMA = "controlled_real10k_20k_independent_qa_required_v2"
HOLDOUT_SCHEMA = "fixed_common_holdout_geometry_identity_v1"
NORMALIZATION_SCHEMA = "declared_midpoint_half_range_normalization_v1"
GO_SCHEMA = "controlled_real10k_20k_independent_qa_exact_go_v3"
GO_STATUS = "GO"
GO_VERDICT = "EXACT_GO"
GO_SCOPE = "TRAIN_SIX_PAIRED_ARMS_VALIDATION_ONLY"
FINAL_STATUS = "READY_FOR_ONE_TIME_COMMON_TEST_EVALUATION"
MAX_GO_VALIDITY = timedelta(hours=24)
MAX_LOAD1 = 40.0
THREAD_LIMIT = 4
CHILD_NICE = 19
_HELD_SINGLETON_FD: int | None = None
_HELD_PYTHON_FD: int | None = None
PRODUCTION_TRAINER_SHA256 = "92988524b08b15a2388f655f6239070889098024e49ee184832f69876f7db3be"
PRODUCTION_PYTHON_SHA256 = "8c515a32b1a5d3d807e53359901a4d09ec06819b488736641aabd6a12eefba63"
PRODUCTION_PYTHON_VERSION = "3.12.13"
PRODUCTION_NUMPY_VERSION = "2.5.0"
PROCESS_AUDIT_SCHEMA = "controlled_real10k_20k_linux_process_exclusivity_audit_v2"
CONTROLLED_PROCESS_BASENAMES = frozenset(
    {
        "build_controlled_real10k_20k_nested.py",
        "run_controlled_real10k_20k_materialization.py",
        "run_controlled_real10k_20k_paired.py",
        "train_physical_feature_tandem_inverse.py",
        "evaluate_controlled_real10k_20k_common.py",
        "controlled_real10k_20k_mars_native_smoke.py",
    }
)
SUMMARY_NAME = "physical_feature_tandem_inverse_summary.json"
WEIGHTS_NAME = "physical_feature_tandem_inverse_weights.npz"
HISTORY_NAME = "physical_feature_tandem_inverse_history.csv"
VALIDATION_PREDICTIONS_NAME = "physical_feature_tandem_inverse_validation_predictions.csv"
TEST_PREDICTIONS_NAME = "physical_feature_tandem_inverse_test_predictions.csv"
MATERIAL_FILES = {
    "small_csv": "arm_source_n10000.csv",
    "large_csv": "arm_source_n20000.csv",
    "common_holdout": "fixed_common_holdout_manifest.json",
    "fixed_normalization": "declared_midpoint_half_range_normalization_contract.json",
}
MATERIAL_SUMMARY_NAME = "controlled_real10k_20k_nested_summary.json"
MATERIAL_RECEIPT_NAME = "controlled_real10k_20k_nested_receipt.json"
MATERIAL_QA_REQUIRED_NAME = "INDEPENDENT_QA_REQUIRED.json"
MATERIAL_SHA_INDEX_NAME = "SHA256SUMS.txt"
MATERIALIZATION_COMPLETE_SCHEMA = "controlled_real10k_20k_materialization_complete_v3"
MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA = (
    "controlled_real10k_20k_materialization_gate_manifest_v2"
)
MATERIALIZATION_GO_SCHEMA = "controlled_real10k_20k_materialization_exact_go_v2"
MATERIALIZATION_GO_SCOPE = "RESULT_BLIND_NESTED_10K_20K_MATERIALIZATION_ONLY"
MATERIALIZATION_CANDIDATE_AUTHORITIES = {
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
MATERIALIZATION_GO_AUTHORITIES = dict(MATERIALIZATION_CANDIDATE_AUTHORITIES)
MATERIALIZATION_GO_AUTHORITIES["result_blind_data_materialization"] = True
MATERIALIZATION_BOUND_ROLE_ORDER = (
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
PACKAGE_VERSION = "v5"
PACKAGE_MANIFEST_SCHEMA = "controlled_real10k_20k_mars_package_v2"
PACKAGE_RECEIPT_SCHEMA = "controlled_real10k_20k_mars_package_receipt_v2"
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
PACKAGE_BUILD_ATTEMPT_BODY_STATUS = "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA"
PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
PACKAGE_BUILD_ATTEMPT_COMMITTED_STATUS = (
    "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED"
)
PACKAGE_BUILD_ATTEMPT_BODY_NAME = "PACKAGE_BUILD_ATTEMPT_RECEIPT.json"
PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME = "PACKAGE_BUILD_ATTEMPT_COMMITTED.json"
PACKAGE_COMMIT_NAME = "PACKAGE_COMMIT.json"
PACKAGE_AUTHORITIES = {
    "native_linux_test_execution": False,
    "data_materialization": False,
    "training": False,
    "common_test_access": False,
    "numerical_metric_access": False,
    "fresh_emx": False,
    "process_signal": False,
}
PACKAGE_ATTEMPT_PUBLICATION = {
    "body_file_fsync": True,
    "attempt_root_fsync": True,
    "attempt_parent_fsync": True,
    "attempt_root_frozen": True,
    "continuity_verified": True,
    "terminal_inode_reserved_create_once_before_freeze": True,
    "terminal_bytes_published_after_durability": True,
    "post_commit_attempt_file_creation_permitted": False,
}
MATERIALIZATION_IMPLEMENTATION_ROLES = {
    "builder": "materialization_builder_code",
    "shared_contract": "shared_contract_code",
    "splitter_source": "splitter_code",
}
MATERIALIZATION_SOURCE_ROLES = {
    "historical_10k_csv": "historical_10k_csv",
    "authoritative_100k_csv": "authoritative_100k_csv",
    "historical_model_summary_json": "historical_model_summary_json",
}
MATERIALIZATION_PRODUCTION_EXACT_CHECK_KEYS = frozenset(
    {
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
)
FROZEN_HISTORICAL_10K_CSV_SHA256 = (
    "3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8"
)
FROZEN_AUTHORITATIVE_100K_CSV_SHA256 = (
    "68468eb2d3678aa0793157c1c647e975f60e8ec1673c259050ababe9fd1ff08a"
)
FROZEN_HISTORICAL_MODEL_SUMMARY_SHA256 = (
    "90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa"
)
FROZEN_AUTHORITATIVE_SOURCE_ROWS = 100_000
EXPECTED_COUNTS: dict[str, dict[str, int]] = {
    "small": {"source_rows": 10_000, "gradient_train": 7_871, "validation": 1_227, "test": 902},
    "large": {"source_rows": 20_000, "gradient_train": 17_871, "validation": 1_227, "test": 902},
}
ARM_ORDER = ("small", "large")
PYTHON_ISOLATION_FLAGS = ("-I", "-B", "-S")
CHILD_ENVIRONMENT_SCHEMA = "controlled_real10k_20k_exact_child_environment_v2"
THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
CHILD_ENVIRONMENT_KEYS = (
    "LC_ALL",
    "LANG",
    "TZ",
    *THREAD_ENV_KEYS,
    "OMP_DYNAMIC",
    "MKL_DYNAMIC",
)


class ControllerError(RuntimeError):
    """A fail-closed contract, provenance, authorization, or process gate failed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_token() -> str:
    return f"{time.time_ns()}_{os.getpid()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_fd(descriptor: int, label: str) -> str:
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        offset = 0
        while True:
            block = os.pread(descriptor, 1024 * 1024, offset)
            if not block:
                break
            digest.update(block)
            offset += len(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ControllerError(f"cannot hash held {label} descriptor") from exc
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mode, before.st_nlink)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mode, after.st_nlink)
        or offset != after.st_size
    ):
        raise ControllerError(f"held {label} descriptor changed during hash")
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _require_exact_json_equal(actual: Any, expected: Any, label: str) -> None:
    """Require recursive JSON equality without Python bool/int coercion."""

    def compare(left: Any, right: Any, location: str) -> None:
        if type(left) is not type(right):
            raise ControllerError(
                f"{label} exact JSON type mismatch at {location}: "
                f"{type(left).__name__} != {type(right).__name__}"
            )
        if isinstance(right, dict):
            if any(type(key) is not str for key in left) or any(
                type(key) is not str for key in right
            ):
                raise ControllerError(f"{label} has a non-string JSON key at {location}")
            if set(left) != set(right):
                raise ControllerError(f"{label} exact JSON keyset mismatch at {location}")
            for key in sorted(right):
                compare(left[key], right[key], f"{location}.{key}")
            return
        if isinstance(right, list):
            if len(left) != len(right):
                raise ControllerError(f"{label} exact JSON length mismatch at {location}")
            for index, (left_value, right_value) in enumerate(zip(left, right)):
                compare(left_value, right_value, f"{location}[{index}]")
            return
        if left != right:
            raise ControllerError(f"{label} exact JSON value mismatch at {location}")

    compare(actual, expected, "$")


def _json_int_is(value: Any, expected: int) -> bool:
    """Compare a decoded JSON integer without accepting ``bool`` or strings."""

    return type(value) is int and value == expected


def _json_float_is(value: Any, expected: float) -> bool:
    """Compare a decoded JSON float without numeric-type coercion."""

    return type(value) is float and math.isfinite(value) and value == expected


def _json_exact_is(actual: Any, expected: Any) -> bool:
    """Boolean form of the recursive, type-exact JSON comparator."""

    try:
        _require_exact_json_equal(actual, expected, "JSON value")
    except ControllerError:
        return False
    return True


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _effective_child_environment() -> dict[str, str]:
    """Build the complete trainer environment without consulting the parent.

    No ``PYTHON*`` startup customization, plugin setting, loader path, user
    directory, or caller-selected extra variable can cross the child process
    boundary.  The frozen venv executable is absolute, so PATH is unnecessary.
    Paired scientific seeds remain exact trainer argv values; isolated CPython
    intentionally ignores PYTHONHASHSEED.
    """

    environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        **{key: str(THREAD_LIMIT) for key in THREAD_ENV_KEYS},
        "OMP_DYNAMIC": "FALSE",
        "MKL_DYNAMIC": "FALSE",
    }
    if tuple(environment) != CHILD_ENVIRONMENT_KEYS:
        raise ControllerError("internal child environment key order is not frozen")
    if any(key.upper().startswith("PYTHON") for key in environment):
        raise ControllerError("PYTHON-prefixed child environment customization is forbidden")
    return environment


def _child_environment_sha256(environment: dict[str, str]) -> str:
    return _canonical_sha(
        {
            "schema": CHILD_ENVIRONMENT_SCHEMA,
            "environment": environment,
        }
    )


def _trainer_launch_contract() -> dict[str, Any]:
    environment = _effective_child_environment()
    return {
        "schema": "controlled_real10k_20k_isolated_trainer_launch_v1",
        "python_isolation_flags": list(PYTHON_ISOLATION_FLAGS),
        "parent_environment_inherited": False,
        "environment_allowlist_exact": list(CHILD_ENVIRONMENT_KEYS),
        "effective_environment": environment,
        "effective_environment_sha256": _child_environment_sha256(environment),
        "python_prefixed_environment_keys": [],
        "scientific_seed_transport": "exact_trainer_argv_seed_and_split_seed",
        "pythonhashseed_environment_used": False,
    }


def _require_exact_effective_environment(
    candidate: Any, contract: dict[str, Any]
) -> dict[str, str]:
    expected_launch = _trainer_launch_contract()
    actual_launch = (contract.get("process_contract") or {}).get("trainer_launch")
    if actual_launch != expected_launch:
        raise ControllerError("run contract isolated trainer launch environment is not exact")
    if not isinstance(candidate, dict) or candidate != expected_launch["effective_environment"]:
        raise ControllerError("trainer effective environment is not the exact allowlist")
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or key.upper().startswith("PYTHON")
        for key, value in candidate.items()
    ):
        raise ControllerError("trainer environment contains a non-string or PYTHON-prefixed entry")
    if _child_environment_sha256(candidate) != expected_launch["effective_environment_sha256"]:
        raise ControllerError("trainer effective environment SHA-256 is not exact")
    return dict(candidate)


def _is_sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _normalized_sha(value: Any, label: str) -> str:
    if not _is_sha(value):
        raise ControllerError(f"{label} is not a lowercase SHA-256: {value!r}")
    return value


def _file(raw: str | Path, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ControllerError(f"{label} is not a regular file: {path}")
    return path


def _directory(raw: str | Path, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.abspath(path))
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ControllerError(f"{label} is missing: {path}") from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ControllerError(f"{label} is not a non-symlink directory: {path}")
    return path


def _open_singleton_lock(
    raw: str | Path, expected_sha256: str
) -> tuple[int, dict[str, Any]]:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.abspath(path))
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ControllerError(f"controlled singleton lock is missing: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ControllerError("controlled singleton lock is not regular/nlink1/non-symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ControllerError("cannot open controlled singleton lock without following links") from exc
    try:
        held = os.fstat(descriptor)
        if (
            (held.st_dev, held.st_ino, held.st_size, held.st_mode, held.st_nlink)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mode, metadata.st_nlink)
            or not stat.S_ISREG(held.st_mode)
            or held.st_nlink != 1
        ):
            raise ControllerError("controlled singleton lock changed before descriptor pin")
        actual_sha = _sha256_fd(descriptor, "controlled singleton lock")
        wanted = _normalized_sha(expected_sha256, "controlled singleton lock")
        if actual_sha != wanted:
            raise ControllerError("controlled singleton lock SHA-256 mismatch")
        identity = {
            "schema": "controlled_real10k_20k_package_singleton_lock_v1",
            "path": str(path),
            "sha256": actual_sha,
            "size_bytes": held.st_size,
            "device": held.st_dev,
            "inode": held.st_ino,
            "nlink": held.st_nlink,
            "lock_mode": "flock_exclusive_nonblocking_held_controller_and_trainer_lifetime",
        }
        _verify_singleton_lock_descriptor(descriptor, identity)
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _verify_singleton_lock_descriptor(
    descriptor: int, expected: Mapping[str, Any]
) -> None:
    try:
        metadata = os.fstat(descriptor)
        path = Path(str(expected["path"]))
        path_metadata = path.lstat()
    except OSError as exc:
        raise ControllerError("cannot inspect held controlled singleton lock") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_dev != metadata.st_dev
        or path_metadata.st_ino != metadata.st_ino
        or metadata.st_dev != expected.get("device")
        or metadata.st_ino != expected.get("inode")
        or metadata.st_nlink != 1
        or metadata.st_size != expected.get("size_bytes")
        or _sha256_fd(descriptor, "controlled singleton lock") != expected.get("sha256")
    ):
        raise ControllerError("held controlled singleton lock identity changed")


def _python_executable(raw: str | Path) -> Path:
    """Preserve a venv's lexical executable path while pinning this runtime.

    Resolving a ``venv/bin/python`` symlink before spawning can silently bypass
    that venv.  The controller itself must therefore run under the supplied
    interpreter, while SHA checks continue to follow and pin its executable
    target.
    """

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.abspath(path))
    if not path.is_file():
        raise ControllerError(f"Python executable is missing/not a file: {path}")
    if not os.access(path, os.X_OK):
        raise ControllerError(f"Python executable is not executable: {path}")
    try:
        same_runtime = os.path.samefile(path, sys.executable)
    except OSError as exc:
        raise ControllerError(f"cannot identify Python executable: {path}") from exc
    if not same_runtime:
        raise ControllerError(
            "--python-executable must be the interpreter running this controller so the pinned NumPy runtime is exact"
        )
    return path


def _open_python_executable_descriptor(path: Path) -> tuple[int, dict[str, Any]]:
    try:
        resolved = path.resolve(strict=True)
        before = resolved.lstat()
    except OSError as exc:
        raise ControllerError("cannot resolve Python executable target") from exc
    if resolved.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise ControllerError("resolved Python executable target is not a regular non-symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ControllerError("cannot pin Python executable target without following links") from exc
    try:
        held = os.fstat(descriptor)
        if (
            (held.st_dev, held.st_ino, held.st_size, held.st_mode, held.st_nlink)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mode, before.st_nlink)
            or not stat.S_ISREG(held.st_mode)
            or held.st_nlink < 1
        ):
            raise ControllerError("Python executable target changed before descriptor pin")
        identity = {
            "path": str(path),
            "resolved_path_at_open": str(resolved),
            "sha256": _sha256_fd(descriptor, "Python executable"),
            "size_bytes": int(held.st_size),
            "device": int(held.st_dev),
            "inode": int(held.st_ino),
            "nlink": int(held.st_nlink),
            "execution_mode": "pinned_descriptor_procfd_executable_v1",
        }
        _verify_python_executable_descriptor(descriptor, identity)
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _verify_python_executable_descriptor(
    descriptor: int, expected: Mapping[str, Any]
) -> None:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ControllerError("cannot inspect held Python executable descriptor") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_dev != expected.get("device")
        or metadata.st_ino != expected.get("inode")
        or metadata.st_size != expected.get("size_bytes")
        or metadata.st_nlink != expected.get("nlink")
        or _sha256_fd(descriptor, "Python executable") != expected.get("sha256")
    ):
        raise ControllerError("held Python executable descriptor identity changed")


def _verify_python_path_binding(expected: Mapping[str, Any]) -> None:
    try:
        resolved = Path(str(expected["path"])).resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, KeyError) as exc:
        raise ControllerError("Python executable lexical path is no longer resolvable") from exc
    if (
        str(resolved) != expected.get("resolved_path_at_open")
        or metadata.st_dev != expected.get("device")
        or metadata.st_ino != expected.get("inode")
        or metadata.st_size != expected.get("size_bytes")
    ):
        raise ControllerError("Python executable lexical path changed after descriptor pin")


def _require_production_training_runtime(
    trainer_sha: str, python: Path, runtime: dict[str, Any]
) -> None:
    if _HELD_PYTHON_FD is None:
        raise ControllerError("pinned Python executable descriptor is not held")
    _verify_python_executable_descriptor(
        _HELD_PYTHON_FD, runtime["python"]
    )
    _verify_python_path_binding(runtime["python"])
    actual_python_sha = runtime["python"]["sha256"]
    actual_python_version = platform.python_version()
    actual_numpy_version = str(np.__version__)
    closure = runtime["descriptor_closure"]
    declared_python = closure["python"]
    declared_numpy = closure["numpy"]
    mismatches: list[str] = []
    if trainer_sha != PRODUCTION_TRAINER_SHA256:
        mismatches.append("trainer_sha256")
    if actual_python_sha != PRODUCTION_PYTHON_SHA256:
        mismatches.append("python_sha256")
    if actual_python_version != PRODUCTION_PYTHON_VERSION:
        mismatches.append("python_version")
    if actual_numpy_version != PRODUCTION_NUMPY_VERSION:
        mismatches.append("numpy_version")
    if declared_python.get("executable_sha256") != actual_python_sha:
        mismatches.append("closure_python_sha256")
    if declared_python.get("version") != actual_python_version:
        mismatches.append("closure_python_version")
    if declared_numpy.get("version") != actual_numpy_version:
        mismatches.append("closure_numpy_version")
    if closure["role_bindings"]["trainer_code"]["sha256"] != trainer_sha:
        mismatches.append("closure_trainer_sha256")
    if closure["role_bindings"]["runtime_bootstrap_code"]["sha256"] != closure[
        "bootstrap"
    ]["sha256"]:
        mismatches.append("closure_bootstrap_cross_binding")
    if mismatches:
        raise ControllerError(
            "production trainer/runtime hard identity mismatch: " + ",".join(mismatches)
        )


def _require_active_runtime(expected_manifest_sha256: str) -> dict[str, Any]:
    try:
        return runtime_bootstrap.require_active_runtime(
            "runner", expected_manifest_sha256
        )
    except runtime_bootstrap.RuntimeClosureError as exc:
        raise ControllerError(f"descriptor runtime startup is not exact: {exc}") from exc


def _require_sha(path: Path, expected: Any, label: str) -> str:
    wanted = _normalized_sha(expected, label)
    actual = _sha256(path)
    if actual != wanted:
        raise ControllerError(f"{label} SHA-256 mismatch: {actual} != {wanted}")
    return actual


def _strict_json_loads(raw: str, label: str) -> Any:
    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_nonfinite(raw: str) -> Any:
        raise ValueError(f"non-finite JSON constant: {raw}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControllerError(f"cannot parse {label} JSON: {exc}") from exc


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ControllerError(f"cannot read {label} JSON: {exc}") from exc
    value = _strict_json_loads(raw, label)
    if type(value) is not dict:
        raise ControllerError(f"{label} must be a JSON object")
    return value


def _json_fd(descriptor: int, label: str) -> dict[str, Any]:
    """Decode strict JSON from an already sealed regular-file descriptor."""

    try:
        metadata = os.fstat(descriptor)
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    except OSError as exc:
        raise ControllerError(f"cannot read held {label} JSON: {exc}") from exc
    if len(raw) != metadata.st_size:
        raise ControllerError(f"held {label} JSON size changed while reading")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ControllerError(f"cannot decode held {label} JSON: {exc}") from exc
    value = _strict_json_loads(decoded, label)
    if type(value) is not dict:
        raise ControllerError(f"{label} must be a JSON object")
    return value


def _write_json_x(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record_event(out_dir: Path, kind: str, reason: str, **details: Any) -> Path:
    path = out_dir / "receipts" / "events" / f"{kind}_{_event_token()}.json"
    payload = {
        "schema": "controlled_real10k_20k_machine_event_v1",
        "generated_utc": _utc_now(),
        "kind": kind,
        "reason": reason,
        "details": details,
    }
    _write_json_x(path, payload)
    return path


def _parse_seeds(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid comma-separated seeds: {exc}") from exc
    if values != EXACT_PAIRED_SEEDS:
        raise argparse.ArgumentTypeError(
            f"paired seeds must be exactly {','.join(str(value) for value in EXACT_PAIRED_SEEDS)} in order"
        )
    return values


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "execute"), default="prepare")
    parser.add_argument("--materialization-summary", required=True)
    parser.add_argument("--expected-materialization-summary-sha256", required=True)
    parser.add_argument("--materialization-complete-receipt", required=True)
    parser.add_argument("--expected-materialization-complete-receipt-sha256", required=True)
    parser.add_argument("--trainer", required=True)
    parser.add_argument("--expected-trainer-sha256", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--runtime-bootstrap", required=True)
    parser.add_argument("--expected-runtime-bootstrap-sha256", required=True)
    parser.add_argument("--runtime-closure-json", required=True)
    parser.add_argument("--expected-runtime-closure-json-sha256", required=True)
    parser.add_argument("--runtime-closure-tree", required=True)
    parser.add_argument("--controlled-singleton-lock", required=True)
    parser.add_argument("--expected-controlled-singleton-lock-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--seeds",
        type=_parse_seeds,
        default=EXACT_PAIRED_SEEDS,
        help="Frozen exact list; any omission, addition, or reordering is rejected.",
    )
    parser.add_argument("--independent-qa-go-receipt")
    parser.add_argument("--expected-independent-qa-go-receipt-sha256")
    args = parser.parse_args(argv)
    for name in (
        "expected_materialization_summary_sha256",
        "expected_materialization_complete_receipt_sha256",
        "expected_trainer_sha256",
        "expected_runtime_bootstrap_sha256",
        "expected_runtime_closure_json_sha256",
        "expected_controlled_singleton_lock_sha256",
    ):
        try:
            setattr(args, name, _normalized_sha(getattr(args, name), "--" + name.replace("_", "-")))
        except ControllerError as exc:
            parser.error(str(exc))
    if tuple(args.seeds) != EXACT_PAIRED_SEEDS:
        parser.error("paired seeds are not the exact frozen three-seed sequence")
    go_values = (args.independent_qa_go_receipt, args.expected_independent_qa_go_receipt_sha256)
    if args.phase == "prepare" and any(go_values):
        parser.error("prepare phase does not accept an independent-QA GO receipt")
    if args.phase == "execute" and not all(go_values):
        parser.error("execute phase requires the external GO receipt and its expected SHA-256")
    if args.phase == "execute":
        try:
            args.expected_independent_qa_go_receipt_sha256 = _normalized_sha(
                args.expected_independent_qa_go_receipt_sha256,
                "--expected-independent-qa-go-receipt-sha256",
            )
        except ControllerError as exc:
            parser.error(str(exc))
    return args


def _parse_sha_index(path: Path, root: Path, label: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if "  " not in line:
            raise ControllerError(f"{label} has malformed line {line_number}")
        expected, relative = line.split("  ", 1)
        expected = _normalized_sha(expected, f"{label} line {line_number}")
        if not relative or relative in records or Path(relative).is_absolute():
            raise ControllerError(f"{label} has invalid/duplicate path at line {line_number}")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ControllerError(f"{label} path escapes root: {relative}") from exc
        candidate = _file(candidate, f"{label} artifact")
        _require_sha(candidate, expected, f"{label} artifact {relative}")
        records[relative] = {"path": str(candidate), "sha256": expected, "size_bytes": candidate.stat().st_size}
    if not records:
        raise ControllerError(f"{label} is empty")
    return records


def _artifact_binding(record: Any, summary_path: Path, label: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ControllerError(f"{label} artifact binding is not an object")
    raw = record.get("path")
    if not isinstance(raw, str) or not raw:
        raise ControllerError(f"{label} artifact path is missing")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = summary_path.parent / candidate
    path = _file(candidate, label)
    sha = _require_sha(path, record.get("sha256"), label)
    if "size_bytes" in record and not _json_int_is(
        record["size_bytes"], path.stat().st_size
    ):
        raise ControllerError(f"{label} size binding is incorrect")
    return {"path": str(path), "sha256": sha, "size_bytes": path.stat().st_size}


def _exact_geometry(values: Sequence[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_float64_v1",
        "columns": list(GEOMETRY_COLUMNS),
        "values": [format(float(value), ".17g") for value in values],
    }
    return _canonical_sha(payload)


def _decimal12(value: float) -> str:
    token = format(float(value), ".12f")
    return "0.000000000000" if token == "-0.000000000000" else token


def _portable_geometry(values: Sequence[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_decimal12_v1",
        "columns": list(GEOMETRY_COLUMNS),
        "values": [_decimal12(value) for value in values],
    }
    return _canonical_sha(payload)


def _line_set_sha(values: Iterable[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in sorted(values)).encode("ascii")).hexdigest()


def _audit_csv(path: Path, arm: str) -> dict[str, Any]:
    expected = EXPECTED_COUNTS[arm]
    rows: list[tuple[str, ...]] = []
    identities: dict[str, dict[str, Any]] = {}
    source_rows: set[int] = set()
    touchstones: set[str] = set()
    portable_set: set[str] = set()
    cell_split: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or []) != OUTPUT_COLUMNS:
            raise ControllerError(f"{arm} CSV schema/order does not match shared OUTPUT_COLUMNS")
        for line_number, row in enumerate(reader, start=2):
            canonical_row = tuple(str(row[column]) for column in OUTPUT_COLUMNS)
            rows.append(canonical_row)
            try:
                source_row = int(row["controlled_source_row_number"])
                inputs = tuple(float(row[column]) for column in INPUT_COLUMNS)
                geometry = tuple(float(row[column]) for column in GEOMETRY_COLUMNS)
            except (TypeError, ValueError) as exc:
                raise ControllerError(f"{arm} CSV row {line_number} has invalid numeric fields") from exc
            if source_row < 1 or source_row in source_rows:
                raise ControllerError(f"{arm} CSV has duplicate/non-positive source row identity")
            source_rows.add(source_row)
            if any(not math.isfinite(value) for value in inputs + geometry):
                raise ControllerError(f"{arm} CSV row {line_number} has non-finite values")
            if any(value < low or value > high for value, low, high in zip(inputs, INPUT_LOWER, INPUT_UPPER)):
                raise ControllerError(f"{arm} CSV row {line_number} lies outside frozen input bounds")
            if any(
                value < low or value > high
                for value, low, high in zip(geometry, GEOMETRY_LOWER, GEOMETRY_UPPER)
            ):
                raise ControllerError(f"{arm} CSV row {line_number} lies outside frozen geometry bounds")
            cell = canonical_physical_cell_id(inputs, bins=PHYSICAL_CELL_BINS)
            if row["controlled_physical_cell_4d"] != cell or ":" not in cell:
                raise ControllerError(f"{arm} CSV row {line_number} has noncanonical colon cell identity")
            split_name = row["controlled_split_assignment"]
            if split_name not in {"train", "validation", "test"}:
                raise ControllerError(f"{arm} CSV row {line_number} has invalid split assignment")
            previous_split = cell_split.setdefault(cell, split_name)
            if previous_split != split_name:
                raise ControllerError(f"{arm} physical cell appears in more than one split")
            identity = row["canonical_geometry_identity_sha256"].strip().lower()
            portable = row["portable_geometry_decimal12_sha256"].strip().lower()
            touchstone = row["touchstone_sha256"].strip().lower()
            if identity != _exact_geometry(geometry) or portable != _portable_geometry(geometry):
                raise ControllerError(f"{arm} CSV row {line_number} geometry identity mismatch")
            if not _is_sha(touchstone) or touchstone in touchstones:
                raise ControllerError(f"{arm} CSV has invalid/duplicate Touchstone identity")
            if identity in identities or portable in portable_set:
                raise ControllerError(f"{arm} CSV has duplicate geometry identity")
            if not row["evaluation"].strip() or not row["touchstone_path"].strip():
                raise ControllerError(f"{arm} CSV row {line_number} lacks real-EMX provenance")
            touchstones.add(touchstone)
            portable_set.add(portable)
            identities[identity] = {
                "portable": portable,
                "touchstone": touchstone,
                "split": split_name,
                "cell": cell,
            }
    if len(rows) != expected["source_rows"]:
        raise ControllerError(f"{arm} source row count is not exact")
    actual_split = {
        name: sum(record["split"] == name for record in identities.values())
        for name in ("train", "validation", "test")
    }
    wanted_split = {name: expected[name if name != "train" else "gradient_train"] for name in actual_split}
    if actual_split != wanted_split:
        raise ControllerError(f"{arm} split row counts are not exact: {actual_split}")
    split_cells = {
        name: sorted(cell for cell, split_name in cell_split.items() if split_name == name)
        for name in ("train", "validation", "test")
    }
    if any(set(split_cells[left]) & set(split_cells[right]) for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))):
        raise ControllerError(f"{arm} complete-cell partitions overlap")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "rows": rows,
        "identities": identities,
        "identity_set_sha256": _line_set_sha(identities),
        "split_cells": split_cells,
        "split_counts": actual_split,
    }


def _exact_vector(payload: dict[str, Any], key: str, expected: Sequence[float]) -> None:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise ControllerError(f"normalization {key} is not a list")
    try:
        actual = tuple(float(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(f"normalization {key} is not numeric") from exc
    if actual != tuple(float(value) for value in expected):
        raise ControllerError(f"normalization {key} is not the exact shared vector")


def _audit_normalization(path: Path) -> dict[str, Any]:
    payload = _json(path, "fixed normalization")
    if payload.get("schema") != NORMALIZATION_SCHEMA:
        raise ControllerError("fixed normalization schema mismatch")
    if payload.get("input_columns") != list(INPUT_COLUMNS) or payload.get("geometry_columns") != list(GEOMETRY_COLUMNS):
        raise ControllerError("fixed normalization column order mismatch")
    input_midpoint = tuple(0.5 * (low + high) for low, high in zip(INPUT_LOWER, INPUT_UPPER))
    input_half = tuple(0.5 * (high - low) for low, high in zip(INPUT_LOWER, INPUT_UPPER))
    geometry_midpoint = tuple(0.5 * (low + high) for low, high in zip(GEOMETRY_LOWER, GEOMETRY_UPPER))
    geometry_half = tuple(0.5 * (high - low) for low, high in zip(GEOMETRY_LOWER, GEOMETRY_UPPER))
    for key, expected in (
        ("input_lower", INPUT_LOWER),
        ("input_upper", INPUT_UPPER),
        ("geometry_lower", GEOMETRY_LOWER),
        ("geometry_upper", GEOMETRY_UPPER),
        ("input_midpoint", input_midpoint),
        ("input_half_range", input_half),
        ("geometry_midpoint", geometry_midpoint),
        ("geometry_half_range", geometry_half),
    ):
        _exact_vector(payload, key, expected)
    required_flags = {
        "train_arm_specific_statistics_used": False,
        "large_arm_empirical_statistics_used": False,
        "all_loaded_rows_required_inside_declared_bounds": True,
    }
    if any(payload.get(key) is not value for key, value in required_flags.items()):
        raise ControllerError("fixed normalization result-blind flags are not exact")
    return {"path": str(path), "sha256": _sha256(path), "schema": NORMALIZATION_SCHEMA}


def _identity_list(payload: dict[str, Any], key: str, expected_count: int) -> list[str]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise ControllerError(f"holdout {key} is not a list")
    values = [str(value).strip().lower() for value in raw]
    if len(values) != expected_count or len(values) != len(set(values)) or any(not _is_sha(value) for value in values):
        raise ControllerError(f"holdout {key} identity list is not exact")
    return values


def _audit_holdout(path: Path, small: dict[str, Any], large: dict[str, Any], historical_sha: str, shared_sha: str) -> dict[str, Any]:
    payload = _json(path, "common holdout")
    if payload.get("schema") != HOLDOUT_SCHEMA or payload.get("identity_kind") != "canonical_geometry_sha256":
        raise ControllerError("common holdout schema/identity kind mismatch")
    exact_scalars = {
        "historical_model_summary_sha256": historical_sha,
        "shared_contract_sha256": shared_sha,
        "selection_method": "exact_historical_physical_cell_grouped_split_reconstruction",
        "selection_uses_model_results": False,
        "stratification": ["physical_cell_4d"],
        "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
        "physical_cell_bins": PHYSICAL_CELL_BINS,
        "physical_lower": list(INPUT_LOWER),
        "physical_upper": list(INPUT_UPPER),
    }
    if any(payload.get(key) != value for key, value in exact_scalars.items()):
        raise ControllerError("common holdout historical/shared/bounds/result-blind contract mismatch")
    validation = _identity_list(payload, "validation_geometry_identities", EXPECTED_COUNTS["small"]["validation"])
    test = _identity_list(payload, "test_geometry_identities", EXPECTED_COUNTS["small"]["test"])
    if not _json_int_is(payload.get("validation_count"), len(validation)) or not _json_int_is(
        payload.get("test_count"), len(test)
    ):
        raise ControllerError("holdout declared validation/test counts differ from identity lists")
    validation_set, test_set = set(validation), set(test)
    if validation_set & test_set:
        raise ControllerError("holdout validation/test identities overlap")
    fingerprint = hashlib.sha256(
        "".join(
            [f"validation\0{value}\n" for value in sorted(validation_set)]
            + [f"test\0{value}\n" for value in sorted(test_set)]
        ).encode("ascii")
    ).hexdigest()
    if payload.get("common_holdout_fingerprint_sha256") != fingerprint:
        raise ControllerError("holdout fingerprint mismatch")
    for arm_name, arm in (("small", small), ("large", large)):
        actual_validation = {identity for identity, row in arm["identities"].items() if row["split"] == "validation"}
        actual_test = {identity for identity, row in arm["identities"].items() if row["split"] == "test"}
        if actual_validation != validation_set or actual_test != test_set:
            raise ControllerError(f"{arm_name} table does not reproduce common holdout identities")
    expected_validation_portable = sorted(
        small["identities"][identity]["portable"] for identity in validation_set
    )
    expected_test_portable = sorted(small["identities"][identity]["portable"] for identity in test_set)
    if (
        payload.get("validation_portable_decimal12_geometry_identities")
        != expected_validation_portable
        or payload.get("test_portable_decimal12_geometry_identities") != expected_test_portable
    ):
        raise ControllerError("holdout portable validation/test identities differ from CSV")
    for name in ("train", "validation", "test"):
        if payload.get(f"{name}_cell_ids") != small["split_cells"][name]:
            raise ControllerError(f"holdout {name} cell list mismatch")
        if large["split_cells"][name] != small["split_cells"][name]:
            raise ControllerError(f"large {name} cell list differs from historical arm")
    isolation = payload.get("complete_cell_isolation") or {}
    if isolation.get("every_cell_assigned_to_exactly_one_split") is not True or not _json_int_is(
        isolation.get("train_validation_test_cell_overlap_count"), 0
    ):
        raise ControllerError("holdout complete-cell isolation proof failed")
    return {"path": str(path), "sha256": _sha256(path), "fingerprint_sha256": fingerprint}


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


def _exact_bound_file(record: Any, label: str) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        raise ControllerError(f"{label} binding keyset is not exact")
    path = _file(record.get("path", ""), label)
    sha = _require_sha(path, record.get("sha256"), label)
    return {"path": str(path), "sha256": sha}


def _audit_live_directory_binding(
    record: Any,
    label: str,
    *,
    expected_path: Path | None = None,
    expected_mode: str | None = None,
) -> Path:
    if (
        type(record) is not dict
        or set(record) != {"path", "st_dev", "st_ino", "mode_octal"}
        or type(record.get("path")) is not str
        or any(type(record.get(key)) is not int for key in ("st_dev", "st_ino"))
        or type(record.get("mode_octal")) is not str
        or len(record["mode_octal"]) != 4
        or any(character not in "01234567" for character in record["mode_octal"])
    ):
        raise ControllerError(f"{label} directory binding is not exact")
    path = _directory(record["path"], label)
    metadata = path.lstat()
    if (
        metadata.st_dev != record["st_dev"]
        or metadata.st_ino != record["st_ino"]
        or f"{stat.S_IMODE(metadata.st_mode):04o}" != record["mode_octal"]
    ):
        raise ControllerError(f"{label} directory identity drifted")
    if expected_path is not None and path != Path(os.path.abspath(expected_path)):
        raise ControllerError(f"{label} directory path differs from the bound package transaction")
    if expected_mode is not None and record["mode_octal"] != expected_mode:
        raise ControllerError(f"{label} directory mode is not {expected_mode}")
    return path


@contextlib.contextmanager
def _hold_exact_package_attempt_root(
    attempt_root: Path,
    root_binding: Any,
    member_records: Mapping[str, Any],
):
    """Hold and reverify the immutable two-file attempt-root closure."""

    expected_names = {
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
    }
    if set(member_records) != expected_names:
        raise ControllerError("package build-attempt member set is not exact")
    bound_root = _audit_live_directory_binding(
        root_binding,
        "package build-attempt attempt root",
        expected_path=attempt_root,
        expected_mode="0555",
    )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_fd = os.open(bound_root, directory_flags)
    except OSError as exc:
        raise ControllerError("cannot descriptor-pin package build-attempt root") from exc
    member_fds: dict[str, int] = {}
    initial_member_identity: dict[str, tuple[int, int, int, int, int]] = {}
    try:
        root_before = os.fstat(root_fd)
        named_before = bound_root.lstat()
        root_identity = (
            root_before.st_dev,
            root_before.st_ino,
            root_before.st_mode,
            root_before.st_nlink,
        )
        if (
            not stat.S_ISDIR(root_before.st_mode)
            or (named_before.st_dev, named_before.st_ino, named_before.st_mode)
            != (root_before.st_dev, root_before.st_ino, root_before.st_mode)
            or root_before.st_dev != root_binding["st_dev"]
            or root_before.st_ino != root_binding["st_ino"]
            or f"{stat.S_IMODE(root_before.st_mode):04o}" != root_binding["mode_octal"]
        ):
            raise ControllerError("package build-attempt root changed before descriptor pin")
        if set(os.listdir(root_fd)) != expected_names or len(os.listdir(root_fd)) != 2:
            raise ControllerError(
                "package build-attempt root closure is not exactly BODY + COMMITTED"
            )
        member_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for name in sorted(expected_names):
            record = member_records[name]
            if type(record) is not dict or Path(str(record.get("path", ""))) != bound_root / name:
                raise ControllerError(f"package build-attempt member path is invalid: {name}")
            try:
                descriptor = os.open(name, member_flags, dir_fd=root_fd)
            except OSError as exc:
                raise ControllerError(
                    f"cannot descriptor-pin package build-attempt member: {name}"
                ) from exc
            member_fds[name] = descriptor
            metadata = os.fstat(descriptor)
            named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mode,
                metadata.st_nlink,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or identity
                != (named.st_dev, named.st_ino, named.st_size, named.st_mode, named.st_nlink)
                or metadata.st_dev != record.get("st_dev")
                or metadata.st_ino != record.get("st_ino")
                or metadata.st_size != record.get("size_bytes")
                or metadata.st_nlink != record.get("nlink")
                or f"{stat.S_IMODE(metadata.st_mode):04o}" != record.get("mode_octal")
                or _sha256_fd(descriptor, f"package build-attempt {name}")
                != record.get("sha256")
            ):
                raise ControllerError(f"package build-attempt held member drifted: {name}")
            initial_member_identity[name] = identity

        yield member_fds

        names_after = os.listdir(root_fd)
        if set(names_after) != expected_names or len(names_after) != 2:
            raise ControllerError("package build-attempt root closure changed during audit")
        for name, descriptor in member_fds.items():
            metadata = os.fstat(descriptor)
            named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mode,
                metadata.st_nlink,
            )
            if (
                identity != initial_member_identity[name]
                or identity
                != (named.st_dev, named.st_ino, named.st_size, named.st_mode, named.st_nlink)
                or _sha256_fd(descriptor, f"package build-attempt {name}")
                != member_records[name].get("sha256")
            ):
                raise ControllerError(f"package build-attempt member changed during audit: {name}")
        root_after = os.fstat(root_fd)
        named_after = bound_root.lstat()
        if (
            (root_after.st_dev, root_after.st_ino, root_after.st_mode, root_after.st_nlink)
            != root_identity
            or (named_after.st_dev, named_after.st_ino, named_after.st_mode)
            != (root_after.st_dev, root_after.st_ino, root_after.st_mode)
        ):
            raise ControllerError("package build-attempt root changed during audit")
    except OSError as exc:
        raise ControllerError("package build-attempt descriptor audit failed") from exc
    finally:
        for descriptor in member_fds.values():
            os.close(descriptor)
        os.close(root_fd)


def _audit_package_build_attempt(bindings: Mapping[str, Any]) -> None:
    """Require one descriptor-sealed package-v5 BODY + COMMITTED authority."""

    if type(bindings) is not dict or not {
        "package_build_attempt_body",
        "package_build_attempt_committed",
    }.issubset(bindings):
        raise ControllerError("package build-attempt materialization bindings are missing")
    body_record = bindings["package_build_attempt_body"]
    committed_record = bindings["package_build_attempt_committed"]
    if type(body_record) is not dict or type(committed_record) is not dict:
        raise ControllerError("package build-attempt materialization bindings are invalid")
    body_path = Path(str(body_record.get("path", "")))
    committed_path = Path(str(committed_record.get("path", "")))
    if (
        body_path.name != PACKAGE_BUILD_ATTEMPT_BODY_NAME
        or committed_path.name != PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
        or body_path.parent != committed_path.parent
        or body_path.is_symlink()
        or committed_path.is_symlink()
    ):
        raise ControllerError("package build-attempt BODY/COMMITTED paths are not exact")
    committed_preview = _json(committed_path, "package build-attempt committed marker preview")
    with _hold_exact_package_attempt_root(
        body_path.parent,
        committed_preview.get("attempt_root"),
        {
            PACKAGE_BUILD_ATTEMPT_BODY_NAME: body_record,
            PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME: committed_record,
        },
    ) as held_descriptors:
        _audit_package_build_attempt_held(bindings, held_descriptors)


def _audit_package_build_attempt_held(
    bindings: Mapping[str, Any], held_descriptors: Mapping[str, int]
) -> None:
    """Require package-v5 BODY + durable COMMITTED as one transmitted authority."""

    body_record = bindings["package_build_attempt_body"]
    committed_record = bindings["package_build_attempt_committed"]
    body_path = Path(str(body_record["path"]))
    committed_path = Path(str(committed_record["path"]))
    if (
        body_path.name != PACKAGE_BUILD_ATTEMPT_BODY_NAME
        or committed_path.name != PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
        or body_path.parent != committed_path.parent
        or body_path.is_symlink()
        or committed_path.is_symlink()
    ):
        raise ControllerError("package build-attempt BODY/COMMITTED paths are not exact")

    body = _json_fd(
        held_descriptors[PACKAGE_BUILD_ATTEMPT_BODY_NAME],
        "package build-attempt body",
    )
    body_keys = {
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
    }
    invocation_keys = {
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
    observed_keys = {
        "package_spec_sha256",
        "builder_sha256",
        "package_output_device",
        "package_output_inode",
    }
    package_keys = {
        "path",
        "manifest_sha256",
        "receipt_sha256",
        "independent_qa_required_sha256",
        "sha256sums_sha256",
        "package_commit_sha256",
        "file_count",
    }
    invocation = body.get("invocation")
    observed = body.get("observed_identity")
    package = body.get("package")
    if (
        set(body) != body_keys
        or body.get("schema") != PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA
        or body.get("status") != PACKAGE_BUILD_ATTEMPT_BODY_STATUS
        or type(body.get("started_utc")) is not str
        or not body["started_utc"]
        or type(body.get("completed_utc")) is not str
        or not body["completed_utc"]
        or type(invocation) is not dict
        or set(invocation) != invocation_keys
        or type(observed) is not dict
        or set(observed) != observed_keys
        or not _is_sha(observed.get("package_spec_sha256"))
        or not _is_sha(observed.get("builder_sha256"))
        or type(observed.get("package_output_device")) is not int
        or type(observed.get("package_output_inode")) is not int
        or type(package) is not dict
        or set(package) != package_keys
        or type(package.get("path")) is not str
        or any(
            not _is_sha(package.get(key))
            for key in (
                "manifest_sha256",
                "receipt_sha256",
                "independent_qa_required_sha256",
                "sha256sums_sha256",
                "package_commit_sha256",
            )
        )
        or type(package.get("file_count")) is not int
        or package["file_count"] <= 0
        or body.get("partial_output_preserved") is not False
        or body.get("execution_authorized") is not False
    ):
        raise ControllerError("package build-attempt body contract is not exact")
    _require_exact_json_equal(
        body.get("authorities"), PACKAGE_AUTHORITIES, "package build-attempt body authorities"
    )

    package_root = _directory(package["path"], "package-v5 root")
    attempt_root = body_path.parent
    attempt_parent = attempt_root.parent
    if (
        Path(str(invocation.get("output_dir"))) != package_root
        or Path(str(invocation.get("failure_receipt_dir"))) != attempt_root
    ):
        raise ControllerError("package build-attempt invocation paths differ from the body")

    committed = _json_fd(
        held_descriptors[PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME],
        "package build-attempt committed marker",
    )
    committed_keys = {
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
    expected_body_binding = {
        "path": str(body_path),
        "sha256": body_record["sha256"],
        "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
        "status": PACKAGE_BUILD_ATTEMPT_BODY_STATUS,
    }
    package_commit_binding = committed.get("package_commit")
    if (
        set(committed) != committed_keys
        or committed.get("schema") != PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA
        or committed.get("status") != PACKAGE_BUILD_ATTEMPT_COMMITTED_STATUS
        or type(committed.get("committed_utc")) is not str
        or not committed["committed_utc"]
        or not _json_exact_is(committed.get("body"), expected_body_binding)
        or type(package_commit_binding) is not dict
        or set(package_commit_binding) != {"path", "sha256", "schema", "status"}
        or package_commit_binding.get("schema") != PACKAGE_COMMIT_SCHEMA
        or package_commit_binding.get("status") != PACKAGE_COMMIT_STATUS
        or package_commit_binding.get("sha256") != package["package_commit_sha256"]
        or committed.get("execution_authorized") is not False
    ):
        raise ControllerError("package build-attempt committed marker is not exact")
    _require_exact_json_equal(
        committed.get("authorities"),
        PACKAGE_AUTHORITIES,
        "package build-attempt committed authorities",
    )
    _require_exact_json_equal(
        committed.get("publication"),
        PACKAGE_ATTEMPT_PUBLICATION,
        "package build-attempt durable publication",
    )
    _audit_live_directory_binding(
        committed.get("package_root"),
        "package build-attempt package root",
        expected_path=package_root,
        expected_mode="0555",
    )
    _audit_live_directory_binding(
        committed.get("attempt_root"),
        "package build-attempt attempt root",
        expected_path=attempt_root,
        expected_mode="0555",
    )
    _audit_live_directory_binding(
        committed.get("attempt_parent"),
        "package build-attempt attempt parent",
        expected_path=attempt_parent,
    )
    if (
        observed["package_output_device"] != committed["package_root"]["st_dev"]
        or observed["package_output_inode"] != committed["package_root"]["st_ino"]
    ):
        raise ControllerError("package body and commit marker package-root identities differ")

    commit_path = _file(package_commit_binding.get("path", ""), "package-v5 commit")
    if (
        commit_path != package_root / PACKAGE_COMMIT_NAME
        or _require_sha(commit_path, package_commit_binding.get("sha256"), "package-v5 commit")
        != package["package_commit_sha256"]
    ):
        raise ControllerError("package-v5 commit path/SHA differs from the attempt body")
    commit = _json(commit_path, "package-v5 commit")
    commit_keys = {
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
    }
    package_member_contract = (
        ("manifest", "MANIFEST.json", "manifest_sha256"),
        ("receipt", "RECEIPT.json", "receipt_sha256"),
        (
            "independent_qa_required",
            "INDEPENDENT_QA_REQUIRED.json",
            "independent_qa_required_sha256",
        ),
        ("sha256sums", "SHA256SUMS.txt", "sha256sums_sha256"),
    )
    if (
        set(commit) != commit_keys
        or commit.get("schema") != PACKAGE_COMMIT_SCHEMA
        or commit.get("status") != PACKAGE_COMMIT_STATUS
        or commit.get("package_version") != PACKAGE_VERSION
        or commit.get("execution_authorized") is not False
        or not _json_exact_is(
            commit.get("required_external_pass_attempt"),
            {
                "body": {
                    "path": str(body_path),
                    "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
                    "status": PACKAGE_BUILD_ATTEMPT_BODY_STATUS,
                },
                "committed": {
                    "path": str(committed_path),
                    "schema": PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
                    "status": PACKAGE_BUILD_ATTEMPT_COMMITTED_STATUS,
                },
            },
        )
        or not _json_exact_is(
            commit.get("creation_order_contract"),
            {
                "this_member_created_last": True,
                "post_commit_package_file_creation_permitted": False,
            },
        )
    ):
        raise ControllerError("package-v5 commit contract is not exact")
    _require_exact_json_equal(
        commit.get("authorities"), PACKAGE_AUTHORITIES, "package-v5 commit authorities"
    )
    package_members: dict[str, Path] = {}
    for key, filename, body_sha_key in package_member_contract:
        record = commit.get(key)
        if (
            type(record) is not dict
            or set(record) != {"path", "sha256"}
            or record.get("path") != filename
            or record.get("sha256") != package[body_sha_key]
        ):
            raise ControllerError(f"package-v5 commit member binding is invalid: {key}")
        member = _file(package_root / filename, f"package-v5 {key}")
        _require_sha(member, record["sha256"], f"package-v5 {key}")
        package_members[key] = member

    manifest = _json(package_members["manifest"], "package-v5 manifest")
    receipt = _json(package_members["receipt"], "package-v5 receipt")
    qa_required = _json(
        package_members["independent_qa_required"], "package-v5 independent-QA record"
    )
    if (
        manifest.get("schema") != PACKAGE_MANIFEST_SCHEMA
        or manifest.get("package_version") != PACKAGE_VERSION
        or receipt.get("schema") != PACKAGE_RECEIPT_SCHEMA
        or receipt.get("package_version") != PACKAGE_VERSION
        or qa_required.get("schema") != PACKAGE_QA_REQUIRED_SCHEMA
    ):
        raise ControllerError("package-v5 manifest/receipt/QA interface is stale")

    expected_singleton_contract = package_root / "runtime/contracts/PROCESS_SINGLETON_CONTRACT.json"
    expected_singleton_lock = package_root / "CONTROLLED_SINGLETON.lock"
    if (
        Path(str(bindings["package_process_singleton_contract"]["path"]))
        != expected_singleton_contract
        or Path(str(bindings["package_singleton_lock"]["path"])) != expected_singleton_lock
    ):
        raise ControllerError("materialization singleton bindings are outside package-v5 root")


def _audit_materialization_outer_complete(
    complete_path: Path,
    expected_complete_sha: str,
    *,
    summary_path: Path,
    material_index: Path,
    implementation: dict[str, Any],
    sources: dict[str, Any],
) -> dict[str, Any]:
    complete_sha = _require_sha(
        complete_path,
        expected_complete_sha,
        "outer materialization COMPLETE receipt",
    )
    complete = _json(complete_path, "outer materialization COMPLETE receipt")
    exact_complete_keys = {
        "schema",
        "generated_utc",
        "status",
        "candidate_manifest_sha256",
        "candidate_sha256sums_sha256",
        "go_sha256",
        "challenge_nonce",
        "candidate_manifest",
        "candidate_sha_index",
        "materialization_go_authority",
        "materialization_output",
        "materialization_validation",
        "frozen_closure_after_materialization",
        "sealed_runtime",
        "execution_precursor_closure",
        "retry_authorized",
        "training_authorized",
        "evaluation_authorized",
        "common_test_access_authorized",
        "numerical_metric_access_authorized",
        "fresh_emx_authorized",
        "emx_generation_authorized",
        "process_signal_sent",
        "subprocess_spawned",
        "next_legal_gate",
    }
    if set(complete) != exact_complete_keys:
        raise ControllerError("outer materialization COMPLETE top-level keyset is not exact")
    exact_boundary = {
        "schema": MATERIALIZATION_COMPLETE_SCHEMA,
        "status": "COMPLETE_RESULT_BLIND_MATERIALIZATION_DEEP_VALIDATED",
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
    _require_exact_json_equal(
        {key: complete.get(key) for key in exact_boundary},
        exact_boundary,
        "outer materialization COMPLETE status/authority boundary",
    )

    candidate_manifest = _exact_bound_file(
        complete.get("candidate_manifest"), "materialization candidate manifest"
    )
    candidate_index = _exact_bound_file(
        complete.get("candidate_sha_index"), "materialization candidate SHA index"
    )
    material_go = _exact_bound_file(
        complete.get("materialization_go_authority"), "materialization GO authority copy"
    )
    if (
        complete.get("candidate_manifest_sha256") != candidate_manifest["sha256"]
        or complete.get("candidate_sha256sums_sha256") != candidate_index["sha256"]
        or complete.get("go_sha256") != material_go["sha256"]
    ):
        raise ControllerError("outer COMPLETE scalar identities differ from bound candidate/GO bytes")

    candidate_root = Path(candidate_manifest["path"]).parent
    if Path(candidate_index["path"]).parent != candidate_root:
        raise ControllerError("candidate manifest and SHA index roots differ")
    candidate_index_records = _parse_sha_index(
        Path(candidate_index["path"]), candidate_root, "materialization candidate SHA index"
    )
    if list(candidate_index_records) != [
        "MANIFEST.json",
        "INDEPENDENT_QA_REQUIRED.json",
        "PREPARED_RECEIPT.json",
    ]:
        raise ControllerError("materialization candidate SHA index entry order is not exact")
    if (
        candidate_index_records["MANIFEST.json"]["sha256"]
        != candidate_manifest["sha256"]
    ):
        raise ControllerError("candidate SHA index does not bind the exact manifest")

    manifest = _json(Path(candidate_manifest["path"]), "materialization candidate manifest")
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
        raise ControllerError("materialization candidate manifest keyset is not exact")
    if (
        manifest.get("schema") != MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA
        or manifest.get("status") != "PREPARED_IMMUTABLE_RESULT_BLIND_NO_AUTHORITY"
        or manifest.get("result_blind") is not True
        or Path(str(manifest.get("candidate_dir"))).resolve() != candidate_root
        or manifest.get("bound_role_order") != list(MATERIALIZATION_BOUND_ROLE_ORDER)
        or manifest.get("next_legal_gate") != MATERIALIZATION_GO_SCHEMA
    ):
        raise ControllerError("materialization candidate manifest status/result-blind contract is invalid")
    _require_exact_json_equal(
        manifest.get("authorities"),
        MATERIALIZATION_CANDIDATE_AUTHORITIES,
        "materialization candidate authorities",
    )
    _require_exact_json_equal(
        manifest.get("result_or_row_access"),
        {
            "csv_rows_read": False,
            "model_summary_json_parsed": False,
            "numerical_model_results_accessed": False,
            "scientific_source_files_sha256_and_stat_only": True,
            "protocol_and_provenance_json_parsed": True,
            "descriptor_sealed_runtime_imports_executed": True,
        },
        "materialization candidate result-access boundary",
    )
    sealed_runtime = manifest.get("sealed_runtime")
    if type(sealed_runtime) is not dict or set(sealed_runtime) != {
        "expected_runtime_closure_json_sha256",
        "attestation",
        "runtime_manifest_role_identity",
        "runtime_tree_role_identity",
        "required_external_entrypoint",
        "raw_runtime_fallback_authorized",
    }:
        raise ControllerError("materialization sealed-runtime keyset is not exact")
    manifest_role = sealed_runtime["runtime_manifest_role_identity"]
    tree_role = sealed_runtime["runtime_tree_role_identity"]
    attestation = sealed_runtime["attestation"]
    if (
        type(manifest_role) is not dict
        or set(manifest_role) != {"kind", "path", "sha256"}
        or manifest_role.get("kind") != "file"
        or manifest_role.get("path") != "runtime/contracts/RUNTIME_CLOSURE.json"
        or not _is_sha(manifest_role.get("sha256"))
        or sealed_runtime.get("expected_runtime_closure_json_sha256")
        != manifest_role["sha256"]
        or type(tree_role) is not dict
        or set(tree_role) != {"kind", "path", "sha256"}
        or tree_role.get("kind") != "tree"
        or tree_role.get("path") != "runtime/dependencies"
        or not _is_sha(tree_role.get("sha256"))
        or type(attestation) is not dict
        or set(attestation)
        != {
            "schema", "entrypoint", "manifest_sha256", "pure_archive_sha256",
            "bootstrap_sha256",
        }
        or attestation.get("schema")
        != runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA
        or attestation.get("entrypoint") != "materialization"
        or attestation.get("manifest_sha256") != manifest_role["sha256"]
        or not _is_sha(attestation.get("pure_archive_sha256"))
        or not _is_sha(attestation.get("bootstrap_sha256"))
        or sealed_runtime.get("required_external_entrypoint") != "materialization"
        or sealed_runtime.get("raw_runtime_fallback_authorized") is not False
    ):
        raise ControllerError("materialization sealed-runtime identity is not exact")
    _require_exact_json_equal(
        complete.get("sealed_runtime"),
        sealed_runtime,
        "outer materialization COMPLETE sealed runtime",
    )
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != set(MATERIALIZATION_BOUND_ROLE_ORDER):
        raise ControllerError("materialization candidate bound role keyset is not exact")
    bound_record_keys = {
        "role",
        "path",
        "sha256",
        "size_bytes",
        "mode_octal",
        "nlink",
        "st_dev",
        "st_ino",
    }
    for role in MATERIALIZATION_BOUND_ROLE_ORDER:
        record = bindings[role]
        if (
            type(record) is not dict
            or set(record) != bound_record_keys
            or record.get("role") != role
            or type(record.get("path")) is not str
            or not _is_sha(record.get("sha256"))
            or type(record.get("mode_octal")) is not str
            or any(
                type(record.get(key)) is not int
                for key in ("size_bytes", "nlink", "st_dev", "st_ino")
            )
        ):
            raise ControllerError(f"materialization candidate binding is not exact: {role}")
        path = _file(record.get("path", ""), f"materialization candidate role {role}")
        metadata = path.lstat()
        if (
            _require_sha(path, record.get("sha256"), f"materialization candidate role {role}")
            != record.get("sha256")
            or metadata.st_size != record.get("size_bytes")
            or f"{stat.S_IMODE(metadata.st_mode):04o}" != record.get("mode_octal")
            or metadata.st_nlink != record.get("nlink")
            or metadata.st_dev != record.get("st_dev")
            or metadata.st_ino != record.get("st_ino")
        ):
            raise ControllerError(f"materialization candidate role metadata drifted: {role}")

    _audit_package_build_attempt(bindings)

    future_paths = manifest.get("future_paths")
    if not isinstance(future_paths, dict) or set(future_paths) != {
        "materialization_out_dir",
        "execution_receipt_dir",
    }:
        raise ControllerError("materialization candidate future path binding is invalid")
    if (
        Path(str(future_paths["materialization_out_dir"])).resolve() != summary_path.parent
        or Path(str(future_paths["execution_receipt_dir"])).resolve() != complete_path.parent
    ):
        raise ControllerError("outer COMPLETE path is not the candidate-bound execution/output path")

    for summary_role, candidate_role in MATERIALIZATION_IMPLEMENTATION_ROLES.items():
        observed = implementation.get(summary_role)
        bound = bindings[candidate_role]
        if not _json_exact_is(
            observed,
            {
                "path": bound["path"],
                "sha256": bound["sha256"],
                "size_bytes": bound["size_bytes"],
            },
        ):
            raise ControllerError(f"materialization implementation does not match candidate: {summary_role}")
    for summary_role, candidate_role in MATERIALIZATION_SOURCE_ROLES.items():
        observed = sources.get(summary_role)
        bound = bindings[candidate_role]
        if (
            not isinstance(observed, dict)
            or observed.get("path") != bound["path"]
            or observed.get("sha256") != bound["sha256"]
        ):
            raise ControllerError(f"materialization source does not match candidate: {summary_role}")

    go = _json(Path(material_go["path"]), "materialization GO authority copy")
    material_go_keys = {
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
    if set(go) != material_go_keys:
        raise ControllerError("materialization GO top-level keyset is not exact")
    reviewer = go.get("reviewer")
    findings = go.get("findings")
    if (
        go.get("schema") != MATERIALIZATION_GO_SCHEMA
        or go.get("status") != "GO"
        or go.get("scope") != MATERIALIZATION_GO_SCOPE
        or go.get("challenge_nonce") != manifest.get("challenge_nonce")
        or not isinstance(reviewer, dict)
        or set(reviewer)
        != {"reviewer_id", "independent", "result_blind", "reviewed_without_numerical_results"}
        or not isinstance(reviewer.get("reviewer_id"), str)
        or not reviewer["reviewer_id"].strip()
        or reviewer.get("independent") is not True
        or reviewer.get("result_blind") is not True
        or reviewer.get("reviewed_without_numerical_results") is not True
        or not isinstance(findings, dict)
        or set(findings) != {"p0", "p1", "p2", "p3"}
        or any(type(findings[key]) is not int or findings[key] < 0 for key in findings)
        or findings["p0"] != 0
        or findings["p1"] != 0
    ):
        raise ControllerError("materialization GO identity/zero-finding contract is invalid")
    _require_exact_json_equal(
        go.get("authorities"),
        MATERIALIZATION_GO_AUTHORITIES,
        "materialization GO authorities",
    )
    go_bindings = go.get("bindings")
    expected_go_binding_keys = {
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
    if not isinstance(go_bindings, dict) or set(go_bindings) != expected_go_binding_keys:
        raise ControllerError("materialization GO binding keyset is not exact")
    expected_artifact_sha = {
        role: bindings[role]["sha256"] for role in MATERIALIZATION_BOUND_ROLE_ORDER
    }
    expected_materialization_go_bindings = {
        "candidate_manifest_sha256": candidate_manifest["sha256"],
        "candidate_sha256sums_sha256": candidate_index["sha256"],
        "challenge_nonce": manifest.get("challenge_nonce"),
        "artifact_sha256": expected_artifact_sha,
        "materialization_out_dir": str(summary_path.parent),
        "execution_receipt_dir": str(complete_path.parent),
        "runtime_identity_sha256": (manifest.get("runtime_identity") or {}).get(
            "identity_sha256"
        ),
        "host_identity_sha256": (manifest.get("host_identity") or {}).get(
            "identity_sha256"
        ),
        "materialization_contract_sha256": manifest.get(
            "materialization_contract_sha256"
        ),
        "sealed_runtime": sealed_runtime,
    }
    try:
        _require_exact_json_equal(
            go_bindings,
            expected_materialization_go_bindings,
            "materialization GO bindings",
        )
    except ControllerError as exc:
        raise ControllerError(
            f"materialization GO does not exactly bind candidate/source/code/output: {exc}"
        ) from exc

    frozen_closure = {
        "candidate_manifest_sha256": candidate_manifest["sha256"],
        "candidate_sha256sums_sha256": candidate_index["sha256"],
        "artifact_sha256": expected_artifact_sha,
        "go_sha256": material_go["sha256"],
        "held_snapshot_consumption": True,
        "path_reopen_for_consumed_inputs": False,
    }
    if not _json_exact_is(
        complete.get("frozen_closure_after_materialization"), frozen_closure
    ):
        raise ControllerError("outer COMPLETE frozen candidate/GO closure is not exact")

    validation = complete.get("materialization_validation")
    material_output = complete.get("materialization_output")
    exact_validation_keys = {
        "status",
        "root",
        "arm_rows",
        "gradient_train_rows",
        "validation_rows_common",
        "test_rows_common",
        "artifact_closure",
        "sha256sums_sha256",
        "training_authorized",
        "evaluation_authorized",
        "fresh_emx_authorized",
    }
    if type(validation) is not dict or set(validation) != exact_validation_keys:
        raise ControllerError("outer COMPLETE materialization validation keyset is not exact")
    if type(material_output) is not dict or set(material_output) != {
        "path",
        "sha256sums",
        "artifact_closure",
    }:
        raise ControllerError("outer COMPLETE materialization-output binding is invalid")
    output_index_binding = _exact_bound_file(
        material_output.get("sha256sums"), "outer COMPLETE material output SHA index"
    )
    actual_output_closure = _artifact_snapshot(summary_path.parent)
    if (
        Path(str(material_output.get("path"))).resolve() != summary_path.parent
        or Path(output_index_binding["path"]) != material_index
        or validation.get("root") != str(summary_path.parent)
        or validation.get("sha256sums_sha256") != output_index_binding["sha256"]
        or not _json_exact_is(validation.get("artifact_closure"), actual_output_closure)
        or not _json_exact_is(
            material_output.get("artifact_closure"), actual_output_closure
        )
        or validation.get("status") != "PASS_MATERIALIZATION_DEEP_VALIDATED_RESULT_BLIND"
        or not _json_exact_is(
            validation.get("arm_rows"),
            {
                "n10000": EXPECTED_COUNTS["small"]["source_rows"],
                "n20000": EXPECTED_COUNTS["large"]["source_rows"],
            },
        )
        or not _json_exact_is(
            validation.get("gradient_train_rows"),
            {
                "n10000": EXPECTED_COUNTS["small"]["gradient_train"],
                "n20000": EXPECTED_COUNTS["large"]["gradient_train"],
            },
        )
        or type(validation.get("validation_rows_common")) is not int
        or validation.get("validation_rows_common")
        != EXPECTED_COUNTS["small"]["validation"]
        or type(validation.get("test_rows_common")) is not int
        or validation.get("test_rows_common") != EXPECTED_COUNTS["small"]["test"]
        or validation.get("training_authorized") is not False
        or validation.get("evaluation_authorized") is not False
        or validation.get("fresh_emx_authorized") is not False
    ):
        raise ControllerError("outer COMPLETE does not bind the exact material output closure")

    receipt_root = complete_path.parent
    expected_receipt_entries = {
        "GO_AUTHORITY.json",
        "INTENT.json",
        "RUNNING.json",
        "COMPLETE.json",
    }
    if {path.name for path in receipt_root.iterdir()} != expected_receipt_entries:
        raise ControllerError("materialization execution receipt closure is not exact")
    precursor = {
        key: value
        for key, value in _artifact_snapshot(receipt_root).items()
        if key != complete_path.name
    }
    if not _json_exact_is(complete.get("execution_precursor_closure"), precursor):
        raise ControllerError("outer COMPLETE execution precursor closure changed")
    return {
        "complete": {"path": str(complete_path), "sha256": complete_sha},
        "candidate_manifest": candidate_manifest,
        "candidate_sha_index": candidate_index,
        "materialization_go_authority": material_go,
        "sealed_runtime": sealed_runtime,
        "candidate_bindings": {
            role: {"path": bindings[role]["path"], "sha256": bindings[role]["sha256"]}
            for role in MATERIALIZATION_BOUND_ROLE_ORDER
        },
        "materialization_output_closure": actual_output_closure,
    }


def _audit_material(
    summary_path: Path,
    expected_sha: str,
    complete_path: Path,
    expected_complete_sha: str,
    runtime_shared_contract_sha256: str,
) -> dict[str, Any]:
    summary_sha = _require_sha(summary_path, expected_sha, "materialization summary")
    root = summary_path.parent
    summary = _json(summary_path, "materialization summary")
    if summary.get("schema") != MATERIAL_SCHEMA or summary.get("status") != "PASS" or summary.get("decision") != "PREPARED_FOR_INDEPENDENT_QA":
        raise ControllerError("materialization summary is not the exact production QA candidate")
    if summary.get("result_accessed") is not False or summary.get("model_training_performed") is not False or summary.get("emx_performed") is not False:
        raise ControllerError("materialization summary is not result blind")
    if summary.get("training_launch_authorized") is not False or summary.get("independent_qa_required") is not True:
        raise ControllerError("materialization independent-QA launch gate was bypassed")
    shared_sha = _normalized_sha(
        runtime_shared_contract_sha256, "runtime shared contract SHA-256"
    )
    implementation = summary.get("implementation_identities") or {}
    if not isinstance(implementation, dict) or set(implementation) != set(
        MATERIALIZATION_IMPLEMENTATION_ROLES
    ):
        raise ControllerError("materialization implementation role keyset is not exact")
    for role, record in implementation.items():
        if not isinstance(record, dict):
            raise ControllerError(f"materialization implementation identity is invalid: {role}")
        implementation_path = _file(record.get("path", ""), f"materialization implementation {role}")
        _require_sha(implementation_path, record.get("sha256"), f"materialization implementation {role}")
        if not _json_int_is(record.get("size_bytes"), implementation_path.stat().st_size):
            raise ControllerError(f"materialization implementation size differs: {role}")
    shared_binding = implementation.get("shared_contract") or {}
    shared_path = _file(shared_binding.get("path", ""), "materialization shared contract")
    if shared_binding.get("sha256") != shared_sha or _sha256(shared_path) != shared_sha:
        raise ControllerError("materialization does not bind the exact shared contract")
    shared_record = summary.get("shared_contract") or {}
    if shared_record != {
        "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
        "physical_cell_bins": PHYSICAL_CELL_BINS,
        "extra_selection_seed": EXACT_EXTRA_SELECTION_SEED,
        "paired_seeds": list(EXACT_PAIRED_SEEDS),
    }:
        raise ControllerError("materialization shared constants are not exact")
    production = summary.get("production_exact_checks")
    if (
        not isinstance(production, dict)
        or set(production) != MATERIALIZATION_PRODUCTION_EXACT_CHECK_KEYS
        or any(value is not True for value in production.values())
    ):
        raise ControllerError("materialization production-exact check keyset/values are not exact")
    selection = summary.get("selection_contract") or {}
    if (
        selection.get("method") != "stable_sha256_rank_within_proportional_historical_train_cell_quotas_v1"
        or not _json_int_is(selection.get("selection_seed"), EXACT_EXTRA_SELECTION_SEED)
        or selection.get("selection_uses_model_results") is not False
        or selection.get("historical_geometry_excluded") is not True
        or selection.get("historical_touchstone_content_excluded") is not True
        or selection.get("extra_rows_restricted_to_historical_train_cells") is not True
    ):
        raise ControllerError("materialization selection is not exact result-blind train-cell selection")
    source = summary.get("source_identities") or {}
    if not isinstance(source, dict) or set(source) != set(MATERIALIZATION_SOURCE_ROLES):
        raise ControllerError("materialization source role keyset is not exact")
    frozen_source_sha = {
        "historical_10k_csv": FROZEN_HISTORICAL_10K_CSV_SHA256,
        "authoritative_100k_csv": FROZEN_AUTHORITATIVE_100K_CSV_SHA256,
        "historical_model_summary_json": FROZEN_HISTORICAL_MODEL_SUMMARY_SHA256,
    }
    for role, frozen_sha in frozen_source_sha.items():
        record = source.get(role) or {}
        expected_record_keys = (
            {"path", "sha256", "rows"}
            if role != "historical_model_summary_json"
            else {"path", "sha256"}
        )
        if not isinstance(record, dict) or set(record) != expected_record_keys:
            raise ControllerError(f"materialization source record keyset is not exact: {role}")
        source_path = _file(record.get("path", ""), f"materialization source {role}")
        if _require_sha(source_path, record.get("sha256"), f"materialization source {role}") != frozen_sha:
            raise ControllerError(f"materialization source is not frozen production identity: {role}")
    if (
        not _json_int_is(
            source["historical_10k_csv"].get("rows"),
            EXPECTED_COUNTS["small"]["source_rows"],
        )
        or not _json_int_is(
            source["authoritative_100k_csv"].get("rows"),
            FROZEN_AUTHORITATIVE_SOURCE_ROWS,
        )
        or "rows" in source["historical_model_summary_json"]
    ):
        raise ControllerError("materialization source row-denominator contract is not exact")
    historical_binding = source.get("historical_model_summary_json") or {}
    historical_path = _file(historical_binding.get("path", ""), "historical model summary")
    historical_sha = _require_sha(historical_path, historical_binding.get("sha256"), "historical model summary")
    artifacts_raw = summary.get("artifacts")
    if not isinstance(artifacts_raw, dict):
        raise ControllerError("materialization artifacts map is missing")
    artifacts: dict[str, dict[str, Any]] = {}
    for logical, filename in MATERIAL_FILES.items():
        if filename not in artifacts_raw:
            raise ControllerError(f"materialization lacks {filename}")
        binding = _artifact_binding(artifacts_raw[filename], summary_path, filename)
        if Path(binding["path"]).name != filename:
            raise ControllerError(f"materialization artifact filename mismatch for {filename}")
        artifacts[logical] = binding
    counts_raw = summary.get("arm_counts") or {}
    for arm, key in (("small", "n10000"), ("large", "n20000")):
        record = counts_raw.get(key) or {}
        count_fields = {
            "source_rows": "source_table_rows",
            "gradient_train": "gradient_train_rows",
            "validation": "validation_rows",
            "test": "test_rows",
        }
        if any(
            not _json_int_is(record.get(source_key), EXPECTED_COUNTS[arm][target_key])
            for target_key, source_key in count_fields.items()
        ):
            raise ControllerError(f"materialization {arm} counts are not exact")
    nested = summary.get("nested_identity_contract") or {}
    if (
        nested.get("arm_n10000_is_exact_ordered_prefix_and_row_subset_of_arm_n20000") is not True
        or nested.get("common_output_schema") != list(OUTPUT_COLUMNS)
        or nested.get("common_validation_and_test_unchanged") is not True
        or not _json_int_is(
            nested.get("geometry_identity_overlap_historical_vs_extra"), 0
        )
        or not _json_int_is(
            nested.get("touchstone_identity_overlap_historical_vs_extra"), 0
        )
    ):
        raise ControllerError("materialization nested identity contract is not exact")
    small = _audit_csv(Path(artifacts["small_csv"]["path"]), "small")
    large = _audit_csv(Path(artifacts["large_csv"]["path"]), "large")
    if small["rows"] != large["rows"][: len(small["rows"])]:
        raise ControllerError("10K CSV is not the exact OUTPUT_COLUMNS prefix of 20K CSV")
    small_train = {identity for identity, row in small["identities"].items() if row["split"] == "train"}
    large_train = {identity for identity, row in large["identities"].items() if row["split"] == "train"}
    if not small_train < large_train:
        raise ControllerError("10K gradient-training identities are not a strict subset of 20K")
    normalization = _audit_normalization(Path(artifacts["fixed_normalization"]["path"]))
    holdout = _audit_holdout(
        Path(artifacts["common_holdout"]["path"]), small, large, historical_sha, shared_sha
    )
    fixed = summary.get("fixed_contracts") or {}
    fixed_pairs = {
        "common_holdout": artifacts["common_holdout"],
        "declared_midpoint_half_range_normalization": artifacts["fixed_normalization"],
    }
    for key, expected in fixed_pairs.items():
        binding = fixed.get(key) or {}
        if Path(str(binding.get("path") or "")).resolve() != Path(expected["path"]) or binding.get("sha256") != expected["sha256"]:
            raise ControllerError(f"materialization fixed contract {key} is inconsistent")
    receipt_path = _file(root / MATERIAL_RECEIPT_NAME, "materialization receipt")
    material_qa_path = _file(root / MATERIAL_QA_REQUIRED_NAME, "materialization independent-QA required record")
    material_qa = _json(material_qa_path, "materialization independent-QA required record")
    if (
        material_qa.get("schema") != MATERIAL_QA_REQUIRED_SCHEMA
        or material_qa.get("status") != "INDEPENDENT_QA_REQUIRED"
        or material_qa.get("verdict") != "NO_GO_PENDING_FRESH_INDEPENDENT_QA"
        or material_qa.get("training_authorized") is not False
        or material_qa.get("result_access_authorized") is not False
        or material_qa.get("fresh_emx_authorized") is not False
        or (material_qa.get("next_legal_gate") or {}).get("required_receipt_schema") != GO_SCHEMA
        or (material_qa.get("next_legal_gate") or {}).get("required_status") != GO_STATUS
    ):
        raise ControllerError("materialization independent-QA required record is not an exact NO-GO gate")
    if (material_qa.get("materialization_summary") or {}) != {
        "path": str(summary_path),
        "sha256": summary_sha,
    }:
        raise ControllerError("materialization QA-required record does not bind exact summary")
    qa_frozen = material_qa.get("frozen_artifacts") or {}
    for logical, filename in MATERIAL_FILES.items():
        record = qa_frozen.get(filename) or {}
        expected = artifacts[logical]
        if record.get("sha256") != expected["sha256"] or Path(str(record.get("path") or "")).resolve() != Path(expected["path"]):
            raise ControllerError(f"materialization QA-required record does not bind {filename}")
    receipt = _json(receipt_path, "materialization receipt")
    if (
        receipt.get("schema") != MATERIAL_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("verdict") != "PREPARED_FOR_INDEPENDENT_QA"
        or receipt.get("training_launch_authorized") is not False
        or receipt.get("independent_qa_required") is not True
        or receipt.get("next_legal_gate") != "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO"
    ):
        raise ControllerError("materialization receipt gate is not exact")
    receipt_checks = receipt.get("checks")
    if not isinstance(receipt_checks, dict) or not receipt_checks or not all(value is True for value in receipt_checks.values()):
        raise ControllerError("materialization receipt checks are not all true")
    if receipt.get("production_exact_checks") != production:
        raise ControllerError("materialization receipt production checks differ from summary")
    expected_material_order = [
        MATERIAL_FILES["small_csv"],
        MATERIAL_FILES["large_csv"],
        MATERIAL_FILES["common_holdout"],
        MATERIAL_FILES["fixed_normalization"],
        MATERIAL_SUMMARY_NAME,
        MATERIAL_QA_REQUIRED_NAME,
        MATERIAL_RECEIPT_NAME,
    ]
    if receipt.get("sha256_closure_contract") != {
        "index_filename": MATERIAL_SHA_INDEX_NAME,
        "index_self_hash_included": False,
        "exact_entry_count": 7,
        "exact_filenames_in_order": expected_material_order,
    }:
        raise ControllerError("materialization receipt SHA closure contract differs")
    receipt_artifacts = receipt.get("artifact_identities") or {}
    expected_receipt_artifacts = set(artifacts_raw) | {
        MATERIAL_SUMMARY_NAME,
        MATERIAL_QA_REQUIRED_NAME,
    }
    if set(receipt_artifacts) != expected_receipt_artifacts:
        raise ControllerError("materialization receipt artifact identity set is not exact")
    for filename, record in artifacts_raw.items():
        if filename not in receipt_artifacts:
            raise ControllerError(f"materialization receipt lacks artifact {filename}")
        receipt_binding = receipt_artifacts[filename]
        if receipt_binding.get("sha256") != record.get("sha256") or Path(str(receipt_binding.get("path") or "")).resolve() != Path(str(record.get("path") or "")).resolve():
            raise ControllerError(f"materialization receipt artifact binding differs for {filename}")
    summary_receipt_binding = receipt_artifacts.get(MATERIAL_SUMMARY_NAME) or {}
    if summary_receipt_binding.get("sha256") != summary_sha or Path(str(summary_receipt_binding.get("path") or "")).resolve() != summary_path:
        raise ControllerError("materialization receipt does not bind exact summary")
    qa_receipt_binding = receipt_artifacts.get(MATERIAL_QA_REQUIRED_NAME) or {}
    if qa_receipt_binding.get("sha256") != _sha256(material_qa_path) or Path(str(qa_receipt_binding.get("path") or "")).resolve() != material_qa_path:
        raise ControllerError("materialization receipt does not bind exact QA-required record")
    if receipt.get("independent_qa_required_record") != {
        "path": str(material_qa_path),
        "sha256": _sha256(material_qa_path),
    }:
        raise ControllerError("materialization receipt QA-required binding differs")
    material_index = _file(root / MATERIAL_SHA_INDEX_NAME, "materialization SHA index")
    index_records = _parse_sha_index(material_index, root, "materialization SHA index")
    required_index = {
        MATERIAL_SUMMARY_NAME,
        MATERIAL_RECEIPT_NAME,
        MATERIAL_QA_REQUIRED_NAME,
        *MATERIAL_FILES.values(),
    }
    if set(index_records) != required_index:
        raise ControllerError("materialization SHA index lacks required closure files")
    if list(index_records) != expected_material_order:
        raise ControllerError("materialization SHA index filename order differs from its receipt contract")
    for filename in required_index:
        expected_path = root / filename
        if index_records[filename]["sha256"] != _sha256(expected_path):
            raise ControllerError(f"materialization SHA index closure mismatch: {filename}")
    outer_authority = _audit_materialization_outer_complete(
        complete_path,
        expected_complete_sha,
        summary_path=summary_path,
        material_index=material_index,
        implementation=implementation,
        sources=source,
    )
    split = summary.get("split_reconstruction") or {}
    if split.get("exact_match_to_historical_summary") is not True:
        raise ControllerError("historical split reconstruction is not exact")
    holdout_payload = _json(Path(holdout["path"]), "common holdout")
    if split.get("physical_cell_partition_fingerprint_sha256") != holdout_payload.get("physical_cell_partition_fingerprint_sha256"):
        raise ControllerError("historical split/holdout cell fingerprint differs")
    return {
        "summary": {"path": str(summary_path), "sha256": summary_sha},
        "receipt": {"path": str(receipt_path), "sha256": _sha256(receipt_path)},
        "independent_qa_required": {"path": str(material_qa_path), "sha256": _sha256(material_qa_path)},
        "sha_index": {"path": str(material_index), "sha256": _sha256(material_index)},
        "artifacts": artifacts,
        "historical_model_summary": {"path": str(historical_path), "sha256": historical_sha},
        "shared_contract": {"path": str(shared_path), "sha256": shared_sha},
        "outer_materialization_authority": outer_authority,
        "counts": {arm: dict(values) for arm, values in EXPECTED_COUNTS.items()},
        "audits": {
            "small_identity_set_sha256": small["identity_set_sha256"],
            "large_identity_set_sha256": large["identity_set_sha256"],
            "holdout_fingerprint_sha256": holdout["fingerprint_sha256"],
            "normalization_schema": normalization["schema"],
            "complete_cell_isolation": True,
            "exact_ordered_prefix": True,
            "selection_result_blind": True,
        },
        "material_gate_consumption": {
            "training_launch_authorized_in_materialization": False,
            "independent_qa_required": True,
            "only_execute_authority": GO_SCHEMA,
        },
    }


def _runtime_identity(
    python: Path,
    python_descriptor_identity: Mapping[str, Any],
    closure_json: Path,
    expected_closure_json_sha256: str,
    closure_tree: Path,
    bootstrap: Path,
    expected_bootstrap_sha256: str,
) -> dict[str, Any]:
    try:
        closure = runtime_bootstrap.audit_runtime_closure_paths(
            closure_json,
            expected_closure_json_sha256,
            closure_tree,
            bootstrap,
            expected_bootstrap_sha256,
        )
    except runtime_bootstrap.RuntimeClosureError as exc:
        raise ControllerError(f"runtime dependency closure is invalid: {exc}") from exc
    return {
        "python": dict(python_descriptor_identity),
        "numpy_version": closure["numpy"]["version"],
        "bootstrap": dict(closure["bootstrap"]),
        "descriptor_closure": closure,
    }


def _run_contract(
    *,
    out_dir: Path,
    material: dict[str, Any],
    trainer: Path,
    trainer_sha: str,
    runtime: dict[str, Any],
    controlled_singleton: dict[str, Any],
) -> dict[str, Any]:
    runner = _file(Path(__file__).resolve(), "paired controller")
    role_bindings = runtime["descriptor_closure"]["role_bindings"]
    if role_bindings["runner_code"]["sha256"] != _sha256(runner):
        raise ControllerError("runtime closure runner bytes differ from the executing runner path")
    shared_binding = role_bindings["shared_contract_code"]
    outer = material["outer_materialization_authority"]
    sealed_runtime = outer["sealed_runtime"]
    closure = runtime["descriptor_closure"]
    if (
        sealed_runtime["expected_runtime_closure_json_sha256"]
        != closure["manifest"]["sha256"]
        or sealed_runtime["runtime_manifest_role_identity"]["sha256"]
        != closure["manifest"]["sha256"]
        or sealed_runtime["attestation"]
        != {
            "schema": runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
            "entrypoint": "materialization",
            "manifest_sha256": closure["manifest"]["sha256"],
            "pure_archive_sha256": closure["pure_archive"]["sha256"],
            "bootstrap_sha256": closure["bootstrap"]["sha256"],
        }
    ):
        raise ControllerError(
            "materialization and paired runner descriptor-runtime identities differ"
        )
    candidate_bindings = outer["candidate_bindings"]
    singleton_lock_binding = candidate_bindings["package_singleton_lock"]
    singleton_contract_binding = candidate_bindings[
        "package_process_singleton_contract"
    ]
    if (
        Path(singleton_lock_binding["path"]) != Path(controlled_singleton["path"])
        or singleton_lock_binding["sha256"] != controlled_singleton["sha256"]
    ):
        raise ControllerError(
            "materialization package singleton lock differs from held runner lock"
        )
    singleton_contract = _json(
        Path(singleton_contract_binding["path"]), "package singleton contract"
    )
    if (
        set(singleton_contract)
        != {
            "schema", "lock", "protected_entrypoints", "proc_audit", "lifetime",
            "conflict_policy",
        }
        or singleton_contract.get("schema")
        != "controlled_real10k_20k_process_singleton_contract_v1"
        or (singleton_contract.get("lock") or {}).get("sha256")
        != controlled_singleton["sha256"]
        or (singleton_contract.get("lock") or {}).get("relative_path")
        != "CONTROLLED_SINGLETON.lock"
        or (singleton_contract.get("lock") or {}).get("operation") != "LOCK_EX|LOCK_NB"
        or (singleton_contract.get("lifetime") or {}).get("full_lifetime_required")
        is not True
        or (singleton_contract.get("conflict_policy") or {}).get(
            "controlled_process_start_authorized"
        )
        is not False
    ):
        raise ControllerError("package singleton contract transmission is not exact")
    core = {
        "schema": RUN_SCHEMA,
        "out_dir": str(out_dir),
        "runner": {"path": str(runner), "sha256": _sha256(runner)},
        "shared_contract": {
            "member": shared_binding["member"],
            "sha256": shared_binding["sha256"],
            "size_bytes": shared_binding["size_bytes"],
        },
        "trainer": {"path": str(trainer), "sha256": trainer_sha},
        "runtime": runtime,
        "controlled_singleton": controlled_singleton,
        "production_hard_identities": {
            "trainer_sha256": PRODUCTION_TRAINER_SHA256,
            "python_sha256": PRODUCTION_PYTHON_SHA256,
            "python_version": PRODUCTION_PYTHON_VERSION,
            "numpy_version": PRODUCTION_NUMPY_VERSION,
        },
        "materialization": material,
        "paired_seeds": list(EXACT_PAIRED_SEEDS),
        "arm_order_within_seed": list(ARM_ORDER),
        "process_contract": {
            "serial_child_count": 1,
            "load1_maximum_inclusive": MAX_LOAD1,
            "nice": CHILD_NICE,
            "thread_environment": {key: str(THREAD_LIMIT) for key in THREAD_ENV_KEYS},
            "trainer_launch": _trainer_launch_contract(),
            "shell": False,
            "stdin": "DEVNULL",
            "incomplete_attempt_policy": "AMBIGUOUS_FAIL_CLOSED_NO_RETRY_NO_RESUME",
        },
        "training_contract": {
            "input_columns": list(INPUT_COLUMNS),
            "geometry_columns": list(GEOMETRY_COLUMNS),
            "activation": "GELU_pinned_by_exact_trainer_SHA256",
            "forward_hidden_widths": [256, 256, 256],
            "inverse_hidden_widths": [256, 256, 256],
            "forward_initialization": "random_fresh",
            "inverse_initialization": "random_fresh",
            "inverse_geometry_projection": "independent_sigmoid",
            "inverse_checkpoint_selection": "training_objective_on_validation_only",
            "split_reference_columns": list(INPUT_COLUMNS),
            "validation_fraction": 0.15,
            "test_fraction": 0.10,
            "batch_size": 1024,
            "training_batch_sampler": "row_uniform",
            "exact_update_batch_mode": "continuous_permutation_full_batch",
            "forward_epochs_parser_limit": 160,
            "inverse_epochs_parser_limit": 180,
            "patience_parser_value_disabled_by_exact_updates": 20,
            "forward_optimizer_updates": 1200,
            "inverse_optimizer_updates": 1200,
            "validation_every_optimizer_updates": 20,
            "early_stopping_enabled": False,
            "learning_rate": 0.001,
            "learning_rate_schedule": "constant",
            "final_learning_rate_fraction_recorded": 0.1,
            "weight_decay": 0.000001,
            "response_loss_family": "mse",
            "response_loss_scaling": "declared_range",
            "response_weight": 1.0,
            "geometry_anchor_weight": 0.01,
            "topology_feasibility_weight": 0.0,
            "q_target_semantics": "exact",
            "q_minimum_margin_physical": 0.0,
            "response_weight_schedule": "warmup_ramp_adaptive_ema",
            "response_schedule_domain": "optimizer_update",
            "response_warmup_fraction": 0.05,
            "response_ramp_fraction": 0.25,
            "response_warmup_optimizer_updates": 60,
            "response_ramp_optimizer_updates": 300,
            "response_adaptive_ema_decay": 0.95,
            "response_adaptive_min_multiplier": 0.25,
            "response_adaptive_max_multiplier": 4.0,
            "normalization_floor": 1.0e-12,
            "fixed_normalization_sha256": material["artifacts"]["fixed_normalization"]["sha256"],
            "fixed_holdout_sha256": material["artifacts"]["common_holdout"]["sha256"],
            "evaluation_mode": "validation_only",
            "test_access_event_count": 0,
            "local_refinement_steps": 0,
            "max_prediction_rows": 20000,
            "stage_checkpoint_mode": "resume_exact",
        },
        "release_boundary": {
            "fresh_emx_accessed": False,
            "test_evaluation_performed": False,
            "numerical_metrics_released": False,
            "success_after_training": FINAL_STATUS,
        },
    }
    return {
        **core,
        "qa_challenge_nonce": hashlib.sha256(
            b"controlled_real10k_20k_training_qa_nonce_v2\0"
            + json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        ).hexdigest(),
    }


def _trainer_entrypoint_argv(
    contract: dict[str, Any], out_dir: Path, seed: int, arm: str
) -> list[str]:
    material = contract["materialization"]
    artifacts = material["artifacts"]
    training_csv = artifacts[f"{arm}_csv"]["path"]
    run_dir = out_dir / "runs" / f"seed_{seed}" / arm
    return [
        contract["trainer"]["path"],
        "--training-csv", training_csv,
        "--out-dir", str(run_dir),
        "--input-columns", ",".join(INPUT_COLUMNS),
        "--geometry-columns", ",".join(GEOMETRY_COLUMNS),
        "--min-training-rows", str(material["counts"][arm]["source_rows"]),
        "--split-reference-columns", ",".join(INPUT_COLUMNS),
        "--validation-fraction", "0.15",
        "--test-fraction", "0.10",
        "--split-mode", "fixed_common_holdout_manifest",
        "--physical-cell-bins", str(PHYSICAL_CELL_BINS),
        "--physical-cell-lower", ",".join(format(value, ".17g") for value in INPUT_LOWER),
        "--physical-cell-upper", ",".join(format(value, ".17g") for value in INPUT_UPPER),
        "--fixed-common-holdout-manifest-json", artifacts["common_holdout"]["path"],
        "--fixed-common-holdout-manifest-sha256", artifacts["common_holdout"]["sha256"],
        "--seed", str(seed),
        "--split-seed", str(seed),
        "--forward-depth", "3",
        "--forward-width", "256",
        "--forward-hidden-widths", "256,256,256",
        "--forward-initialization-mode", "random",
        "--inverse-depth", "3",
        "--inverse-width", "256",
        "--inverse-hidden-widths", "256,256,256",
        "--inverse-geometry-projection", "independent_sigmoid",
        "--inverse-checkpoint-selection", "training_objective",
        "--inverse-checkpoint-exact-relative-error-threshold", "0.10",
        "--inverse-initialization-mode", "random",
        "--batch-size", "1024",
        "--training-batch-sampler", "row_uniform",
        "--exact-update-batch-mode", "continuous_permutation_full_batch",
        "--forward-epochs", "160",
        "--inverse-epochs", "180",
        "--patience", "20",
        "--forward-max-optimizer-updates", "1200",
        "--inverse-max-optimizer-updates", "1200",
        "--validation-every-optimizer-updates", "20",
        "--learning-rate", "0.001",
        "--training-learning-rate-schedule", "constant",
        "--training-final-learning-rate-fraction", "0.1",
        "--weight-decay", "0.000001",
        "--response-weight", "1.0",
        "--geometry-anchor-weight", "0.01",
        "--topology-feasibility-weight", "0.0",
        "--response-ramp-fraction", "0.25",
        "--response-loss-scaling", "declared_range",
        "--response-loss-family", "mse",
        "--q-target-semantics", "exact",
        "--q-minimum-margin-physical", "0.0",
        "--response-weight-schedule", "warmup_ramp_adaptive_ema",
        "--response-schedule-domain", "optimizer_update",
        "--response-warmup-fraction", "0.05",
        "--response-warmup-optimizer-updates", "60",
        "--response-ramp-optimizer-updates", "300",
        "--response-adaptive-ema-decay", "0.95",
        "--response-adaptive-min-multiplier", "0.25",
        "--response-adaptive-max-multiplier", "4.0",
        "--normalization-floor", "1e-12",
        "--fixed-normalization-contract-json", artifacts["fixed_normalization"]["path"],
        "--fixed-normalization-contract-sha256", artifacts["fixed_normalization"]["sha256"],
        "--max-forward-test-rmse", "inf",
        "--max-tandem-response-test-rmse", "inf",
        "--evaluation-mode", "validation_only",
        "--local-refinement-steps", "0",
        "--local-refinement-starts", "1",
        "--local-refinement-learning-rate", "0.05",
        "--local-refinement-optimizer", "projected_gradient",
        "--local-refinement-lr-schedule", "constant",
        "--local-refinement-final-lr-fraction", "0.1",
        "--local-refinement-jitter", "0.05",
        "--local-refinement-seed", str(seed),
        "--max-prediction-rows", "20000",
        "--robustness-noise-levels", "0.01,0.03,0.05,0.10",
        "--robustness-repeats", "3",
        "--robustness-max-rows", "4096",
        "--robustness-seed", str(seed),
        "--stage-checkpoint-mode", "resume_exact",
    ]


def _trainer_argv(
    contract: dict[str, Any], out_dir: Path, seed: int, arm: str
) -> list[str]:
    del out_dir, seed, arm
    return [
        contract["runtime"]["python"]["path"],
        *PYTHON_ISOLATION_FLAGS,
        f"/proc/self/fd/{runtime_bootstrap.BOOTSTRAP_FD}",
        "--request-fd",
        str(runtime_bootstrap.REQUEST_FD),
        "--entrypoint",
        "trainer",
    ]


def _command_records(contract: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    contract_file_sha = hashlib.sha256(_json_bytes(contract)).hexdigest()
    environment = _require_exact_effective_environment(
        _effective_child_environment(), contract
    )
    environment_sha = _child_environment_sha256(environment)
    records: list[dict[str, Any]] = []
    for seed in EXACT_PAIRED_SEEDS:
        for arm in ARM_ORDER:
            records.append(
                {
                    "schema": "controlled_real10k_20k_exact_trainer_argv_v3",
                    "run_contract_sha256": contract_file_sha,
                    "seed": seed,
                    "arm": arm,
                    "argv": _trainer_argv(contract, out_dir, seed, arm),
                    "entrypoint_argv": _trainer_entrypoint_argv(
                        contract, out_dir, seed, arm
                    ),
                    "runtime_dependency_closure": dict(
                        contract["runtime"]["descriptor_closure"]
                    ),
                    "runtime_attestation_path": str(
                        out_dir
                        / "receipts"
                        / f"seed_{seed}_{arm}"
                        / "attempt_0001"
                        / "RUNTIME_ATTESTATION.jsonl"
                    ),
                    "python_isolation_flags": list(PYTHON_ISOLATION_FLAGS),
                    "effective_environment": dict(environment),
                    "effective_environment_sha256": environment_sha,
                    "shell": False,
                    "evaluation_mode": "validation_only",
                    "test_access_authorized": False,
                }
            )
    return records


def _command_path(out_dir: Path, seed: int, arm: str) -> Path:
    return out_dir / "commands" / f"seed_{seed}_{arm}.json"


def _qa_required_payload(contract: dict[str, Any], out_dir: Path, commands: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "controlled_real10k_20k_independent_qa_required_v4",
        "status": "QA_REQUIRED",
        "training_launch_authorized": False,
        "challenge_nonce": contract["qa_challenge_nonce"],
        "required_go_schema": GO_SCHEMA,
        "required_go_status": GO_STATUS,
        "required_go_verdict": GO_VERDICT,
        "required_go_scope": GO_SCOPE,
        "maximum_go_validity_hours": MAX_GO_VALIDITY.total_seconds() / 3600.0,
        "run_contract_candidate_sha256": hashlib.sha256(_json_bytes(contract)).hexdigest(),
        "command_candidate_sha256": {
            f"seed_{record['seed']}_{record['arm']}": hashlib.sha256(_json_bytes(record)).hexdigest()
            for record in commands
        },
        "required_external_bindings": [
            "run_contract",
            "prepared_receipt",
            "independent_qa_required",
            "package_sha_index",
            "runner",
            "shared_contract",
            "trainer",
            "python",
            "numpy_version",
            "runtime_bootstrap",
            "runtime_dependency_closure",
            "controlled_singleton",
            "trainer_launch_contract",
            "materialization_summary",
            "materialization_receipt",
            "materialization_independent_qa_required",
            "materialization_sha_index",
            "materialization_complete_receipt",
            "materialization_candidate_manifest",
            "materialization_candidate_sha_index",
            "materialization_go_authority",
            "materialized_artifacts",
            "exact_paired_seeds",
            "out_dir",
        ],
        "required_findings": {"p0": 0, "p1": 0},
        "next_legal_action": "EXTERNAL_INDEPENDENT_RESULT_BLIND_REVIEW_THEN_EXACT_GO_OR_NO_GO",
    }


def _write_sha_index_x(path: Path, root: Path, files: Sequence[Path]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        for item in sorted(files, key=lambda value: value.relative_to(root).as_posix()):
            handle.write(f"{_sha256(item)}  {item.relative_to(root).as_posix()}\n")


def _prepare_or_verify(out_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    contract_path = out_dir / "run_contract.json"
    commands = _command_records(contract, out_dir)
    qa_path = out_dir / "INDEPENDENT_QA_REQUIRED.json"
    prepared_path = out_dir / "receipts" / "PREPARED_RECEIPT.json"
    index_path = out_dir / "SHA256SUMS.txt"
    new = not out_dir.exists()
    if new:
        out_dir.mkdir(parents=True)
        _write_json_x(contract_path, contract)
        for record in commands:
            _write_json_x(_command_path(out_dir, record["seed"], record["arm"]), record)
        qa_payload = _qa_required_payload(contract, out_dir, commands)
        _write_json_x(qa_path, qa_payload)
        command_bindings = [
            {
                "seed": record["seed"],
                "arm": record["arm"],
                "path": str(_command_path(out_dir, record["seed"], record["arm"])),
                "sha256": _sha256(_command_path(out_dir, record["seed"], record["arm"])),
            }
            for record in commands
        ]
        _write_json_x(
            prepared_path,
            {
                "schema": "controlled_real10k_20k_prepared_receipt_v4",
                "status": "PREPARED_AWAITING_INDEPENDENT_QA",
                "training_launch_authorized": False,
                "run_contract": {"path": str(contract_path), "sha256": _sha256(contract_path)},
                "commands": command_bindings,
                "independent_qa_required": {"path": str(qa_path), "sha256": _sha256(qa_path)},
                "challenge_nonce": contract["qa_challenge_nonce"],
                "exact_paired_seeds": list(EXACT_PAIRED_SEEDS),
                "numerical_metrics_accessed": False,
                "fresh_emx_accessed": False,
            },
        )
        package_files = [contract_path, qa_path, prepared_path] + [
            _command_path(out_dir, record["seed"], record["arm"]) for record in commands
        ]
        _write_sha_index_x(index_path, out_dir, package_files)
    if not contract_path.is_file() or contract_path.read_bytes() != _json_bytes(contract):
        raise ControllerError("existing run_contract.json is not byte-identical")
    for record in commands:
        path = _command_path(out_dir, record["seed"], record["arm"])
        if not path.is_file() or path.read_bytes() != _json_bytes(record):
            raise ControllerError(f"frozen command is missing/non-identical: {path}")
    expected_qa = _qa_required_payload(contract, out_dir, commands)
    if not qa_path.is_file() or qa_path.read_bytes() != _json_bytes(expected_qa):
        raise ControllerError("INDEPENDENT_QA_REQUIRED.json is missing/non-identical")
    command_bindings = [
        {
            "seed": record["seed"],
            "arm": record["arm"],
            "path": str(_command_path(out_dir, record["seed"], record["arm"])),
            "sha256": _sha256(_command_path(out_dir, record["seed"], record["arm"])),
        }
        for record in commands
    ]
    expected_prepared = {
        "schema": "controlled_real10k_20k_prepared_receipt_v4",
        "status": "PREPARED_AWAITING_INDEPENDENT_QA",
        "training_launch_authorized": False,
        "run_contract": {"path": str(contract_path), "sha256": _sha256(contract_path)},
        "commands": command_bindings,
        "independent_qa_required": {"path": str(qa_path), "sha256": _sha256(qa_path)},
        "challenge_nonce": contract["qa_challenge_nonce"],
        "exact_paired_seeds": list(EXACT_PAIRED_SEEDS),
        "numerical_metrics_accessed": False,
        "fresh_emx_accessed": False,
    }
    prepared_file = _file(prepared_path, "prepared receipt")
    if prepared_file.read_bytes() != _json_bytes(expected_prepared):
        raise ControllerError("prepared receipt is missing/non-identical")
    package_index = _file(index_path, "package SHA index")
    index = _parse_sha_index(package_index, out_dir, "package SHA index")
    required = {"run_contract.json", "INDEPENDENT_QA_REQUIRED.json", "receipts/PREPARED_RECEIPT.json"} | {
        f"commands/seed_{seed}_{arm}.json" for seed in EXACT_PAIRED_SEEDS for arm in ARM_ORDER
    }
    if set(index) != required:
        raise ControllerError("package SHA index has missing or unexpected prepare artifacts")
    if list(index) != sorted(required):
        raise ControllerError("package SHA index order is not the frozen lexical order")
    return {
        "contract_path": contract_path,
        "prepared_path": prepared_path,
        "qa_required_path": qa_path,
        "package_index_path": package_index,
        "commands": commands,
    }


def _binding(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path)}


def _validate_runtime_attestation(
    path: Path,
    contract: dict[str, Any],
    *,
    require_terminal_pass: bool,
) -> dict[str, Any]:
    artifact = _file(path, "descriptor runtime attestation")
    try:
        lines = artifact.read_text(encoding="ascii").splitlines()
        records = [
            _strict_json_loads(line, f"descriptor runtime attestation line {index}")
            for index, line in enumerate(lines, start=1)
        ]
    except (OSError, UnicodeError, ControllerError) as exc:
        raise ControllerError(f"cannot parse descriptor runtime attestation: {exc}") from exc
    if any(type(record) is not dict for record in records):
        raise ControllerError("descriptor runtime attestation record is not an object")
    if len(records) not in ({2} if require_terminal_pass else {1, 2}):
        raise ControllerError("descriptor runtime attestation record count is invalid")
    closure = contract["runtime"]["descriptor_closure"]
    common = {
        "schema": runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "trainer",
        "manifest_sha256": closure["manifest"]["sha256"],
        "pure_archive_sha256": closure["pure_archive"]["sha256"],
        "bootstrap_sha256": closure["bootstrap"]["sha256"],
    }
    startup = records[0]
    expected_startup_keys = {
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
    if set(startup) != expected_startup_keys:
        raise ControllerError("descriptor runtime startup attestation keyset differs")
    if any(startup.get(key) != value for key, value in common.items()):
        raise ControllerError("descriptor runtime startup attestation identity mismatch")
    if (
        startup.get("status") != "PASS_DESCRIPTOR_CLOSED_STARTUP"
        or startup.get("entrypoint_sha256") != contract["trainer"]["sha256"]
        or startup.get("numpy_version") != contract["runtime"]["numpy_version"]
        or startup.get("python_flags")
        != {"isolated": 1, "no_site": 1, "dont_write_bytecode": True}
        or startup.get("site_initialization_disabled") is not True
        or startup.get("external_package_fallback_allowed") is not False
    ):
        raise ControllerError("descriptor runtime startup isolation evidence mismatch")
    expected_native_libraries = {
        record["soname"]: record["sha256"]
        for record in closure["native_libraries"]
    }
    expected_native_extensions = {
        record["module"]: record["sha256"]
        for record in closure["native_extensions"]
    }
    if (
        startup.get("native_library_sha256") != expected_native_libraries
        or startup.get("native_extension_sha256") != expected_native_extensions
        or startup.get("system_library_allowlist")
        != list(runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST)
    ):
        raise ControllerError("descriptor runtime native attestation maps differ")
    modules = startup.get("module_origins")
    required_modules = {
        "numpy",
        "rfic_transformer_inverse_design",
        "rfic_transformer_inverse_design.controlled_real10k_20k_contract",
        "rfic_transformer_inverse_design.model_splitting",
        runtime_bootstrap.BOOTSTRAP_MODULE,
    }
    if (
        not isinstance(modules, dict)
        or not required_modules.issubset(modules)
        or any(
            not isinstance(record, dict)
            or set(record) != {"kind", "origin", "sha256"}
            or record.get("kind") not in {"sealed_pure_zip", "sealed_native_extension"}
            or not _is_sha(record.get("sha256"))
            or not str(record.get("origin") or "").startswith(
                ("descriptor-zip:/proc/self/fd/", "/proc/self/fd/")
            )
            for record in modules.values()
        )
    ):
        raise ControllerError("descriptor runtime controlled module origins are not exact")
    if len(records) == 2:
        terminal = records[1]
        expected_terminal_keys = {
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
            set(terminal) != expected_terminal_keys
            or any(terminal.get(key) != value for key, value in common.items())
            or terminal.get("status") != "PASS_DESCRIPTOR_CLOSED_TERMINAL"
            or type(terminal.get("exit_code")) is not int
            or terminal.get("exit_code") != 0
            or terminal.get("external_package_fallback_allowed") is not False
            or terminal.get("system_library_allowlist")
            != list(runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST)
            or not isinstance(terminal.get("module_origins"), dict)
            or not required_modules.issubset(terminal["module_origins"])
            or any(
                type(record) is not dict
                or set(record) != {"kind", "origin", "sha256"}
                or record.get("kind")
                not in {"sealed_pure_zip", "sealed_native_extension"}
                or not _is_sha(record.get("sha256"))
                or not str(record.get("origin") or "").startswith(
                    ("descriptor-zip:/proc/self/fd/", "/proc/self/fd/")
                )
                for record in terminal["module_origins"].values()
            )
        ):
            raise ControllerError("descriptor runtime terminal attestation mismatch")
    elif require_terminal_pass:
        raise ControllerError("descriptor runtime terminal PASS attestation is missing")
    return {
        "path": str(artifact),
        "sha256": _sha256(artifact),
        "record_count": len(records),
        "startup_status": startup["status"],
        "terminal_status": records[1]["status"] if len(records) == 2 else None,
    }


def _expected_go_bindings(contract: dict[str, Any], package: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    material = contract["materialization"]
    outer = material["outer_materialization_authority"]
    return {
        "run_contract": _binding(package["contract_path"]),
        "prepared_receipt": _binding(package["prepared_path"]),
        "independent_qa_required": _binding(package["qa_required_path"]),
        "package_sha_index": _binding(package["package_index_path"]),
        "runner": dict(contract["runner"]),
        "shared_contract": dict(contract["shared_contract"]),
        "trainer": dict(contract["trainer"]),
        "python": dict(contract["runtime"]["python"]),
        "numpy_version": contract["runtime"]["numpy_version"],
        "runtime_bootstrap": dict(contract["runtime"]["bootstrap"]),
        "runtime_dependency_closure": dict(
            contract["runtime"]["descriptor_closure"]
        ),
        "controlled_singleton": dict(contract["controlled_singleton"]),
        "trainer_launch_contract": dict(contract["process_contract"]["trainer_launch"]),
        "materialization_summary": dict(material["summary"]),
        "materialization_receipt": dict(material["receipt"]),
        "materialization_independent_qa_required": dict(material["independent_qa_required"]),
        "materialization_sha_index": dict(material["sha_index"]),
        "materialization_complete_receipt": dict(outer["complete"]),
        "materialization_candidate_manifest": dict(outer["candidate_manifest"]),
        "materialization_candidate_sha_index": dict(outer["candidate_sha_index"]),
        "materialization_go_authority": dict(outer["materialization_go_authority"]),
        "materialized_artifacts": {
            key: {"path": value["path"], "sha256": value["sha256"]}
            for key, value in material["artifacts"].items()
        },
        "exact_paired_seeds": list(EXACT_PAIRED_SEEDS),
        "out_dir": str(out_dir),
    }


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ControllerError(f"GO {label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ControllerError(f"GO {label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ControllerError(f"GO {label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _validate_go(path: Path, expected_sha: str, contract: dict[str, Any], package: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    try:
        path.relative_to(out_dir)
    except ValueError:
        pass
    else:
        raise ControllerError("independent-QA GO receipt must be external to the execution output directory")
    sha = _require_sha(path, expected_sha, "independent-QA GO receipt")
    receipt = _json(path, "independent-QA GO receipt")
    expected_top_level_keys = {
        "schema",
        "status",
        "verdict",
        "scope",
        "nonce",
        "issued_utc",
        "expires_utc",
        "reviewer",
        "findings",
        "checks",
        "bindings",
    }
    if set(receipt) != expected_top_level_keys:
        raise ControllerError("independent-QA GO top-level keyset is not exact")
    exact = {
        "schema": GO_SCHEMA,
        "status": GO_STATUS,
        "verdict": GO_VERDICT,
        "scope": GO_SCOPE,
        "nonce": contract["qa_challenge_nonce"],
    }
    for key, value in exact.items():
        _require_exact_json_equal(
            receipt.get(key), value, f"independent-QA GO {key}"
        )
    reviewer = receipt.get("reviewer") or {}
    if (
        set(reviewer) != {"role", "identity", "independent_of_builder_and_execution"}
        or reviewer.get("role") != "independent_qa"
        or reviewer.get("independent_of_builder_and_execution") is not True
        or not isinstance(reviewer.get("identity"), str)
        or not reviewer["identity"].strip()
    ):
        raise ControllerError("GO does not identify an independent reviewer")
    _require_exact_json_equal(
        receipt.get("findings"),
        {"p0": 0, "p1": 0},
        "independent-QA GO findings",
    )
    checks = receipt.get("checks")
    required_checks = {
        "result_blind_review": True,
        "materialization_closure_exact": True,
        "training_contract_exact": True,
        "validation_only_test_sealed": True,
        "six_arm_scope_only": True,
        "no_fresh_emx_authority": True,
    }
    _require_exact_json_equal(checks, required_checks, "independent-QA GO checks")
    issued = _parse_time(receipt.get("issued_utc"), "issued_utc")
    expires = _parse_time(receipt.get("expires_utc"), "expires_utc")
    now = datetime.now(timezone.utc)
    if issued > now or now >= expires or expires <= issued or expires - issued > MAX_GO_VALIDITY or now - issued > MAX_GO_VALIDITY:
        raise ControllerError("independent-QA GO is future-dated, expired, stale, or overlong")
    expected_bindings = _expected_go_bindings(contract, package, out_dir)
    try:
        _require_exact_json_equal(
            receipt.get("bindings"), expected_bindings, "independent-QA GO bindings"
        )
    except ControllerError as exc:
        raise ControllerError(f"independent-QA GO bindings are not exact: {exc}") from exc
    return {"path": str(path), "sha256": sha, "nonce": receipt["nonce"], "expires_utc": expires.isoformat()}


def _verify_closure(contract: dict[str, Any], package: dict[str, Any], out_dir: Path) -> None:
    if _HELD_PYTHON_FD is None:
        raise ControllerError("pinned Python executable descriptor is not held")
    _verify_python_executable_descriptor(
        _HELD_PYTHON_FD, contract["runtime"]["python"]
    )
    _verify_python_path_binding(contract["runtime"]["python"])
    outer = contract["materialization"]["outer_materialization_authority"]
    bindings = [
        contract["runner"], contract["trainer"],
        contract["runtime"]["python"], contract["runtime"]["bootstrap"],
        contract["controlled_singleton"],
        contract["materialization"]["summary"], contract["materialization"]["receipt"],
        contract["materialization"]["independent_qa_required"],
        contract["materialization"]["sha_index"],
        *contract["materialization"]["artifacts"].values(),
        outer["complete"], outer["candidate_manifest"], outer["candidate_sha_index"],
        outer["materialization_go_authority"],
        *outer["candidate_bindings"].values(),
    ]
    for record in bindings:
        _require_sha(_file(record["path"], "frozen closure artifact"), record["sha256"], "frozen closure artifact")
    if _HELD_SINGLETON_FD is None:
        raise ControllerError("controlled package singleton lock is not held")
    _verify_singleton_lock_descriptor(
        _HELD_SINGLETON_FD, contract["controlled_singleton"]
    )
    closure = contract["runtime"]["descriptor_closure"]
    try:
        observed_closure = runtime_bootstrap.audit_runtime_closure_paths(
            Path(closure["manifest"]["path"]),
            closure["manifest"]["sha256"],
            Path(closure["tree_root"]),
            Path(closure["bootstrap"]["path"]),
            closure["bootstrap"]["sha256"],
        )
    except runtime_bootstrap.RuntimeClosureError as exc:
        raise ControllerError(f"runtime dependency closure changed: {exc}") from exc
    if observed_closure != closure:
        raise ControllerError("runtime dependency closure identity changed")
    _require_sha(package["contract_path"], _sha256(package["contract_path"]), "run contract")
    _prepare_or_verify(out_dir, contract)


def _load1() -> float:
    return float(os.getloadavg()[0])


def _child_preexec() -> None:
    os.setpriority(os.PRIO_PROCESS, 0, CHILD_NICE)


def _boot_id() -> str | None:
    path = Path("/proc/sys/kernel/random/boot_id")
    return path.read_text(encoding="ascii").strip() if path.is_file() else None


def _controlled_process_roles(
    argv: Sequence[str], *, pid: int | None = None
) -> list[str]:
    """Identify the executed controlled script, not paths passed as data args."""

    if not argv:
        return []
    direct = Path(argv[0]).name
    if direct in CONTROLLED_PROCESS_BASENAMES:
        return [direct]
    if "python" not in direct.lower():
        return []
    bootstrap_index = 1 + len(PYTHON_ISOLATION_FLAGS)
    if (
        tuple(argv[1:bootstrap_index]) == PYTHON_ISOLATION_FLAGS
        and len(argv) >= bootstrap_index + 5
        and argv[bootstrap_index]
        == f"/proc/self/fd/{runtime_bootstrap.BOOTSTRAP_FD}"
        and argv[bootstrap_index + 1 : bootstrap_index + 3]
        == ["--request-fd", str(runtime_bootstrap.REQUEST_FD)]
        and argv[bootstrap_index + 3] == "--entrypoint"
    ):
        entrypoint_role = argv[bootstrap_index + 4]
        role_map = {
            "materialization": "run_controlled_real10k_20k_materialization.py",
            "runner": "run_controlled_real10k_20k_paired.py",
            "trainer": "train_physical_feature_tandem_inverse.py",
            "evaluator": "evaluate_controlled_real10k_20k_common.py",
            "native_smoke": "controlled_real10k_20k_mars_native_smoke.py",
        }
        role = role_map.get(entrypoint_role)
        if role is None or pid is None:
            return []
        fd_root = Path("/proc") / str(pid) / "fd"
        try:
            manifest_payload: bytes | None = None
            request_fd = os.open(
                fd_root / str(runtime_bootstrap.REQUEST_FD), os.O_RDONLY
            )
            try:
                request_payload = runtime_bootstrap._require_sealed_descriptor(
                    request_fd, "process launch request"
                )
            finally:
                os.close(request_fd)
            request = runtime_bootstrap._validate_launch_request(
                runtime_bootstrap._json_object(
                    request_payload, "process launch request"
                )
            )
            if request["entrypoint"] != entrypoint_role:
                raise ControllerError("bootstrap process entrypoint/request mismatch")
            for descriptor_number, expected_sha, label in (
                (
                    runtime_bootstrap.BOOTSTRAP_FD,
                    request["expected_bootstrap_sha256"],
                    "bootstrap",
                ),
                (
                    runtime_bootstrap.MANIFEST_FD,
                    request["expected_manifest_sha256"],
                    "manifest",
                ),
                (
                    runtime_bootstrap.PURE_ARCHIVE_FD,
                    request["expected_pure_archive_sha256"],
                    "pure archive",
                ),
            ):
                held = os.open(fd_root / str(descriptor_number), os.O_RDONLY)
                try:
                    payload = runtime_bootstrap._require_sealed_descriptor(
                        held, f"process {label}"
                    )
                finally:
                    os.close(held)
                if hashlib.sha256(payload).hexdigest() != expected_sha:
                    raise ControllerError(
                        f"bootstrap process {label} descriptor SHA mismatch"
                    )
                if descriptor_number == runtime_bootstrap.MANIFEST_FD:
                    manifest_payload = payload
            if manifest_payload is None:
                raise ControllerError("bootstrap process manifest descriptor was not read")
            manifest = runtime_bootstrap.parse_runtime_manifest_bytes(
                manifest_payload, request["expected_manifest_sha256"]
            )
            executable_payload = (Path("/proc") / str(pid) / "exe").read_bytes()
            if (
                hashlib.sha256(executable_payload).hexdigest()
                != manifest["python"]["executable_sha256"]
            ):
                raise ControllerError("bootstrap process Python executable SHA mismatch")
        except (FileNotFoundError, ProcessLookupError):
            return []
        except (OSError, runtime_bootstrap.RuntimeClosureError) as exc:
            raise ControllerError(
                f"cannot prove bootstrap process descriptor identity for PID {pid}: {exc}"
            ) from exc
        return [role]
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "-c":
            return []
        if token == "-m":
            if index + 1 >= len(argv):
                return []
            module_stem = argv[index + 1].rsplit(".", 1)[-1]
            candidate = module_stem + ".py"
            return [candidate] if candidate in CONTROLLED_PROCESS_BASENAMES else []
        if token.startswith("-"):
            index += 1
            continue
        candidate = Path(token).name
        return [candidate] if candidate in CONTROLLED_PROCESS_BASENAMES else []
    return []


def _process_exclusivity_audit() -> dict[str, Any]:
    """Snapshot same-UID Linux processes and reject parallel controlled work."""

    if not sys.platform.startswith("linux"):
        raise ControllerError("production execute requires Linux /proc process exclusivity audit")
    proc_root = Path("/proc")
    boot_id = _boot_id()
    if not proc_root.is_dir() or not boot_id:
        raise ControllerError("Linux /proc or boot_id is unavailable for exact process audit")
    controller_pid = os.getpid()
    uid = os.getuid()
    scanned_same_uid = 0
    matched: list[dict[str, Any]] = []
    try:
        entries = sorted(
            (entry for entry in proc_root.iterdir() if entry.name.isdigit()),
            key=lambda entry: int(entry.name),
        )
    except OSError as exc:
        raise ControllerError("cannot enumerate Linux /proc for process exclusivity") from exc
    for entry in entries:
        pid = int(entry.name)
        try:
            if entry.stat().st_uid != uid:
                continue
            cmdline = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            raise ControllerError(
                f"cannot inspect same-UID process {pid} during exclusivity audit"
            ) from exc
        scanned_same_uid += 1
        argv = [
            token.decode("utf-8", errors="surrogateescape")
            for token in cmdline.rstrip(b"\0").split(b"\0")
            if token
        ]
        roles = _controlled_process_roles(argv, pid=pid)
        if roles:
            matched.append(
                {
                    "pid": pid,
                    "roles": roles,
                    "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
                }
            )
    current_matches = [record for record in matched if record["pid"] == controller_pid]
    if len(current_matches) != 1 or "run_controlled_real10k_20k_paired.py" not in current_matches[0]["roles"]:
        raise ControllerError("current controller PID is not exactly identifiable in Linux /proc")
    duplicates = [record for record in matched if record["pid"] != controller_pid]
    return {
        "schema": PROCESS_AUDIT_SCHEMA,
        "status": "PASS" if not duplicates else "FAIL_DUPLICATE_CONTROLLED_PROCESS",
        "audit_mode": "linux_procfs_current_uid_exact",
        "uid": uid,
        "controller_pid": controller_pid,
        "boot_id": boot_id,
        "scanned_same_uid_pid_count": scanned_same_uid,
        "matched_controlled_processes": matched,
        "duplicates": duplicates,
    }


def _require_process_exclusivity(
    out_dir: Path, *, seed: int, arm: str, checkpoint: str
) -> dict[str, Any]:
    try:
        audit = _process_exclusivity_audit()
    except ControllerError as exc:
        event = _record_event(
            out_dir,
            "PROCESS_EXCLUSIVITY_AUDIT_FAIL",
            str(exc),
            seed=seed,
            arm=arm,
            checkpoint=checkpoint,
        )
        raise ControllerError(f"process exclusivity audit failed; receipt={event}: {exc}") from exc
    if (
        audit.get("schema") != PROCESS_AUDIT_SCHEMA
        or audit.get("status") != "PASS"
        or audit.get("audit_mode") != "linux_procfs_current_uid_exact"
        or audit.get("controller_pid") != os.getpid()
        or audit.get("uid") != os.getuid()
        or not audit.get("boot_id")
        or audit.get("duplicates") != []
    ):
        event = _record_event(
            out_dir,
            "DUPLICATE_CONTROLLED_PROCESS_FAIL",
            "another same-UID controlled builder/runner/trainer process exists",
            seed=seed,
            arm=arm,
            checkpoint=checkpoint,
            audit=audit,
        )
        raise ControllerError(f"duplicate controlled process detected; receipt={event}")
    return audit


def _stored_process_audit_valid(audit: Any) -> bool:
    if not isinstance(audit, dict) or set(audit) != {
        "schema",
        "status",
        "audit_mode",
        "uid",
        "controller_pid",
        "boot_id",
        "scanned_same_uid_pid_count",
        "matched_controlled_processes",
        "duplicates",
    }:
        return False
    matches = audit.get("matched_controlled_processes")
    controller_pid = audit.get("controller_pid")
    if (
        audit.get("schema") != PROCESS_AUDIT_SCHEMA
        or audit.get("status") != "PASS"
        or audit.get("audit_mode") != "linux_procfs_current_uid_exact"
        or type(audit.get("uid")) is not int
        or type(controller_pid) is not int
        or controller_pid <= 0
        or not isinstance(audit.get("boot_id"), str)
        or not audit.get("boot_id")
        or type(audit.get("scanned_same_uid_pid_count")) is not int
        or audit.get("scanned_same_uid_pid_count") < 1
        or not isinstance(matches, list)
        or audit.get("duplicates") != []
    ):
        return False
    current = [record for record in matches if record.get("pid") == controller_pid]
    return (
        len(current) == 1
        and "run_controlled_real10k_20k_paired.py" in (current[0].get("roles") or [])
        and _is_sha(current[0].get("cmdline_sha256"))
        and all(
            isinstance(record, dict)
            and set(record) == {"pid", "roles", "cmdline_sha256"}
            and type(record.get("pid")) is int
            and isinstance(record.get("roles"), list)
            and bool(record.get("roles"))
            and set(record["roles"]).issubset(CONTROLLED_PROCESS_BASENAMES)
            and _is_sha(record.get("cmdline_sha256"))
            for record in matches
        )
    )


def _linux_process_identity(pid: int, expected_argv: Sequence[str], python: Path) -> dict[str, Any]:
    proc = Path("/proc") / str(pid)
    if proc.is_dir():
        stat_text = (proc / "stat").read_text(encoding="utf-8")
        close_paren = stat_text.rfind(")")
        if close_paren < 0:
            raise ControllerError("spawned process /proc stat has no command terminator")
        # Fields after ``comm`` start at documented proc(5) field 3.  The
        # process start time is field 22, hence zero-based offset 19 here.
        stat_after_comm = stat_text[close_paren + 2 :].split()
        if len(stat_after_comm) <= 19:
            raise ControllerError("spawned process /proc stat is truncated")
        cmdline = (proc / "cmdline").read_bytes()
        exe = (proc / "exe").resolve()
        expected_cmdline = b"\0".join(token.encode("utf-8") for token in expected_argv) + b"\0"
        if cmdline != expected_cmdline:
            raise ControllerError("spawned process /proc cmdline does not match exact argv")
        if exe != python.resolve():
            raise ControllerError("spawned process executable does not match frozen Python")
        return {
            "identity_source": "linux_procfs_exact",
            "boot_id": _boot_id(),
            "proc_startticks": int(stat_after_comm[19]),
            "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
            "exe": {"path": str(exe), "sha256": _sha256(exe)},
        }
    fallback = b"\0".join(token.encode("utf-8") for token in expected_argv) + b"\0"
    return {
        "identity_source": "non_linux_frozen_spawn_argv_fallback",
        "boot_id": None,
        "proc_startticks": None,
        "cmdline_sha256": hashlib.sha256(fallback).hexdigest(),
        "exe": {"path": str(python), "sha256": _sha256(python)},
    }


def _flag_map(argv: Sequence[str]) -> dict[str, str]:
    if len(argv) < 3 or not os.path.isabs(argv[0]):
        raise ControllerError("trainer entrypoint argv lacks an absolute frozen script path")
    flags: dict[str, str] = {}
    index = 1
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--") or index + 1 >= len(argv):
            raise ControllerError(f"trainer argv is not an exact flag/value sequence at {token!r}")
        if token in flags or argv[index + 1].startswith("--"):
            raise ControllerError(f"trainer argv has duplicate/missing value for {token}")
        flags[token] = argv[index + 1]
        index += 2
    return flags


def _require_exact_launch_record(
    command: dict[str, Any],
    contract: dict[str, Any],
    out_dir: Path,
    seed: int,
    arm: str,
) -> dict[str, str]:
    environment = _require_exact_effective_environment(
        command.get("effective_environment"), contract
    )
    expected = {
        "schema": "controlled_real10k_20k_exact_trainer_argv_v3",
        "run_contract_sha256": hashlib.sha256(_json_bytes(contract)).hexdigest(),
        "seed": seed,
        "arm": arm,
        "argv": _trainer_argv(contract, out_dir, seed, arm),
        "entrypoint_argv": _trainer_entrypoint_argv(contract, out_dir, seed, arm),
        "runtime_dependency_closure": dict(
            contract["runtime"]["descriptor_closure"]
        ),
        "runtime_attestation_path": str(
            out_dir
            / "receipts"
            / f"seed_{seed}_{arm}"
            / "attempt_0001"
            / "RUNTIME_ATTESTATION.jsonl"
        ),
        "python_isolation_flags": list(PYTHON_ISOLATION_FLAGS),
        "effective_environment": environment,
        "effective_environment_sha256": _child_environment_sha256(environment),
        "shell": False,
        "evaluation_mode": "validation_only",
        "test_access_authorized": False,
    }
    if command != expected:
        raise ControllerError("trainer command/argv/environment launch record is not exact")
    process_argv = command["argv"]
    entrypoint_argv = command["entrypoint_argv"]
    if (
        process_argv[0] != contract["runtime"]["python"]["path"]
        or tuple(process_argv[1 : 1 + len(PYTHON_ISOLATION_FLAGS)])
        != PYTHON_ISOLATION_FLAGS
        or process_argv[1 + len(PYTHON_ISOLATION_FLAGS)]
        != f"/proc/self/fd/{runtime_bootstrap.BOOTSTRAP_FD}"
        or entrypoint_argv[0] != contract["trainer"]["path"]
    ):
        raise ControllerError("trainer argv executable/isolation/script prefix differs")
    _flag_map(entrypoint_argv)
    return environment


def _arguments_match_argv(arguments: dict[str, Any], flags: dict[str, str]) -> bool:
    for flag, expected in flags.items():
        key = flag.removeprefix("--").replace("-", "_")
        if key not in arguments:
            return False
        actual = arguments[key]
        if expected == "inf":
            if actual is not None:
                return False
        elif isinstance(actual, bool):
            if str(actual).lower() != expected.lower():
                return False
        elif type(actual) is int:
            try:
                if actual != int(expected):
                    return False
            except ValueError:
                return False
        elif isinstance(actual, float):
            try:
                if actual != float(expected):
                    return False
            except ValueError:
                return False
        elif str(actual) != expected:
            return False
    forbidden_flags = {
        "--forward-initial-weights",
        "--forward-initial-weights-sha256",
        "--forward-initial-summary",
        "--forward-initial-summary-sha256",
        "--inverse-initial-weights",
        "--inverse-initial-weights-sha256",
        "--inverse-initial-summary",
        "--inverse-initial-summary-sha256",
        "--freeze-transported-forward",
    }
    forbidden_arguments = {
        flag.removeprefix("--").replace("-", "_") for flag in forbidden_flags
    }
    return not (set(flags) & forbidden_flags) and all(
        arguments.get(key) is None
        or arguments.get(key) is False
        or (type(arguments.get(key)) is str and arguments.get(key) == "")
        for key in forbidden_arguments
    )


def _summary_checks(
    summary: dict[str, Any], contract: dict[str, Any], command: dict[str, Any], arm: str, seed: int
) -> dict[str, bool]:
    args = summary.get("arguments") or {}
    comparison = summary.get("model_comparison_contract") or {}
    architecture = comparison.get("architecture") or {}
    optimization = comparison.get("optimization") or {}
    comparison_normalization = comparison.get("normalization") or {}
    comparison_evaluation = comparison.get("evaluation") or {}
    loss = comparison.get("loss") or {}
    comparison_split = comparison.get("split") or {}
    split = summary.get("split_audit") or {}
    rows = split.get("row_counts") or {}
    budget = summary.get("optimizer_budget_contract") or {}
    realized = budget.get("realized") or {}
    sampler = summary.get("training_batch_sampler_contract") or {}
    sampler_realized = sampler.get("realized_training_budget") or {}
    evaluation = summary.get("evaluation_isolation") or {}
    test_contract = summary.get("test_access_contract") or {}
    thresholds = summary.get("acceptance_thresholds") or {}
    response_loss = summary.get("response_loss_contract") or {}
    stage_resume = summary.get("stage_checkpoint_resume") or {}
    initialization_forward = summary.get("method", {}).get("forward_proxy_initialization") or {}
    initialization_inverse = summary.get("method", {}).get("inverse_initialization") or {}
    material = contract["materialization"]
    artifacts = material["artifacts"]
    expected = material["counts"][arm]
    argv = command["entrypoint_argv"]
    flags = _flag_map(argv)
    history_path = Path(str(summary.get("history_csv") or ""))
    history: list[dict[str, str]] = []
    validation_event_counts: dict[str, int] = {}
    validation_update_sequences: dict[str, list[int]] = {}
    if history_path.is_file():
        with history_path.open(newline="", encoding="utf-8") as handle:
            history = list(csv.DictReader(handle))
        for stage in ("forward_proxy", "tandem_inverse"):
            stage_rows = [row for row in history if row.get("stage") == stage]
            validation_event_counts[stage] = len(stage_rows)
            try:
                validation_update_sequences[stage] = [
                    int(row["optimizer_updates"]) for row in stage_rows
                ]
            except (KeyError, TypeError, ValueError):
                validation_update_sequences[stage] = []
    else:
        validation_event_counts = {"forward_proxy": -1, "tandem_inverse": -1}
        validation_update_sequences = {"forward_proxy": [], "tandem_inverse": []}
    best = summary.get("best_optimizer_updates") or {}
    checks = {
        "overall_review_required": summary.get("overall_status") == "COMPLETE_REVIEW_REQUIRED",
        "execution_pass": summary.get("execution_status") == "PASS",
        "quality_validation_only": summary.get("quality_status") == "REVIEW_REQUIRED_VALIDATION_ONLY",
        "acceptance_ineligible": summary.get("eligible_for_checkpoint_model_acceptance") is False
        and summary.get("eligible_for_model_success_claim") is False,
        "training_csv_exact": Path(str(summary.get("training_csv") or "")).resolve()
        == Path(artifacts[f"{arm}_csv"]["path"])
        and summary.get("training_csv_sha256") == artifacts[f"{arm}_csv"]["sha256"],
        "source_count_exact": _json_int_is(
            summary.get("training_count"), expected["source_rows"]
        ),
        "split_counts_exact": _json_int_is(
            rows.get("train"), expected["gradient_train"]
        )
        and _json_int_is(rows.get("validation"), expected["validation"])
        and _json_int_is(rows.get("test"), expected["test"]),
        "holdout_exact": (split.get("fixed_common_holdout_manifest") or {}).get("sha256")
        == artifacts["common_holdout"]["sha256"],
        "normalization_exact": (summary.get("normalization_contract") or {}).get("sha256")
        == artifacts["fixed_normalization"]["sha256"]
        and (summary.get("normalization_contract") or {}).get("mode")
        == "external_declared_midpoint_half_range"
        and (summary.get("normalization_contract") or {}).get("schema") == NORMALIZATION_SCHEMA
        and (summary.get("normalization_contract") or {}).get("train_arm_specific_statistics_used") is False
        and (summary.get("normalization_contract") or {}).get("large_arm_empirical_statistics_used") is False,
        "trainer_exact": comparison.get("trainer_implementation_sha256") == contract["trainer"]["sha256"],
        "columns_exact": comparison.get("input_columns") == list(INPUT_COLUMNS)
        and comparison.get("geometry_columns") == list(GEOMETRY_COLUMNS),
        "architecture_exact": architecture.get("forward_hidden_widths") == [256, 256, 256]
        and architecture.get("inverse_hidden_widths") == [256, 256, 256]
        and architecture.get("inverse_geometry_projection") == "independent_sigmoid",
        "fresh_initialization_exact": optimization.get("forward_initialization", {}).get("mode") == "random"
        and optimization.get("inverse_initialization", {}).get("mode") == "random"
        and not optimization.get("forward_initialization", {}).get("source_weights_sha256")
        and not optimization.get("inverse_initialization", {}).get("source_weights_sha256")
        and initialization_forward.get("mode") == "random"
        and initialization_inverse.get("mode") == "random",
        "seed_exact": _json_int_is(args.get("seed"), seed)
        and _json_int_is(args.get("split_seed"), seed),
        "optimizer_config_exact": _json_int_is(optimization.get("batch_size"), 1024)
        and optimization.get("training_batch_sampler") == "row_uniform"
        and optimization.get("exact_update_batch_mode") == "continuous_permutation_full_batch"
        and _json_int_is(optimization.get("forward_epochs"), 160)
        and _json_int_is(optimization.get("inverse_epochs"), 180)
        and _json_int_is(optimization.get("patience"), 20)
        and _json_int_is(optimization.get("forward_max_optimizer_updates"), 1200)
        and _json_int_is(optimization.get("inverse_max_optimizer_updates"), 1200)
        and _json_int_is(optimization.get("validation_every_optimizer_updates"), 20)
        and _json_float_is(optimization.get("learning_rate"), 0.001)
        and optimization.get("training_learning_rate_schedule") == "constant"
        and _json_float_is(
            optimization.get("training_final_learning_rate_fraction"), 0.1
        )
        and _json_float_is(optimization.get("weight_decay"), 0.000001),
        "comparison_split_normalization_evaluation_exact": comparison_split.get("mode")
        == "fixed_common_holdout_manifest"
        and comparison_split.get("fixed_common_holdout_manifest_sha256")
        == artifacts["common_holdout"]["sha256"]
        and _json_float_is(comparison_split.get("validation_fraction"), 0.15)
        and _json_float_is(comparison_split.get("test_fraction"), 0.10)
        and _json_int_is(
            comparison_split.get("physical_cell_bins"), PHYSICAL_CELL_BINS
        )
        and comparison_split.get("physical_cell_lower")
        == ",".join(format(value, ".17g") for value in INPUT_LOWER)
        and comparison_split.get("physical_cell_upper")
        == ",".join(format(value, ".17g") for value in INPUT_UPPER)
        and comparison_normalization.get("mode") == "external_declared_midpoint_half_range"
        and comparison_normalization.get("fixed_contract_sha256")
        == artifacts["fixed_normalization"]["sha256"]
        and _json_exact_is(
            comparison_evaluation,
            {"mode": "validation_only", "test_access_allowed": False},
        ),
        "realized_budget_exact": budget.get("mode") == "fixed_optimizer_updates"
        and budget.get("early_stopping_enabled") is False
        and budget.get("exact_update_batch_mode") == "continuous_permutation_full_batch"
        and _json_int_is(budget.get("validation_every_optimizer_updates"), 20)
        and budget.get("response_schedule_domain") == "optimizer_update"
        and budget.get("response_schedule")
        == {
            "weight_schedule": "warmup_ramp_adaptive_ema",
            "unit_source": "absolute_optimizer_updates",
            "total_units": 1200,
            "warmup_units": 60,
            "ramp_units": 300,
        }
        and _json_int_is(
            (budget.get("forward") or {}).get("target_optimizer_updates"), 1200
        )
        and _json_int_is(
            (budget.get("inverse") or {}).get("target_optimizer_updates"), 1200
        )
        and _json_int_is(
            (budget.get("forward") or {}).get("target_real_row_draws"), 1200 * 1024
        )
        and _json_int_is(
            (budget.get("inverse") or {}).get("target_real_row_draws"), 1200 * 1024
        )
        and _json_int_is(realized.get("forward_optimizer_updates"), 1200)
        and _json_int_is(realized.get("inverse_optimizer_updates"), 1200)
        and realized.get("exact_update_budget_pass") is True,
        "sampler_contract_exact": sampler.get("family") == "row_uniform"
        and sampler.get("exact_update_batch_mode") == "continuous_permutation_full_batch"
        and _json_int_is(sampler.get("training_row_count"), expected["gradient_train"])
        and _json_int_is(sampler.get("draws_per_epoch"), expected["gradient_train"])
        and _json_int_is(sampler.get("batch_size"), 1024)
        and _json_int_is(
            sampler.get("optimizer_updates_per_epoch"),
            math.ceil(expected["gradient_train"] / 1024.0),
        )
        and _json_int_is(sampler.get("model_seed"), seed)
        and _json_int_is(sampler.get("forward_sampler_seed"), seed)
        and _json_int_is(sampler.get("inverse_sampler_seed"), seed + 1)
        and sampler.get("enabled") is False
        and sampler.get("train_only") is True
        and sampler.get("validation_or_test_rows_eligible_for_sampling") is False
        and sampler.get("synthetic_rows_created") is False
        and sampler.get("sampling_with_replacement") is False
        and sampler.get("all_exact_update_batches_have_configured_size") is True
        and _json_int_is(sampler_realized.get("forward_optimizer_updates"), 1200)
        and _json_int_is(sampler_realized.get("inverse_optimizer_updates"), 1200)
        and _json_int_is(
            sampler_realized.get("forward_real_row_draws"), 1200 * 1024
        )
        and _json_int_is(
            sampler_realized.get("inverse_real_row_draws"), 1200 * 1024
        ),
        "validation_events_exact": validation_event_counts
        == {"forward_proxy": 60, "tandem_inverse": 60}
        and len(history) == 120
        and validation_update_sequences
        == {
            "forward_proxy": list(range(20, 1201, 20)),
            "tandem_inverse": list(range(20, 1201, 20)),
        },
        "best_updates_validation_grid": all(
            type(best.get(stage)) is int
            and 20 <= best[stage] <= 1200
            and best[stage] % 20 == 0
            for stage in ("forward_proxy", "tandem_inverse")
        ),
        "loss_exact": loss.get("response_loss_family") == "mse"
        and loss.get("response_loss_scaling") == "declared_range"
        and _json_float_is(loss.get("response_weight"), 1.0)
        and _json_float_is(loss.get("geometry_anchor_weight"), 0.01)
        and _json_float_is(loss.get("topology_feasibility_weight"), 0.0)
        and loss.get("enforce_power_line_port_ground_overlap") is False
        and _json_float_is(loss.get("power_line_bar_offset_um"), 12.0)
        and _json_float_is(loss.get("power_line_shield_opening_clearance_um"), 10.0)
        and _json_float_is(loss.get("power_line_port_ground_overlap_um"), 10.0)
        and _json_float_is(
            loss.get("power_line_feed_training_safety_margin_um"), 0.0
        )
        and loss.get("q_target_semantics") == "exact"
        and _json_float_is(loss.get("q_minimum_margin_physical"), 0.0)
        and loss.get("relative_error_floors") == "0.5,0.5,5.0,0.05"
        and loss.get("response_semantic_loss_weights") == ""
        and loss.get("response_weight_schedule") == "warmup_ramp_adaptive_ema"
        and loss.get("response_schedule_domain") == "optimizer_update"
        and _json_float_is(loss.get("response_warmup_fraction"), 0.05)
        and _json_float_is(loss.get("response_ramp_fraction"), 0.25)
        and _json_int_is(loss.get("response_warmup_optimizer_updates"), 60)
        and _json_int_is(loss.get("response_ramp_optimizer_updates"), 300)
        and _json_float_is(loss.get("response_adaptive_ema_decay"), 0.95)
        and _json_float_is(loss.get("response_adaptive_min_multiplier"), 0.25)
        and _json_float_is(loss.get("response_adaptive_max_multiplier"), 4.0),
        "response_loss_contract_exact": response_loss.get("family") == "mse"
        and response_loss.get("scaling") == "declared_range"
        and response_loss.get("input_columns") == list(INPUT_COLUMNS)
        and response_loss.get("physical_spans")
        == dict(zip(INPUT_COLUMNS, (2.5, 2.5, 20.0, 0.8)))
        and _json_exact_is(
            response_loss.get("standardized_dimension_weights"),
            dict.fromkeys(INPUT_COLUMNS, 1.0),
        )
        and _json_float_is(response_loss.get("dimension_weight_mean"), 1.0)
        and response_loss.get("balanced_mse_bni") is None
        and response_loss.get("relative_mse") is None
        and response_loss.get("q_target_semantics") == "exact"
        and response_loss.get("target_semantics") == dict.fromkeys(INPUT_COLUMNS, "exact"),
        "validation_only_exact": summary.get("evaluation_mode") == "validation_only"
        and set(test_contract)
        == {
            "test_access_event_count",
            "test_access_timing",
            "test_used_for_training",
            "test_used_for_early_stopping",
            "test_used_for_model_or_hyperparameter_selection",
            "test_used_for_acceptance_threshold_tuning",
            "test_evaluator_called",
        }
        and _json_int_is(test_contract.get("test_access_event_count"), 0)
        and test_contract.get("test_access_timing") == "not_accessed"
        and test_contract.get("test_used_for_training") is False
        and test_contract.get("test_used_for_early_stopping") is False
        and test_contract.get("test_used_for_model_or_hyperparameter_selection") is False
        and test_contract.get("test_used_for_acceptance_threshold_tuning") is False
        and test_contract.get("test_evaluator_called") is False
        and evaluation.get("test_set_not_accessed") is True
        and evaluation.get("test_set_used_only_for_post_training_evaluation") is False
        and summary.get("metrics") == {}
        and thresholds.get("configured") is False
        and thresholds.get("max_forward_test_normalized_rmse") is None
        and thresholds.get("max_tandem_response_test_normalized_rmse") is None,
        "test_prediction_empty": False,
        "no_refinement": _json_int_is(args.get("local_refinement_steps"), 0),
        "checkpoint_selection_validation": args.get("inverse_checkpoint_selection") == "training_objective"
        and summary.get("method", {}).get("inverse_checkpoint_selection_uses_validation_only") is True,
        "resume_contract_first_launch": stage_resume.get("mode") == "resume_exact"
        and stage_resume.get("enabled") is True
        and _json_int_is(stage_resume.get("resumed_stage_count"), 0),
        "argv_all_effective_values_exact": _arguments_match_argv(args, flags)
        and flags.get("--evaluation-mode") == "validation_only"
        and flags.get("--training-csv") == artifacts[f"{arm}_csv"]["path"]
        and flags.get("--seed") == str(seed)
        and flags.get("--split-seed") == str(seed),
    }
    prediction_path = Path(str(summary.get("test_predictions_csv") or ""))
    if prediction_path.is_file():
        checks["test_prediction_empty"] = (
            prediction_path.stat().st_size == 0
            and _sha256(prediction_path) == hashlib.sha256(b"").hexdigest()
            and summary.get("test_predictions_csv_sha256") == hashlib.sha256(b"").hexdigest()
        )
    return checks


def _npz_scalar_string(archive: Any, key: str) -> str:
    array = np.asarray(archive[key])
    if array.shape != (1,) or array.dtype.kind not in {"U", "S"}:
        raise ControllerError(f"weights metadata {key} is not a one-string array")
    value = array.reshape(-1)[0]
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _audit_weights(
    path: Path,
    normalization_sha: str,
    gradient_train_rows: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    layer_shapes = {
        "forward_weight_0": (10, 256), "forward_weight_1": (256, 256),
        "forward_weight_2": (256, 256), "forward_weight_3": (256, 4),
        "forward_bias_0": (256,), "forward_bias_1": (256,),
        "forward_bias_2": (256,), "forward_bias_3": (4,),
        "inverse_weight_0": (4, 256), "inverse_weight_1": (256, 256),
        "inverse_weight_2": (256, 256), "inverse_weight_3": (256, 10),
        "inverse_bias_0": (256,), "inverse_bias_1": (256,),
        "inverse_bias_2": (256,), "inverse_bias_3": (10,),
    }
    expected_numeric = {
        "normalization__x_mean": np.asarray([(low + high) / 2 for low, high in zip(INPUT_LOWER, INPUT_UPPER)]),
        "normalization__x_scale": np.asarray([(high - low) / 2 for low, high in zip(INPUT_LOWER, INPUT_UPPER)]),
        "normalization__feature_lower": np.asarray(INPUT_LOWER),
        "normalization__feature_upper": np.asarray(INPUT_UPPER),
        "normalization__y_mean": np.asarray([(low + high) / 2 for low, high in zip(GEOMETRY_LOWER, GEOMETRY_UPPER)]),
        "normalization__y_scale": np.asarray([(high - low) / 2 for low, high in zip(GEOMETRY_LOWER, GEOMETRY_UPPER)]),
        "normalization__geometry_lower": -np.ones(len(GEOMETRY_COLUMNS)),
        "normalization__geometry_upper": np.ones(len(GEOMETRY_COLUMNS)),
        "normalization__response_loss_dimension_weights": np.ones(len(INPUT_COLUMNS)),
        "normalization__response_loss_physical_spans": np.asarray(INPUT_UPPER) - np.asarray(INPUT_LOWER),
        "training_sampler__draws_per_epoch": np.asarray([gradient_train_rows]),
        "training_sampler__optimizer_updates_per_epoch": np.asarray(
            [int(math.ceil(gradient_train_rows / 1024.0))]
        ),
        "optimizer_budget__forward_target_updates": np.asarray([1200]),
        "optimizer_budget__inverse_target_updates": np.asarray([1200]),
    }
    required_metadata = {
        "normalization_contract__mode": "external_declared_midpoint_half_range",
        "normalization_contract__sha256": normalization_sha,
        "training_sampler__family": "row_uniform",
        "optimizer_budget__mode": "fixed_optimizer_updates",
        "inverse_geometry_projection__mode": "independent_sigmoid",
    }
    dynamic_string_keys = {
        "training_sampler__fingerprint_sha256",
        "optimizer_budget__fingerprint_sha256",
        "inverse_geometry_projection__topology_contract_json",
    }
    sampler_fingerprint = str(
        (summary.get("training_batch_sampler_contract") or {}).get("fingerprint_sha256") or ""
    )
    optimizer_fingerprint = str(
        (summary.get("optimizer_budget_contract") or {}).get("fingerprint_sha256") or ""
    )
    topology_contract = (
        (summary.get("method") or {}).get("topology_feasibility_contract") or {}
    )
    expected_topology_json = json.dumps(
        topology_contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    if not _is_sha(sampler_fingerprint) or not _is_sha(optimizer_fingerprint):
        raise ControllerError("summary sampler/optimizer fingerprints are not SHA-256")
    try:
        with np.load(path, allow_pickle=False) as archive:
            keys = set(archive.files)
            exact_keys = set(layer_shapes) | set(expected_numeric) | set(required_metadata) | dynamic_string_keys
            missing = exact_keys - keys
            if missing:
                raise ControllerError(f"weights archive lacks required keys: {sorted(missing)}")
            if keys != exact_keys or len(keys) != 38:
                raise ControllerError(
                    f"weights archive keyset is not the exact 38-key contract: extras={sorted(keys - exact_keys)}"
                )
            for key, shape in layer_shapes.items():
                array = np.asarray(archive[key])
                if array.shape != shape or array.dtype.kind not in "biufc" or not np.all(np.isfinite(array)):
                    raise ControllerError(f"weights layer {key} has invalid shape/dtype/finite state")
            for key in keys:
                array = np.asarray(archive[key])
                if array.dtype.kind == "O":
                    raise ControllerError(f"weights archive contains object array: {key}")
                if array.dtype.kind in "biufc" and not np.all(np.isfinite(array)):
                    raise ControllerError(f"weights archive contains non-finite numeric array: {key}")
            for key, expected in expected_numeric.items():
                actual = np.asarray(archive[key])
                if actual.shape != expected.shape or not np.array_equal(actual, expected):
                    raise ControllerError(f"weights normalization/budget metadata differs: {key}")
            for key, expected in required_metadata.items():
                if _npz_scalar_string(archive, key) != expected:
                    raise ControllerError(f"weights string metadata differs: {key}")
            dynamic_expected = {
                "training_sampler__fingerprint_sha256": sampler_fingerprint,
                "optimizer_budget__fingerprint_sha256": optimizer_fingerprint,
                "inverse_geometry_projection__topology_contract_json": expected_topology_json,
            }
            for key, expected in dynamic_expected.items():
                if _npz_scalar_string(archive, key) != expected:
                    raise ControllerError(f"weights dynamic metadata differs from summary: {key}")
            try:
                topology = _strict_json_loads(
                    expected_topology_json, "weights topology metadata"
                )
            except ControllerError as exc:
                raise ControllerError("weights topology metadata is not JSON") from exc
            if type(topology) is not dict:
                raise ControllerError("weights topology metadata is not an object")
            if (
                topology.get("enabled") is not False
                or not _json_float_is(topology.get("weight"), 0.0)
                or topology.get("geometry_columns") != list(GEOMETRY_COLUMNS)
                or (topology.get("power_line_port_ground_overlap") or {}).get("enabled") is not False
            ):
                raise ControllerError("weights topology metadata differs from zero-weight frozen contract")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ControllerError):
            raise
        raise ControllerError(f"cannot safely inspect weights with allow_pickle=False: {exc}") from exc
    return {"path": str(path), "sha256": _sha256(path), "required_layer_shapes_exact": True, "all_numeric_finite": True}


def _validate_outputs(run_dir: Path, contract: dict[str, Any], command: dict[str, Any], arm: str, seed: int) -> dict[str, Any]:
    summary_path = _file(run_dir / SUMMARY_NAME, "trainer summary")
    weights_path = _file(run_dir / WEIGHTS_NAME, "trainer weights")
    summary = _json(summary_path, "trainer summary")
    checks = _summary_checks(summary, contract, command, arm, seed)
    history_path = _file(run_dir / HISTORY_NAME, "trainer history")
    validation_predictions_path = _file(
        run_dir / VALIDATION_PREDICTIONS_NAME, "trainer validation predictions"
    )
    test_predictions_path = _file(run_dir / TEST_PREDICTIONS_NAME, "sealed test predictions")
    expected_outputs = {
        "history_csv": history_path,
        "validation_predictions_csv": validation_predictions_path,
        "test_predictions_csv": test_predictions_path,
    }
    checks["output_paths_and_summary_hashes_exact"] = all(
        Path(str(summary.get(key) or "")).resolve() == path
        and summary.get(f"{key}_sha256") == _sha256(path)
        for key, path in expected_outputs.items()
    )
    weights = _audit_weights(
        weights_path,
        contract["materialization"]["artifacts"]["fixed_normalization"]["sha256"],
        int(contract["materialization"]["counts"][arm]["gradient_train"]),
        summary,
    )
    if summary.get("weights_npz") != str(weights_path) or summary.get("weights_npz_sha256") != weights["sha256"]:
        checks["summary_weights_binding_exact"] = False
    else:
        checks["summary_weights_binding_exact"] = True
    if not all(checks.values()):
        raise ControllerError(f"trainer output contract checks failed: {sorted(key for key, value in checks.items() if not value)}")
    return {
        "checks": checks,
        "summary": {"path": str(summary_path), "sha256": _sha256(summary_path)},
        "weights": weights,
    }


def _scan_regular(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not root.is_dir():
        return records
    entries = list(root.rglob("*"))
    if any(
        item.is_symlink() or (not item.is_file() and not item.is_dir())
        for item in entries
    ):
        raise ControllerError("artifact tree contains a symlink or non-regular filesystem entry")
    for path in sorted((item for item in entries if item.is_file()), key=lambda value: value.relative_to(root).as_posix()):
        records.append({
            "relative_path": path.relative_to(root).as_posix(),
            "path": str(path.resolve()),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        })
    return records


def _attempt_state(receipt_dir: Path) -> tuple[list[Path], list[Path], list[Path]]:
    attempts = sorted(path for path in receipt_dir.glob("attempt_*") if path.is_dir()) if receipt_dir.is_dir() else []
    complete = [path for path in attempts if (path / "COMPLETE_RECEIPT.json").is_file()]
    failed = [path for path in attempts if (path / "FAIL_RECEIPT.json").is_file()]
    ambiguous = [path for path in attempts if path not in complete and path not in failed]
    return complete, failed, ambiguous


def _verify_output_manifest(path: Path, run_dir: Path) -> dict[str, Any]:
    payload = _json(path, "arm output artifact manifest")
    if set(payload) != {
        "schema",
        "root",
        "artifacts",
        "excluded_paths",
        "all_regular_outputs_indexed",
    }:
        raise ControllerError("arm output artifact manifest keyset is not exact")
    if payload.get("schema") != "controlled_real10k_20k_arm_output_manifest_v1" or payload.get("root") != str(run_dir):
        raise ControllerError("arm output artifact manifest schema/root mismatch")
    actual = _scan_regular(run_dir)
    if not _json_exact_is(payload.get("artifacts"), actual):
        raise ControllerError("arm run directory has changed or contains an unindexed output")
    if payload.get("excluded_paths") != [] or payload.get("all_regular_outputs_indexed") is not True:
        raise ControllerError("arm output manifest has unexpected exclusions")
    return payload


def _verify_frozen_go_binding(binding: dict[str, Any], contract: dict[str, Any]) -> None:
    if set(binding) != {"path", "sha256", "nonce", "expires_utc"}:
        raise ControllerError("frozen GO binding field set is not exact")
    path = _file(binding.get("path", ""), "frozen independent-QA GO receipt")
    _require_sha(path, binding.get("sha256"), "frozen independent-QA GO receipt")
    receipt = _json(path, "frozen independent-QA GO receipt")
    try:
        for key, value in {
            "schema": GO_SCHEMA,
            "status": GO_STATUS,
            "verdict": GO_VERDICT,
            "scope": GO_SCOPE,
            "nonce": contract["qa_challenge_nonce"],
        }.items():
            _require_exact_json_equal(
                receipt.get(key), value, f"frozen GO receipt {key}"
            )
        _require_exact_json_equal(
            binding.get("nonce"), receipt.get("nonce"), "frozen GO nonce binding"
        )
        _require_exact_json_equal(
            receipt.get("findings"),
            {"p0": 0, "p1": 0},
            "frozen GO findings",
        )
    except ControllerError as exc:
        raise ControllerError(
            f"frozen GO receipt identity no longer matches the run contract: {exc}"
        ) from exc
    if _parse_time(binding.get("expires_utc"), "frozen expires_utc") != _parse_time(
        receipt.get("expires_utc"), "receipt expires_utc"
    ):
        raise ControllerError("frozen GO expiry binding differs")


def _verify_completed_arm(
    out_dir: Path, contract: dict[str, Any], command: dict[str, Any], seed: int, arm: str
) -> dict[str, Any]:
    receipt_dir = out_dir / "receipts" / f"seed_{seed}_{arm}"
    root_receipt_path = _file(receipt_dir / "COMPLETE_RECEIPT.json", "arm completion pointer")
    root_receipt = _json(root_receipt_path, "arm completion pointer")
    if (
        set(root_receipt) != {"schema", "status", "seed", "arm", "attempt_complete"}
        or root_receipt.get("schema") != "controlled_real10k_20k_arm_complete_pointer_v1"
        or root_receipt.get("status") != "COMPLETE"
        or not _json_int_is(root_receipt.get("seed"), seed)
        or root_receipt.get("arm") != arm
    ):
        raise ControllerError("arm completion pointer identity mismatch")
    attempt_binding = root_receipt.get("attempt_complete") or {}
    attempt_path = _file(attempt_binding.get("path", ""), "attempt completion receipt")
    expected_attempt_dir = receipt_dir / "attempt_0001"
    if attempt_path != expected_attempt_dir / "COMPLETE_RECEIPT.json":
        raise ControllerError("arm completion pointer does not bind exact attempt_0001 path")
    _require_sha(attempt_path, attempt_binding.get("sha256"), "attempt completion receipt")
    attempt_entries = {path.name for path in expected_attempt_dir.iterdir()}
    expected_attempt_entries = {
        "INTENT_RECEIPT.json",
        "RUNNING_RECEIPT.json",
        "stdout.log",
        "stderr.log",
        "RUNTIME_ATTESTATION.jsonl",
        "OUTPUT_ARTIFACT_MANIFEST.json",
        "COMPLETE_RECEIPT.json",
    }
    if attempt_entries != expected_attempt_entries or any(
        path.is_symlink() or not path.is_file() for path in expected_attempt_dir.iterdir()
    ):
        raise ControllerError("completed attempt contains missing, extra, symlink, or non-regular artifacts")
    receipt_entries = {path.name for path in receipt_dir.iterdir()}
    if receipt_entries != {"attempt_0001", "COMPLETE_RECEIPT.json"}:
        raise ControllerError("completed arm receipt directory contains an unindexed entry")
    receipt = _json(attempt_path, "attempt completion receipt")
    expected_receipt_keys = {
        "schema", "generated_utc", "status", "seed", "arm", "returncode",
        "evaluation_mode", "test_access_event_count", "python_isolation_flags",
        "effective_environment", "effective_environment_sha256", "run_contract",
        "command", "intent", "running", "stdout", "stderr", "runtime_attestation",
        "runtime_dependency_closure", "controlled_singleton", "output_manifest",
        "contract_checks", "summary", "weights", "fresh_emx_accessed",
        "numerical_metrics_released",
    }
    expected_environment = _require_exact_launch_record(
        command, contract, out_dir, seed, arm
    )
    expected_environment_sha = _child_environment_sha256(expected_environment)
    if (
        set(receipt) != expected_receipt_keys
        or receipt.get("schema") != "controlled_real10k_20k_arm_complete_receipt_v3"
        or receipt.get("status") != "COMPLETE"
        or not _json_int_is(receipt.get("seed"), seed)
        or receipt.get("arm") != arm
        or not _json_int_is(receipt.get("returncode"), 0)
        or receipt.get("evaluation_mode") != "validation_only"
        or not _json_int_is(receipt.get("test_access_event_count"), 0)
        or receipt.get("fresh_emx_accessed") is not False
        or receipt.get("numerical_metrics_released") is not False
        or receipt.get("python_isolation_flags") != list(PYTHON_ISOLATION_FLAGS)
        or receipt.get("effective_environment") != expected_environment
        or receipt.get("effective_environment_sha256") != expected_environment_sha
    ):
        raise ControllerError("attempt completion receipt contract mismatch")
    expected_bindings = {
        "run_contract": _binding(out_dir / "run_contract.json"),
        "command": _binding(_command_path(out_dir, seed, arm)),
    }
    for key, expected in expected_bindings.items():
        if receipt.get(key) != expected:
            raise ControllerError(f"completed arm {key} binding differs")
    for key in ("intent", "running", "stdout", "stderr", "output_manifest"):
        binding = receipt.get(key) or {}
        artifact = _file(binding.get("path", ""), f"completed arm {key}")
        expected_names = {
            "intent": "INTENT_RECEIPT.json",
            "running": "RUNNING_RECEIPT.json",
            "stdout": "stdout.log",
            "stderr": "stderr.log",
            "output_manifest": "OUTPUT_ARTIFACT_MANIFEST.json",
        }
        if artifact != expected_attempt_dir / expected_names[key]:
            raise ControllerError(f"completed arm {key} path is not exact")
        _require_sha(artifact, binding.get("sha256"), f"completed arm {key}")
    reverified_runtime_attestation = _validate_runtime_attestation(
        expected_attempt_dir / "RUNTIME_ATTESTATION.jsonl",
        contract,
        require_terminal_pass=True,
    )
    if receipt.get("runtime_attestation") != reverified_runtime_attestation:
        raise ControllerError("completed arm runtime attestation binding changed")
    if receipt.get("runtime_dependency_closure") != contract["runtime"]["descriptor_closure"]:
        raise ControllerError("completed arm runtime dependency closure changed")
    if receipt.get("controlled_singleton") != contract["controlled_singleton"]:
        raise ControllerError("completed arm controlled singleton identity changed")
    intent = _json(Path(receipt["intent"]["path"]), "intent receipt")
    running = _json(Path(receipt["running"]["path"]), "running receipt")
    expected_thread_environment = {key: str(THREAD_LIMIT) for key in THREAD_ENV_KEYS}
    expected_cmdline = (
        b"\0".join(token.encode("utf-8") for token in command["argv"]) + b"\0"
    )
    process_audit_preflight = intent.get("process_exclusivity_audit_preflight")
    process_audit_preintent = intent.get("process_exclusivity_audit_preintent")
    process_audits_exact = (
        _stored_process_audit_valid(process_audit_preflight)
        and _stored_process_audit_valid(process_audit_preintent)
        and process_audit_preflight.get("uid") == process_audit_preintent.get("uid")
        and process_audit_preflight.get("controller_pid")
        == process_audit_preintent.get("controller_pid")
        and process_audit_preflight.get("boot_id") == process_audit_preintent.get("boot_id")
    )
    intent_go = intent.get("go_receipt") or {}
    _verify_frozen_go_binding(intent_go, contract)
    running_exe = running.get("exe") or {}
    running_exe_path = _file(running_exe.get("path", ""), "recorded Python executable")
    if (
        running_exe.get("sha256") != contract["runtime"]["python"]["sha256"]
        or running_exe_path.resolve()
        != Path(contract["runtime"]["python"]["path"]).resolve()
    ):
        raise ControllerError("completed arm recorded Python executable differs")
    identity_source = running.get("identity_source")
    process_identity_fields_valid = (
        identity_source == "linux_procfs_exact"
        and isinstance(running.get("boot_id"), str)
        and bool(running.get("boot_id"))
        and type(running.get("proc_startticks")) is int
        and running.get("proc_startticks") > 0
    ) or (
        identity_source == "non_linux_frozen_spawn_argv_fallback"
        and running.get("boot_id") is None
        and running.get("proc_startticks") is None
    )
    expected_intent_keys = {
        "schema", "generated_utc", "status", "seed", "arm", "run_contract",
        "command", "go_receipt", "load1", "load1_maximum", "nice",
        "thread_environment", "python_isolation_flags", "effective_environment",
        "effective_environment_sha256", "process_exclusivity_audit_preflight",
        "process_exclusivity_audit_preintent",
        "closure_rehashed_immediately_before_intent", "runtime_dependency_closure",
        "runtime_attestation_expected_path", "controlled_singleton",
    }
    expected_running_keys = {
        "schema", "generated_utc", "status", "seed", "arm", "pid", "hostname",
        "platform", "intent", "command", "python_isolation_flags",
        "effective_environment", "effective_environment_sha256", "nice",
        "runtime_dependency_closure", "runtime_attestation_expected_path",
        "controlled_singleton", "go_and_all_bindings_rehashed_immediately_before_spawn",
        "identity_source", "boot_id", "proc_startticks", "cmdline_sha256", "exe",
    }
    if (
        set(intent) != expected_intent_keys
        or intent.get("schema") != "controlled_real10k_20k_spawn_intent_v1"
        or intent.get("status") != "INTENT_CREATE_ONCE"
        or not _json_int_is(intent.get("seed"), seed)
        or intent.get("arm") != arm
        or intent.get("run_contract") != expected_bindings["run_contract"]
        or intent.get("command") != expected_bindings["command"]
        or type(intent.get("load1")) is not float
        or not math.isfinite(intent["load1"])
        or intent["load1"] > MAX_LOAD1
        or not _json_float_is(intent.get("load1_maximum"), MAX_LOAD1)
        or not _json_int_is(intent.get("nice"), CHILD_NICE)
        or intent.get("thread_environment") != expected_thread_environment
        or intent.get("python_isolation_flags") != list(PYTHON_ISOLATION_FLAGS)
        or intent.get("effective_environment") != expected_environment
        or intent.get("effective_environment_sha256") != expected_environment_sha
        or not process_audits_exact
        or intent.get("closure_rehashed_immediately_before_intent") is not True
        or intent.get("runtime_dependency_closure")
        != contract["runtime"]["descriptor_closure"]
        or intent.get("runtime_attestation_expected_path")
        != str(expected_attempt_dir / "RUNTIME_ATTESTATION.jsonl")
        or intent.get("controlled_singleton") != contract["controlled_singleton"]
        or set(running) != expected_running_keys
        or running.get("schema") != "controlled_real10k_20k_running_receipt_v1"
        or running.get("status") != "RUNNING"
        or not _json_int_is(running.get("seed"), seed)
        or running.get("arm") != arm
        or running.get("intent") != _binding(Path(receipt["intent"]["path"]))
        or running.get("command") != expected_bindings["command"]
        or running.get("python_isolation_flags") != list(PYTHON_ISOLATION_FLAGS)
        or running.get("effective_environment") != expected_environment
        or running.get("effective_environment_sha256") != expected_environment_sha
        or not _json_int_is(running.get("nice"), CHILD_NICE)
        or running.get("runtime_dependency_closure")
        != contract["runtime"]["descriptor_closure"]
        or running.get("runtime_attestation_expected_path")
        != str(expected_attempt_dir / "RUNTIME_ATTESTATION.jsonl")
        or running.get("controlled_singleton") != contract["controlled_singleton"]
        or running.get("go_and_all_bindings_rehashed_immediately_before_spawn") is not True
        or type(running.get("pid")) is not int
        or running.get("pid") <= 0
        or not running.get("hostname")
        or not running.get("platform")
        or running.get("cmdline_sha256") != hashlib.sha256(expected_cmdline).hexdigest()
        or not process_identity_fields_valid
    ):
        raise ControllerError("completed arm intent/running process identity mismatch")
    checks = receipt.get("contract_checks")
    if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
        raise ControllerError("completed arm does not retain all-passing checks")
    run_dir = out_dir / "runs" / f"seed_{seed}" / arm
    _verify_output_manifest(Path(receipt["output_manifest"]["path"]), run_dir)
    reverified = _validate_outputs(run_dir, contract, command, arm, seed)
    if receipt.get("summary") != reverified["summary"] or receipt.get("weights") != reverified["weights"]:
        raise ControllerError("completed arm summary/weights binding changed")
    return receipt


def _fail_attempt(path: Path, *, reason: str, seed: int, arm: str, details: dict[str, Any]) -> Path:
    failure = {
        "schema": "controlled_real10k_20k_arm_fail_receipt_v2",
        "generated_utc": _utc_now(),
        "status": "FAIL",
        "reason": reason,
        "seed": seed,
        "arm": arm,
        "details": details,
        "retry_authorized": False,
        "fresh_emx_accessed": False,
        "numerical_metrics_released": False,
    }
    _write_json_x(path, failure)
    _fsync_dir(path.parent)
    _fsync_dir(path.parent.parent)
    return path


def _fail_attempt_once(
    attempt: Path,
    *,
    reason: str,
    seed: int,
    arm: str,
    exc: BaseException,
    process: subprocess.Popen[bytes] | None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Create one durable FAIL without replacing any existing terminal byte."""

    failure_path = attempt / "FAIL_RECEIPT.json"
    if not failure_path.exists():
        try:
            precursor_closure: dict[str, Any] = _artifact_snapshot(attempt)
        except BaseException as snapshot_exc:
            precursor_closure = {
                "snapshot_failed": True,
                "exception_type": type(snapshot_exc).__name__,
                "exception": str(snapshot_exc),
            }
        details = {
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "pid": None if process is None else process.pid,
            "child_process_state_ambiguous": process is not None,
            "attempt_precursor_closure": precursor_closure,
            **(extra or {}),
        }
        try:
            _fail_attempt(
                failure_path,
                reason=reason,
                seed=seed,
                arm=arm,
                details=details,
            )
        except FileExistsError:
            # A concurrent create-once terminal wins.  Never replace it.
            pass
    if failure_path.is_file():
        _fsync_dir(attempt)
        _fsync_dir(attempt.parent)
    return failure_path


def _run_arm(
    out_dir: Path,
    contract: dict[str, Any],
    package: dict[str, Any],
    go_binding: dict[str, Any],
    command: dict[str, Any],
    seed: int,
    arm: str,
) -> dict[str, Any]:
    receipt_dir = out_dir / "receipts" / f"seed_{seed}_{arm}"
    complete, failed, ambiguous = _attempt_state(receipt_dir)
    pointer = receipt_dir / "COMPLETE_RECEIPT.json"
    if pointer.is_file():
        if len(complete) != 1 or failed or ambiguous:
            raise ControllerError("completed arm has extra failed or ambiguous attempts")
        return _verify_completed_arm(out_dir, contract, command, seed, arm)
    if ambiguous:
        event = _record_event(
            out_dir,
            "AMBIGUOUS_ATTEMPT_FAIL",
            "existing attempt lacks COMPLETE/FAIL; no new attempt or automatic resume is allowed",
            seed=seed,
            arm=arm,
            paths=[str(path) for path in ambiguous],
        )
        raise ControllerError(f"ambiguous existing attempt; receipt={event}")
    if complete or failed:
        raise ControllerError("arm has terminal attempt but no valid completion pointer; retry forbidden")
    run_dir = out_dir / "runs" / f"seed_{seed}" / arm
    if run_dir.exists():
        event = _record_event(
            out_dir,
            "AMBIGUOUS_RUN_DIRECTORY_FAIL",
            "run directory exists without a valid completed attempt; fresh initialization cannot be proven",
            seed=seed,
            arm=arm,
            path=str(run_dir),
        )
        raise ControllerError(f"ambiguous existing run directory; receipt={event}")
    _verify_closure(contract, package, out_dir)
    revalidated_go = _validate_go(
        _file(go_binding["path"], "external independent-QA GO receipt"),
        go_binding["sha256"],
        contract,
        package,
        out_dir,
    )
    if revalidated_go != go_binding:
        raise ControllerError("external GO binding changed before arm resource gate")
    if datetime.now(timezone.utc) >= _parse_time(
        go_binding["expires_utc"], "spawn-time expires_utc"
    ):
        event = _record_event(
            out_dir,
            "GO_EXPIRED_BEFORE_SPAWN_FAIL",
            "independent-QA GO expired before this arm could spawn",
            seed=seed,
            arm=arm,
            expires_utc=go_binding["expires_utc"],
        )
        raise ControllerError(f"independent-QA GO expired before spawn; receipt={event}")
    process_audit_preflight = _require_process_exclusivity(
        out_dir, seed=seed, arm=arm, checkpoint="before_resource_and_closure_recheck"
    )
    load1 = _load1()
    if not math.isfinite(load1) or load1 > MAX_LOAD1:
        event = _record_event(out_dir, "RESOURCE_GATE_FAIL", "load1 exceeds frozen maximum", seed=seed, arm=arm, load1=load1, maximum=MAX_LOAD1)
        raise ControllerError(f"resource gate failed; receipt={event}")
    command_path = _command_path(out_dir, seed, arm)
    if command_path.read_bytes() != _json_bytes(command):
        raise ControllerError("command changed immediately before spawn")
    effective_environment = _require_exact_launch_record(
        command, contract, out_dir, seed, arm
    )
    effective_environment_sha = _child_environment_sha256(effective_environment)
    _verify_closure(contract, package, out_dir)
    process_audit_preintent = _require_process_exclusivity(
        out_dir, seed=seed, arm=arm, checkpoint="immediately_before_attempt_and_intent"
    )
    revalidated_go = _validate_go(
        _file(go_binding["path"], "external independent-QA GO receipt"),
        go_binding["sha256"],
        contract,
        package,
        out_dir,
    )
    if revalidated_go != go_binding:
        raise ControllerError("external GO or an exact GO binding changed immediately before arm intent")
    attempt = receipt_dir / "attempt_0001"
    attempt.mkdir(parents=True, exist_ok=False)
    intent_path = attempt / "INTENT_RECEIPT.json"
    runtime_attestation_path = attempt / "RUNTIME_ATTESTATION.jsonl"
    if command.get("runtime_attestation_path") != str(runtime_attestation_path):
        raise ControllerError("runtime attestation path is not the frozen attempt path")
    intent = {
        "schema": "controlled_real10k_20k_spawn_intent_v1",
        "generated_utc": _utc_now(),
        "status": "INTENT_CREATE_ONCE",
        "seed": seed,
        "arm": arm,
        "run_contract": _binding(out_dir / "run_contract.json"),
        "command": _binding(command_path),
        "go_receipt": go_binding,
        "load1": load1,
        "load1_maximum": MAX_LOAD1,
        "nice": CHILD_NICE,
        "thread_environment": {key: str(THREAD_LIMIT) for key in THREAD_ENV_KEYS},
        "python_isolation_flags": list(PYTHON_ISOLATION_FLAGS),
        "effective_environment": dict(effective_environment),
        "effective_environment_sha256": effective_environment_sha,
        "process_exclusivity_audit_preflight": process_audit_preflight,
        "process_exclusivity_audit_preintent": process_audit_preintent,
        "closure_rehashed_immediately_before_intent": True,
        "runtime_dependency_closure": dict(
            contract["runtime"]["descriptor_closure"]
        ),
        "runtime_attestation_expected_path": str(runtime_attestation_path),
        "controlled_singleton": dict(contract["controlled_singleton"]),
    }
    process: subprocess.Popen[bytes] | None = None
    attestation_descriptor: int | None = None
    try:
        _write_json_x(intent_path, intent)
        stdout_path, stderr_path = attempt / "stdout.log", attempt / "stderr.log"
        # Never inspect or copy os.environ.  The exact allowlist was frozen in
        # the run contract, command, GO binding, and intent; it is independently
        # rebuilt once more at the actual spawn boundary below.
    except BaseException as exc:
        failure_path = _fail_attempt_once(
            attempt,
            reason="POST_ATTEMPT_INTENT_OR_ENVIRONMENT_BASE_EXCEPTION_FAIL",
            seed=seed,
            arm=arm,
            exc=exc,
            process=None,
        )
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, ControllerError):
            raise
        raise ControllerError(
            f"intent/environment setup failed; receipt={failure_path}: {exc}"
        ) from exc
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            _verify_closure(contract, package, out_dir)
            immediate_spawn_go = _validate_go(
                _file(go_binding["path"], "external independent-QA GO receipt"),
                go_binding["sha256"],
                contract,
                package,
                out_dir,
            )
            if immediate_spawn_go != go_binding:
                raise ControllerError(
                    "external GO or an exact GO binding changed at the trainer spawn boundary"
                )
            spawn_environment = _require_exact_effective_environment(
                _effective_child_environment(), contract
            )
            if (
                spawn_environment != effective_environment
                or _child_environment_sha256(spawn_environment)
                != effective_environment_sha
                or intent.get("effective_environment") != spawn_environment
                or command.get("effective_environment") != spawn_environment
            ):
                raise ControllerError(
                    "trainer effective environment changed at the exact spawn boundary"
                )
            _require_exact_launch_record(command, contract, out_dir, seed, arm)
            attestation_descriptor = os.open(
                runtime_attestation_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
            )
            closure = contract["runtime"]["descriptor_closure"]
            try:
                launch_context = runtime_bootstrap.prepare_sealed_runtime_launch(
                    manifest_path=Path(closure["manifest"]["path"]),
                    expected_manifest_sha256=closure["manifest"]["sha256"],
                    tree_root=Path(closure["tree_root"]),
                    bootstrap_path=Path(closure["bootstrap"]["path"]),
                    expected_bootstrap_sha256=closure["bootstrap"]["sha256"],
                    entrypoint="trainer",
                    entrypoint_argv=command["entrypoint_argv"],
                    attestation_output_fd=attestation_descriptor,
                )
            except runtime_bootstrap.RuntimeClosureError as exc:
                raise ControllerError(
                    f"cannot freeze descriptor runtime at spawn: {exc}"
                ) from exc
            with launch_context as launch:
                if _HELD_PYTHON_FD is None:
                    raise ControllerError("pinned Python descriptor vanished before spawn")
                _verify_python_executable_descriptor(
                    _HELD_PYTHON_FD, contract["runtime"]["python"]
                )
                _verify_python_path_binding(contract["runtime"]["python"])
                expected_process_argv = [
                    contract["runtime"]["python"]["path"],
                    *PYTHON_ISOLATION_FLAGS,
                    *launch.process_argv_suffix,
                ]
                if expected_process_argv != command["argv"]:
                    raise ControllerError("sealed runtime launch argv differs from frozen command")
                process = subprocess.Popen(
                    command["argv"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    env=spawn_environment,
                    preexec_fn=_child_preexec,
                    executable=f"/proc/self/fd/{_HELD_PYTHON_FD}",
                    pass_fds=tuple(sorted({*launch.pass_fds, _HELD_PYTHON_FD})),
                )
                process_identity = _linux_process_identity(
                    process.pid,
                    command["argv"],
                    Path(contract["runtime"]["python"]["path"]),
                )
            running_path = attempt / "RUNNING_RECEIPT.json"
            running = {
                "schema": "controlled_real10k_20k_running_receipt_v1",
                "generated_utc": _utc_now(),
                "status": "RUNNING",
                "seed": seed,
                "arm": arm,
                "pid": process.pid,
                "hostname": socket.getfqdn(),
                "platform": platform.platform(),
                "intent": _binding(intent_path),
                "command": _binding(command_path),
                "python_isolation_flags": list(PYTHON_ISOLATION_FLAGS),
                "effective_environment": dict(spawn_environment),
                "effective_environment_sha256": _child_environment_sha256(
                    spawn_environment
                ),
                "nice": CHILD_NICE,
                "runtime_dependency_closure": dict(closure),
                "runtime_attestation_expected_path": str(runtime_attestation_path),
                "controlled_singleton": dict(contract["controlled_singleton"]),
                "go_and_all_bindings_rehashed_immediately_before_spawn": True,
                **process_identity,
            }
            _write_json_x(running_path, running)
            returncode = process.wait()
            os.close(attestation_descriptor)
            attestation_descriptor = None
            runtime_attestation_path.chmod(0o400)
    except BaseException as exc:
        if attestation_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(attestation_descriptor)
            attestation_descriptor = None
        reason = (
            "CONTROLLER_KEYBOARD_INTERRUPT_PROCESS_STATE_AMBIGUOUS"
            if isinstance(exc, KeyboardInterrupt)
            else "SPAWN_WAIT_OR_PROCESS_IDENTITY_BASE_EXCEPTION_FAIL"
        )
        failure_path = _fail_attempt_once(
            attempt,
            reason=reason,
            seed=seed,
            arm=arm,
            exc=exc,
            process=process,
            extra={"intent": _binding(intent_path)},
        )
        if isinstance(exc, KeyboardInterrupt):
            _record_event(out_dir, "KEYBOARD_INTERRUPT_FAIL", "controller interrupted; child was not duplicated or resumed", seed=seed, arm=arm, failure_receipt=str(failure_path))
            raise
        if isinstance(exc, ControllerError):
            raise
        raise ControllerError(
            f"spawn/wait/process identity failed; receipt={failure_path}: {exc}"
        ) from exc
    running_path = attempt / "RUNNING_RECEIPT.json"
    if returncode != 0:
        failure_path = _fail_attempt_once(
            attempt,
            reason="TRAINER_NONZERO_EXIT",
            seed=seed,
            arm=arm,
            exc=ControllerError(f"trainer returncode {returncode}"),
            process=process,
            extra={
                "returncode": returncode,
                "intent": _binding(intent_path),
                "running": _binding(running_path),
                "stdout": _binding(stdout_path),
                "stderr": _binding(stderr_path),
                "runtime_attestation": (
                    _binding(runtime_attestation_path)
                    if runtime_attestation_path.is_file()
                    else None
                ),
            },
        )
        raise ControllerError(f"trainer failed with returncode {returncode}; receipt={failure_path}")
    try:
        runtime_attestation = _validate_runtime_attestation(
            runtime_attestation_path, contract, require_terminal_pass=True
        )
        verified = _validate_outputs(run_dir, contract, command, arm, seed)
    except BaseException as exc:
        failure_path = _fail_attempt_once(
            attempt,
            reason="TRAINER_OUTPUT_CONTRACT_FAIL",
            seed=seed,
            arm=arm,
            exc=exc,
            process=process,
            extra={
                "returncode": returncode,
                "intent": _binding(intent_path),
                "running": _binding(running_path),
                "stdout": _binding(stdout_path),
                "stderr": _binding(stderr_path),
            },
        )
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, ControllerError):
            raise ControllerError(f"trainer output contract failed; receipt={failure_path}: {exc}") from exc
        raise ControllerError(f"trainer output inspection raised; receipt={failure_path}: {exc}") from exc
    try:
        manifest_path = attempt / "OUTPUT_ARTIFACT_MANIFEST.json"
        manifest = {
            "schema": "controlled_real10k_20k_arm_output_manifest_v1",
            "root": str(run_dir),
            "artifacts": _scan_regular(run_dir),
            "excluded_paths": [],
            "all_regular_outputs_indexed": True,
        }
        _write_json_x(manifest_path, manifest)
        complete_path = attempt / "COMPLETE_RECEIPT.json"
        complete_receipt = {
            "schema": "controlled_real10k_20k_arm_complete_receipt_v3",
            "generated_utc": _utc_now(),
            "status": "COMPLETE",
            "seed": seed,
            "arm": arm,
            "returncode": 0,
            "evaluation_mode": "validation_only",
            "test_access_event_count": 0,
            "python_isolation_flags": list(PYTHON_ISOLATION_FLAGS),
            "effective_environment": dict(effective_environment),
            "effective_environment_sha256": effective_environment_sha,
            "run_contract": _binding(out_dir / "run_contract.json"),
            "command": _binding(command_path),
            "intent": _binding(intent_path),
            "running": _binding(running_path),
            "stdout": _binding(stdout_path),
            "stderr": _binding(stderr_path),
            "runtime_attestation": runtime_attestation,
            "runtime_dependency_closure": dict(
                contract["runtime"]["descriptor_closure"]
            ),
            "controlled_singleton": dict(contract["controlled_singleton"]),
            "output_manifest": _binding(manifest_path),
            "contract_checks": verified["checks"],
            "summary": verified["summary"],
            "weights": verified["weights"],
            "fresh_emx_accessed": False,
            "numerical_metrics_released": False,
        }
        _write_json_x(complete_path, complete_receipt)
        pointer_payload = {
            "schema": "controlled_real10k_20k_arm_complete_pointer_v1",
            "status": "COMPLETE",
            "seed": seed,
            "arm": arm,
            "attempt_complete": _binding(complete_path),
        }
        _write_json_x(pointer, pointer_payload)
        return _verify_completed_arm(out_dir, contract, command, seed, arm)
    except BaseException as exc:
        failure_path = _fail_attempt_once(
            attempt,
            reason="POST_TRAIN_VALIDATION_TERMINALIZATION_BASE_EXCEPTION_FAIL",
            seed=seed,
            arm=arm,
            exc=exc,
            process=process,
            extra={"returncode": returncode},
        )
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, ControllerError):
            raise
        raise ControllerError(f"arm terminalization failed; receipt={failure_path}: {exc}") from exc


def _pair_receipt(
    out_dir: Path,
    contract: dict[str, Any],
    seed: int,
    arms: Sequence[dict[str, Any]],
) -> Path:
    path = out_dir / "receipts" / f"seed_{seed}_PAIR_COMPLETE_RECEIPT.json"
    payload = {
        "schema": "controlled_real10k_20k_pair_complete_receipt_v2",
        "status": "COMPLETE",
        "seed": seed,
        "arm_completion_receipts": [
            _binding(out_dir / "receipts" / f"seed_{seed}_{arm}" / "COMPLETE_RECEIPT.json")
            for arm in ARM_ORDER
        ],
        "runtime_dependency_closure": dict(
            contract["runtime"]["descriptor_closure"]
        ),
        "controlled_singleton": dict(contract["controlled_singleton"]),
        "checks": {
            "paired_seed_exact": [receipt["seed"] for receipt in arms] == [seed, seed],
            "execution_order_exact": [receipt["arm"] for receipt in arms] == list(ARM_ORDER),
            "both_validation_only": all(receipt["evaluation_mode"] == "validation_only" for receipt in arms),
            "both_test_access_zero": all(
                _json_int_is(receipt["test_access_event_count"], 0)
                for receipt in arms
            ),
            "both_complete": all(receipt["status"] == "COMPLETE" for receipt in arms),
        },
    }
    if not all(payload["checks"].values()):
        raise ControllerError(f"paired completion checks failed for seed {seed}")
    if path.is_file():
        if path.read_bytes() != _json_bytes(payload):
            raise ControllerError(f"existing pair receipt is non-identical: {path}")
    else:
        _write_json_x(path, payload)
    verified = _json(path, "pair completion receipt")
    if set(verified) != {
        "schema", "status", "seed", "arm_completion_receipts",
        "runtime_dependency_closure", "controlled_singleton", "checks",
    }:
        raise ControllerError("pair completion receipt keyset is not exact")
    if (
        verified.get("schema") != "controlled_real10k_20k_pair_complete_receipt_v2"
        or verified.get("status") != "COMPLETE"
        or not _json_int_is(verified.get("seed"), seed)
        or not isinstance(verified.get("checks"), dict)
        or not verified["checks"]
        or not all(value is True for value in verified["checks"].values())
    ):
        raise ControllerError("pair receipt contains a false check")
    for binding in verified["arm_completion_receipts"]:
        _require_sha(_file(binding["path"], "arm completion pointer"), binding["sha256"], "arm completion pointer")
    return path


def _final_eligible_files(out_dir: Path) -> list[Path]:
    exclusions = {"controller.lock", "SHA256SUMS.txt", "FINAL_SHA256SUMS.txt"}
    entries = list(out_dir.rglob("*"))
    if any(
        path.is_symlink() or (not path.is_file() and not path.is_dir())
        for path in entries
    ):
        raise ControllerError("controller artifact tree contains a symlink or non-regular entry")
    return sorted(
        (
            path
            for path in entries
            if path.is_file()
            and path.relative_to(out_dir).as_posix() not in exclusions
        ),
        key=lambda value: value.relative_to(out_dir).as_posix(),
    )


def _verify_final_index(path: Path, out_dir: Path) -> None:
    records = _parse_sha_index(path, out_dir, "final SHA index")
    actual = {
        item.relative_to(out_dir).as_posix()
        for item in _final_eligible_files(out_dir)
        if item != path
    }
    if set(records) != actual:
        missing = sorted(actual - set(records))
        extra = sorted(set(records) - actual)
        raise ControllerError(f"final index has unindexed/extra regular outputs: missing={missing} extra={extra}")
    if list(records) != sorted(actual):
        raise ControllerError("final SHA index order is not the frozen lexical order")


def _finish(out_dir: Path, contract: dict[str, Any], pair_paths: Sequence[Path]) -> Path:
    manifest_path = out_dir / "FINAL_ARTIFACT_MANIFEST.json"
    final_receipt_path = out_dir / "receipts" / "COMPLETE_RECEIPT.json"
    index_path = out_dir / "FINAL_SHA256SUMS.txt"
    excluded = [
        "controller.lock",
        "SHA256SUMS.txt",
        "FINAL_SHA256SUMS.txt",
        "FINAL_ARTIFACT_MANIFEST.json",
        "receipts/COMPLETE_RECEIPT.json",
    ]
    manifest_files = [
        path
        for path in _final_eligible_files(out_dir)
        if path not in {manifest_path, final_receipt_path}
    ]
    manifest = {
        "schema": "controlled_real10k_20k_final_artifact_manifest_v1",
        "root": str(out_dir),
        "artifacts": [
            {
                "relative_path": path.relative_to(out_dir).as_posix(),
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in manifest_files
        ],
        "excluded_paths": excluded,
        "all_other_regular_outputs_indexed": True,
    }
    if manifest_path.is_file():
        if manifest_path.read_bytes() != _json_bytes(manifest):
            raise ControllerError("final artifact manifest changed or an unindexed regular output exists")
    else:
        _write_json_x(manifest_path, manifest)
    final_receipt = {
        "schema": "controlled_real10k_20k_controller_complete_receipt_v3",
        "status": FINAL_STATUS,
        "run_contract": _binding(out_dir / "run_contract.json"),
        "pairs": [_binding(path) for path in pair_paths],
        "final_artifact_manifest": _binding(manifest_path),
        "exact_paired_seeds": list(EXACT_PAIRED_SEEDS),
        "runtime_dependency_closure": dict(
            contract["runtime"]["descriptor_closure"]
        ),
        "controlled_singleton": dict(contract["controlled_singleton"]),
        "evaluation_mode": "validation_only",
        "test_access_event_count": 0,
        "one_time_common_test_evaluation_performed": False,
        "fresh_emx_accessed": False,
        "numerical_metrics_released": False,
        "next_legal_gate": "INDEPENDENT_QA_FOR_ONE_TIME_COMMON_TEST_EVALUATOR",
    }
    if final_receipt_path.is_file():
        if final_receipt_path.read_bytes() != _json_bytes(final_receipt):
            raise ControllerError("existing final receipt is non-identical")
    else:
        _write_json_x(final_receipt_path, final_receipt)
    if index_path.is_file():
        _verify_final_index(index_path, out_dir)
    else:
        _write_sha_index_x(index_path, out_dir, _final_eligible_files(out_dir))
        _verify_final_index(index_path, out_dir)
    verified = _json(final_receipt_path, "final completion receipt")
    if (
        set(verified)
        != {
            "schema", "status", "run_contract", "pairs", "final_artifact_manifest",
            "exact_paired_seeds", "runtime_dependency_closure", "controlled_singleton",
            "evaluation_mode", "test_access_event_count",
            "one_time_common_test_evaluation_performed", "fresh_emx_accessed",
            "numerical_metrics_released", "next_legal_gate",
        }
        or verified.get("schema")
        != "controlled_real10k_20k_controller_complete_receipt_v3"
        or verified.get("status") != FINAL_STATUS
        or not _json_int_is(verified.get("test_access_event_count"), 0)
        or not _json_exact_is(
            verified.get("exact_paired_seeds"), list(EXACT_PAIRED_SEEDS)
        )
        or verified.get("evaluation_mode") != "validation_only"
        or verified.get("one_time_common_test_evaluation_performed") is not False
        or verified.get("fresh_emx_accessed") is not False
        or verified.get("numerical_metrics_released") is not False
        or verified.get("next_legal_gate")
        != "INDEPENDENT_QA_FOR_ONE_TIME_COMMON_TEST_EVALUATOR"
        or verified.get("runtime_dependency_closure")
        != contract["runtime"]["descriptor_closure"]
        or verified.get("controlled_singleton") != contract["controlled_singleton"]
    ):
        raise ControllerError("final completion status/test seal is invalid")
    for path in pair_paths:
        pair = _json(path, "pair completion receipt")
        if not all((pair.get("checks") or {}).values()):
            raise ControllerError("final completion references a failed pair check")
        if (
            pair.get("runtime_dependency_closure")
            != contract["runtime"]["descriptor_closure"]
            or pair.get("controlled_singleton") != contract["controlled_singleton"]
        ):
            raise ControllerError("pair completion runtime/singleton binding differs")
    return final_receipt_path


def _execute(
    out_dir: Path,
    contract: dict[str, Any],
    package: dict[str, Any],
    go_path: Path,
    go_sha: str,
) -> Path:
    go = _validate_go(go_path, go_sha, contract, package, out_dir)
    _verify_closure(contract, package, out_dir)
    command_lookup = {(record["seed"], record["arm"]): record for record in package["commands"]}
    pair_paths: list[Path] = []
    for seed in EXACT_PAIRED_SEEDS:
        arms: list[dict[str, Any]] = []
        for arm in ARM_ORDER:
            arms.append(
                _run_arm(
                    out_dir,
                    contract,
                    package,
                    go,
                    command_lookup[(seed, arm)],
                    seed,
                    arm,
                )
            )
        pair_paths.append(_pair_receipt(out_dir, contract, seed, arms))
    return _finish(out_dir, contract, pair_paths)


def main(argv: list[str] | None = None) -> int:
    global _HELD_PYTHON_FD, _HELD_SINGLETON_FD
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    python_descriptor: int | None = None
    try:
        _require_active_runtime(args.expected_runtime_closure_json_sha256)
        python = _python_executable(args.python_executable)
        python_descriptor, python_descriptor_identity = (
            _open_python_executable_descriptor(python)
        )
        _HELD_PYTHON_FD = python_descriptor
        runtime = _runtime_identity(
            python,
            python_descriptor_identity,
            _file(args.runtime_closure_json, "runtime closure manifest"),
            args.expected_runtime_closure_json_sha256,
            _directory(args.runtime_closure_tree, "runtime dependency closure tree"),
            _file(args.runtime_bootstrap, "runtime bootstrap"),
            args.expected_runtime_bootstrap_sha256,
        )
        trainer = _file(args.trainer, "trainer")
        trainer_sha = _require_sha(trainer, args.expected_trainer_sha256, "trainer")
        singleton_descriptor, controlled_singleton = _open_singleton_lock(
            args.controlled_singleton_lock,
            args.expected_controlled_singleton_lock_sha256,
        )
        try:
            # Verify the pinned inode before acquiring a lock so the controller
            # never locks a pathname that failed the immutable identity check.
            _verify_singleton_lock_descriptor(
                singleton_descriptor, controlled_singleton
            )
            try:
                fcntl.flock(
                    singleton_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError as exc:
                raise ControllerError(
                    "controlled package singleton lock is already held; no action taken"
                ) from exc
            _verify_singleton_lock_descriptor(
                singleton_descriptor, controlled_singleton
            )
            _HELD_SINGLETON_FD = singleton_descriptor
            try:
                summary_path = _file(
                    args.materialization_summary, "materialization summary"
                )
                material = _audit_material(
                    summary_path,
                    args.expected_materialization_summary_sha256,
                    _file(
                        args.materialization_complete_receipt,
                        "outer materialization COMPLETE receipt",
                    ),
                    args.expected_materialization_complete_receipt_sha256,
                    runtime["descriptor_closure"]["role_bindings"][
                        "shared_contract_code"
                    ]["sha256"],
                )
                _require_production_training_runtime(trainer_sha, python, runtime)
                contract = _run_contract(
                    out_dir=out_dir,
                    material=material,
                    trainer=trainer,
                    trainer_sha=trainer_sha,
                    runtime=runtime,
                    controlled_singleton=controlled_singleton,
                )
                if args.phase == "execute" and not out_dir.is_dir():
                    raise ControllerError(
                        "execute requires a previously frozen prepare package"
                    )
                package = _prepare_or_verify(out_dir, contract)
                lock_path = out_dir / "controller.lock"
                with lock_path.open("a+b") as lock:
                    try:
                        fcntl.flock(
                            lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                        )
                    except BlockingIOError as exc:
                        event = _record_event(
                            out_dir,
                            "LOCK_FAIL",
                            "controller lock is held; no action taken",
                            phase=args.phase,
                        )
                        raise ControllerError(
                            f"controller lock unavailable; receipt={event}"
                        ) from exc
                    _verify_singleton_lock_descriptor(
                        singleton_descriptor, controlled_singleton
                    )
                    if args.phase == "prepare":
                        print(f"status=PREPARED receipt={package['prepared_path']}")
                        return 0
                    final = _execute(
                        out_dir,
                        contract,
                        package,
                        _file(
                            args.independent_qa_go_receipt,
                            "external independent-QA GO receipt",
                        ),
                        args.expected_independent_qa_go_receipt_sha256,
                    )
                    print(f"status={FINAL_STATUS} receipt={final}")
                    return 0
            finally:
                _HELD_SINGLETON_FD = None
        finally:
            os.close(singleton_descriptor)
    except KeyboardInterrupt:
        _record_event(out_dir, "KEYBOARD_INTERRUPT_FAIL", "operation interrupted; no duplicate/retry authorized", phase=args.phase)
        raise
    except ControllerError as exc:
        try:
            event = _record_event(out_dir, "PREFLIGHT_OR_EXECUTE_FAIL", str(exc), phase=args.phase)
        except OSError:
            event = None
        if event is not None:
            setattr(exc, "machine_receipt", str(event))
        raise
    finally:
        _HELD_PYTHON_FD = None
        if python_descriptor is not None:
            os.close(python_descriptor)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControllerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if getattr(exc, "machine_receipt", None):
            print(f"failure_receipt={exc.machine_receipt}", file=sys.stderr)
        raise SystemExit(2)
