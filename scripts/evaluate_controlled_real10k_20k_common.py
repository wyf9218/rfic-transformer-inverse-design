#!/usr/bin/env python3
"""One-time common evaluation for the controlled nested real-EMX 10K/20K arms.

``prepare`` is result blind.  It verifies and freezes the materialization,
common holdout, shared normalization, six validation-only model artifacts,
three complete pair receipts, trainer, evaluator, and immutable fixed10k frame.
It cannot read the sealed test rows or run inference.

``execute`` requires a fresh, external, exact-GO receipt bound to the prepared
bytes.  After acquiring a singleton lock it writes a create-once release claim
*before* reading the common test rows.  A claim is never retried: interruption
or failure is retained as an ambiguous/failed denominator.  The evaluator does
not generate EMX, layouts, data, targets, or models.

The common historical real-EMX holdout is used for (1) forward prediction error
against the true stored labels (primary), (2) inverse-to-own-forward proxy
self-consistency, and (3) inverse geometry-to-one-recorded-label distance.  The
last two are secondary because inverse solutions are non-unique.  The fixed10k
frame is an own-forward one-shot proxy diagnostic, not fresh EMX.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import secrets
import stat
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence

import numpy as np
import numpy.__config__ as numpy_config
import numpy._core._multiarray_umath as numpy_core

from rfic_transformer_inverse_design import (  # noqa: E402
    controlled_real10k_20k_runtime_bootstrap as runtime_bootstrap,
)


FROZEN_SHARED_CONTRACT_SHA256 = (
    "ca6824e5d47fc037c856044ad74b0dec26844fed19d09bbfee42d44fbd3969c0"
)


def _module_load_file_identity(raw: str | Path) -> dict[str, Any]:
    path = Path(raw).resolve(strict=True)
    metadata = path.stat()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": int(metadata.st_size),
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "nlink": int(metadata.st_nlink),
    }


# In production the evaluator is executed as a sealed runtime entrypoint.  The
# scientific contract is therefore compiled only from the manifest-bound ZIP
# member, never from sys.path or a mutable pathname.  Unit tests import this
# module (not as __main__) outside the production bootstrap and use the local
# source strictly as a fixture path; main() rejects that mode in production.
try:
    _shared_source_bytes, _shared_source_origin = runtime_bootstrap.active_member_source(
        "shared_contract_code", FROZEN_SHARED_CONTRACT_SHA256
    )
    _SEALED_RUNTIME_IMPORT = True
except runtime_bootstrap.RuntimeClosureError as _runtime_import_error:
    if __name__ == "__main__":
        raise RuntimeError(
            "production evaluator must start inside the exact descriptor runtime"
        ) from _runtime_import_error
    _SEALED_RUNTIME_IMPORT = False
    _shared_source_path = (
        Path(__file__).resolve(strict=True).parents[1]
        / "rfic_transformer_inverse_design"
        / "controlled_real10k_20k_contract.py"
    )
    _shared_source_bytes = _shared_source_path.read_bytes()
    if hashlib.sha256(_shared_source_bytes).hexdigest() != FROZEN_SHARED_CONTRACT_SHA256:
        raise RuntimeError("local fixture shared scientific contract SHA-256 mismatch")
    _shared_source_origin = str(_shared_source_path)

_shared_module_name = (
    "rfic_transformer_inverse_design.controlled_real10k_20k_contract"
)
shared_contract = ModuleType(_shared_module_name)
shared_contract.__dict__.update(
    {
        "__file__": _shared_source_origin,
        "__package__": "rfic_transformer_inverse_design",
        "__name__": _shared_module_name,
    }
)
exec(
    compile(_shared_source_bytes, _shared_source_origin, "exec"),
    shared_contract.__dict__,
)
sys.modules[_shared_module_name] = shared_contract
EXACT_PAIRED_SEEDS = shared_contract.EXACT_PAIRED_SEEDS
GEOMETRY_COLUMNS = shared_contract.GEOMETRY_COLUMNS
GEOMETRY_LOWER = shared_contract.GEOMETRY_LOWER
GEOMETRY_UPPER = shared_contract.GEOMETRY_UPPER
INPUT_COLUMNS = shared_contract.INPUT_COLUMNS
INPUT_LOWER = shared_contract.INPUT_LOWER
INPUT_UPPER = shared_contract.INPUT_UPPER
canonical_physical_cell_id = shared_contract.canonical_physical_cell_id

_MODULE_LOAD_EVALUATOR_IDENTITY = _module_load_file_identity(__file__)
if _SEALED_RUNTIME_IMPORT:
    _sealed_evaluator_bytes, _sealed_evaluator_origin = (
        runtime_bootstrap.active_member_source(
            "evaluator_code", _MODULE_LOAD_EVALUATOR_IDENTITY["sha256"]
        )
    )
    if len(_sealed_evaluator_bytes) != _MODULE_LOAD_EVALUATOR_IDENTITY["size_bytes"]:
        raise RuntimeError("sealed evaluator/display-path source size mismatch")
    _shared_evidence_path = (
        Path(__file__).resolve(strict=True).parents[1]
        / "rfic_transformer_inverse_design"
        / "controlled_real10k_20k_contract.py"
    )
    _MODULE_LOAD_SHARED_IDENTITY = _module_load_file_identity(_shared_evidence_path)
    if _MODULE_LOAD_SHARED_IDENTITY["sha256"] != FROZEN_SHARED_CONTRACT_SHA256:
        raise RuntimeError("sealed shared/display-path source SHA-256 mismatch")
else:
    _MODULE_LOAD_SHARED_IDENTITY = _module_load_file_identity(_shared_source_path)
_MODULE_LOAD_PYTHON_IDENTITY = _module_load_file_identity(sys.executable)
_MODULE_LOAD_NUMPY_CORE_IDENTITY = (
    None if _SEALED_RUNTIME_IMPORT else _module_load_file_identity(numpy_core.__file__)
)
_MODULE_LOAD_NUMPY_CONFIG_IDENTITY = (
    None if _SEALED_RUNTIME_IMPORT else _module_load_file_identity(numpy_config.__file__)
)
_MODULE_LOAD_RUNTIME_SCALARS = {
    "python_version": ".".join(str(value) for value in sys.version_info[:3]),
    "python_implementation": str(sys.implementation.name),
    "numpy_version": str(np.__version__),
}
_module_load_config_buffer = io.StringIO()
with contextlib.redirect_stdout(_module_load_config_buffer):
    np.show_config()
_MODULE_LOAD_NUMPY_SHOW_CONFIG_SHA256 = hashlib.sha256(
    _module_load_config_buffer.getvalue().encode("utf-8")
).hexdigest()
del _module_load_config_buffer
del _shared_source_bytes


EVALUATOR_SCHEMA = "controlled_real10k_20k_common_evaluator_v1"
PREPARED_SCHEMA = "controlled_real10k_20k_common_evaluation_prepared_v1"
MANIFEST_SCHEMA = "controlled_real10k_20k_common_evaluation_manifest_v1"
QA_REQUIRED_SCHEMA = "controlled_real10k_20k_common_evaluation_qa_required_v1"
GO_SCHEMA = "controlled_real10k_20k_common_evaluation_exact_go_v1"
GO_SCOPE = "ONE_TIME_COMMON_TEST_AND_FIXED10K_EVALUATION"
RELEASE_CLAIM_SCHEMA = "controlled_real10k_20k_common_test_release_claim_v1"
SUMMARY_SCHEMA = "controlled_real10k_20k_common_evaluation_summary_v1"
COMPLETE_SCHEMA = "controlled_real10k_20k_common_evaluation_terminal_receipt_v1"
SPATIAL_SENSITIVITY_SCHEMA = (
    "controlled_real10k_20k_physical_cell_cluster_bootstrap_v1"
)

# Frozen adapter to the result-blind paired controller.  Production preparation
# accepts only this exact runner implementation and its exact terminal schemas.
FROZEN_PAIRED_RUNNER_SHA256 = (
    "06d3658f94b1964b2dab4154f6db4065a00e2bc3c1174e4d7deb2912952a6f31"
)
FROZEN_TRAINER_LAUNCH_CONTRACT = {
    "schema": "controlled_real10k_20k_isolated_trainer_launch_v1",
    "python_isolation_flags": ["-I", "-B", "-S"],
    "parent_environment_inherited": False,
    "environment_allowlist_exact": [
        "LC_ALL",
        "LANG",
        "TZ",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "GOTO_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "OMP_DYNAMIC",
        "MKL_DYNAMIC",
    ],
    "effective_environment": {
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "4",
        "GOTO_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
        "NUMEXPR_NUM_THREADS": "4",
        "BLIS_NUM_THREADS": "4",
        "VECLIB_MAXIMUM_THREADS": "4",
        "OMP_DYNAMIC": "FALSE",
        "MKL_DYNAMIC": "FALSE",
    },
    "effective_environment_sha256": (
        "c7e1adc11b08201dfc5438bc08dbf67a5c5273aa1f9ca085646a6ab7ca59fc9b"
    ),
    "python_prefixed_environment_keys": [],
    "scientific_seed_transport": "exact_trainer_argv_seed_and_split_seed",
    "pythonhashseed_environment_used": False,
}
PAIRED_RUN_CONTRACT_SCHEMA = "controlled_real10k_20k_paired_controller_v4"
SIX_ARM_TERMINAL_SCHEMA = "controlled_real10k_20k_controller_complete_receipt_v3"
ARM_TERMINAL_POINTER_SCHEMA = "controlled_real10k_20k_arm_complete_pointer_v1"
ARM_TERMINAL_RECEIPT_SCHEMA = "controlled_real10k_20k_arm_complete_receipt_v3"
PAIR_TERMINAL_RECEIPT_SCHEMA = "controlled_real10k_20k_pair_complete_receipt_v2"
LEGACY_FIXTURE_PAIRED_RUN_CONTRACT_SCHEMA = (
    "controlled_real10k_20k_paired_controller_v3"
)
LEGACY_FIXTURE_SIX_ARM_TERMINAL_SCHEMA = (
    "controlled_real10k_20k_controller_complete_receipt_v2"
)
LEGACY_FIXTURE_ARM_TERMINAL_RECEIPT_SCHEMA = (
    "controlled_real10k_20k_arm_complete_receipt_v2"
)
FINAL_ARTIFACT_MANIFEST_SCHEMA = "controlled_real10k_20k_final_artifact_manifest_v1"
PAIRED_FINAL_INDEX_NAME = "FINAL_SHA256SUMS.txt"
MATERIAL_SCHEMA = "controlled_real10k_20k_nested_materialization_v2"
HOLDOUT_SCHEMA = "fixed_common_holdout_geometry_identity_v1"
NORMALIZATION_SCHEMA = "declared_midpoint_half_range_normalization_v1"

FIXED10K_SHA256 = (
    "c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407"
)
PREREGISTRATION_ADDENDUM_SCHEMA = (
    "controlled_real10k_20k_nested_preregistration_addendum_v1_2"
)
PREREGISTRATION_ADDENDUM_SHA256 = (
    "fb7c7d0f9e206e3743cf795a544004e570842f26495903ad0eafdd5f909f37a9"
)
SPATIAL_BOOTSTRAP_REPLICATES = 2000
SPATIAL_BOOTSTRAP_MASTER_SEED = 2026082402
SPATIAL_FRAME_IDS = (
    "common_real_emx_holdout_902",
    "fixed_target_full10k",
    "fixed_target_legacy_K_abs_le_0p8_8000",
    "fixed_target_highK_K_abs_gt_0p8_2000",
)
EXPECTED_COMMON_TEST_ROWS = 902
EXPECTED_FIXED_ROWS = 10_000
EXPECTED_LEGACY_ROWS = 8_000
EXPECTED_EXTENSION_ROWS = 2_000
MAX_GO_VALIDITY = timedelta(hours=24)
T95_DF2 = 4.302652729911275
GO_NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
GO_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
FINAL_FILE_MODE = 0o444
FINAL_DIRECTORY_MODE = 0o555

GO_CHECKS = {
    "result_blind_independent_review": True,
    "prepared_sha_closure_exact": True,
    "shared_scientific_contract_exact": True,
    "numerical_runtime_exact": True,
    "paired_run_shared_contract_exact": True,
    "six_validation_only_models_exact": True,
    "common_test_and_fixed10k_release_scope_exact": True,
    "one_time_claim_and_descriptor_consumption_reviewed": True,
    "final_filesystem_closure_reviewed": True,
}

FEATURE_KEYS = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")
FEATURE_UNITS = ("nH", "nH", "dimensionless", "dimensionless")
FIXED_RESPONSE_SPANS = np.asarray([2.5, 2.5, 20.0, 1.0], dtype=np.float64)

PREPARED_NAME = "EVALUATION_PREPARED.json"
MANIFEST_NAME = "EVALUATION_MANIFEST.json"
QA_REQUIRED_NAME = "INDEPENDENT_QA_REQUIRED.json"
PREPARE_INDEX_NAME = "SHA256SUMS_PREPARE.txt"
LOCK_NAME = "EVALUATION_LOCK"
CLAIM_NAME = "TEST_RELEASE_CLAIM.json"
COMMON_ROWS_NAME = "common_real_emx_holdout_per_row.csv"
FIXED_ROWS_NAME = "fixed10k_own_forward_proxy_per_row.csv"
SUMMARY_NAME = "COMMON_EVALUATION_SUMMARY.json"
TERMINAL_NAME = "COMMON_EVALUATION_TERMINAL_RECEIPT.json"
RESULT_INDEX_NAME = "SHA256SUMS_RESULTS.txt"
FATAL_FAIL_NAME = "COMMON_EVALUATION_FATAL_FAIL.json"
FAILURE_INDEX_NAME = "SHA256SUMS_FAILURE.txt"
LEASE_SCHEMA = "controlled_real10k_20k_one_time_release_lease_v1"
CONTROLLED_SINGLETON_SCHEMA = "controlled_real10k_20k_package_singleton_lock_v1"
CONTROLLED_SINGLETON_LOCK_MODE = (
    "flock_exclusive_nonblocking_held_controller_and_trainer_lifetime"
)
PROCESS_SINGLETON_CONTRACT_SCHEMA = (
    "controlled_real10k_20k_process_singleton_contract_v1"
)
MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA = (
    "controlled_real10k_20k_materialization_gate_manifest_v2"
)
MATERIALIZATION_GO_SCHEMA = "controlled_real10k_20k_materialization_exact_go_v2"
MATERIALIZATION_COMPLETE_SCHEMA = "controlled_real10k_20k_materialization_complete_v3"
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
MARS_PREFLIGHT_BODY_SCHEMA = "controlled_real10k_20k_mars_preflight_receipt_body_v3"
MARS_PREFLIGHT_COMMITTED_SCHEMA = "controlled_real10k_20k_mars_preflight_committed_v3"
MARS_PREFLIGHT_LEASE_SCHEMA = "controlled_real10k_20k_mars_preflight_one_use_lease_v3"
PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_body_v3"
)
PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA = (
    "controlled_real10k_20k_mars_package_build_attempt_committed_v1"
)
PACKAGE_BUILD_ATTEMPT_BODY_NAME = "PACKAGE_BUILD_ATTEMPT_RECEIPT.json"
PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME = "PACKAGE_BUILD_ATTEMPT_COMMITTED.json"
PACKAGE_COMMIT_SCHEMA = "controlled_real10k_20k_mars_package_commit_v2"
PACKAGE_VERSION = "v5"
PACKAGE_NO_AUTHORITY = {
    "native_linux_test_execution": False,
    "data_materialization": False,
    "training": False,
    "common_test_access": False,
    "numerical_metric_access": False,
    "fresh_emx": False,
    "process_signal": False,
}
PACKAGE_BUILD_ATTEMPT_PUBLICATION = {
    "body_file_fsync": True,
    "attempt_root_fsync": True,
    "attempt_parent_fsync": True,
    "attempt_root_frozen": True,
    "continuity_verified": True,
    "terminal_inode_reserved_create_once_before_freeze": True,
    "terminal_bytes_published_after_durability": True,
    "post_commit_attempt_file_creation_permitted": False,
}
MARS_PREFLIGHT_ROLE_FILENAMES = {
    "mars_preflight_prepared": "PREFLIGHT_PREPARED.json",
    "mars_preflight_execution_qa_required": "PREFLIGHT_EXECUTION_QA_REQUIRED.json",
    "mars_preflight_prepare_sha_index": "PREPARE_SHA256SUMS.txt",
    "mars_preflight_receipt_body": "PREFLIGHT_RECEIPT_BODY.json",
    "mars_preflight_sha_index": "PREFLIGHT_SHA256SUMS.txt",
    "mars_preflight_committed": "PREFLIGHT_COMMITTED.json",
}
MARS_PREFLIGHT_SUCCESS_FILES = tuple(MARS_PREFLIGHT_ROLE_FILENAMES.values())

PREPARE_FILE_NAMES = {
    MANIFEST_NAME,
    PREPARED_NAME,
    QA_REQUIRED_NAME,
    PREPARE_INDEX_NAME,
    LOCK_NAME,
}
FINAL_FILE_NAMES = PREPARE_FILE_NAMES | {
    CLAIM_NAME,
    COMMON_ROWS_NAME,
    FIXED_ROWS_NAME,
    SUMMARY_NAME,
    TERMINAL_NAME,
    RESULT_INDEX_NAME,
}


class EvaluationError(RuntimeError):
    """A frozen identity, authorization, isolation, or numeric gate failed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvaluationError(f"{label} is not a nonempty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise EvaluationError(f"{label} is timezone-naive")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _require_sha_token(value: Any, label: str) -> str:
    if type(value) is not str or value != value.strip().lower() or not _is_sha(value):
        raise EvaluationError(f"{label} is not a lowercase SHA-256")
    return value


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _absolute_lexical(raw: str | Path) -> Path:
    """Return an absolute lexical path without following any symlink."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(raw))))


def _reject_symlink_chain(path: Path, label: str) -> None:
    absolute = _absolute_lexical(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise EvaluationError(f"{label} traverses a symlink: {current}")


def _safe_file_metadata(path: Path, label: str) -> os.stat_result:
    _reject_symlink_chain(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise EvaluationError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise EvaluationError(f"{label} is not a regular file: {path}")
    if metadata.st_nlink != 1:
        raise EvaluationError(
            f"{label} must have exactly one hard link; observed {metadata.st_nlink}"
        )
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o7000 or mode & 0o022:
        raise EvaluationError(
            f"{label} has unsafe special/group-or-other-writable mode {mode:04o}"
        )
    return metadata


def _file(raw: str | Path, label: str) -> Path:
    path = _absolute_lexical(raw)
    _safe_file_metadata(path, label)
    return path


def _directory(raw: str | Path, label: str) -> Path:
    path = _absolute_lexical(raw)
    _reject_symlink_chain(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise EvaluationError(f"{label} is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise EvaluationError(f"{label} is not a directory: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o7000 or mode & 0o022:
        raise EvaluationError(f"{label} has unsafe directory mode {mode:04o}")
    return path


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_directory_entry(path: Path, *, include_parent: bool) -> None:
    _fsync_directory(path)
    if include_parent:
        _fsync_directory(path.parent)


def _strict_json_loads(payload: str, label: str) -> Any:
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
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise EvaluationError(f"cannot parse {label}: {exc}") from exc


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvaluationError(f"cannot read {label}: {exc}") from exc
    payload = _strict_json_loads(text, label)
    if type(payload) is not dict:
        raise EvaluationError(f"{label} must be a JSON object")
    return payload


def _json_from_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise EvaluationError(f"cannot parse held {label}: {exc}") from exc
    value = _strict_json_loads(text, f"held {label}")
    if type(value) is not dict:
        raise EvaluationError(f"held {label} must be a JSON object")
    return value


def _require_exact_json_equal(actual: Any, expected: Any, label: str) -> None:
    """Require recursive JSON equality without Python's bool/int coercion."""

    def compare(left: Any, right: Any, location: str) -> None:
        if type(left) is not type(right):
            raise EvaluationError(
                f"{label} exact JSON type mismatch at {location}: "
                f"{type(left).__name__} != {type(right).__name__}"
            )
        if isinstance(right, dict):
            if any(type(key) is not str for key in left) or any(
                type(key) is not str for key in right
            ):
                raise EvaluationError(f"{label} has a non-string JSON key at {location}")
            if set(left) != set(right):
                raise EvaluationError(f"{label} exact JSON keyset mismatch at {location}")
            for key in sorted(right):
                compare(left[key], right[key], f"{location}.{key}")
            return
        if isinstance(right, list):
            if len(left) != len(right):
                raise EvaluationError(f"{label} exact JSON length mismatch at {location}")
            for index, (left_value, right_value) in enumerate(zip(left, right)):
                compare(left_value, right_value, f"{location}[{index}]")
            return
        if left != right:
            raise EvaluationError(f"{label} exact JSON value mismatch at {location}")

    compare(actual, expected, "$")


def _require_exact_int(
    value: Any,
    label: str,
    *,
    expected: int | None = None,
    minimum: int | None = None,
) -> int:
    """Reject bool/float/string aliases at every receipt integer boundary."""

    if type(value) is not int:
        raise EvaluationError(f"{label} must be an exact JSON integer")
    if expected is not None and value != expected:
        raise EvaluationError(f"{label} differs: {value} != {expected}")
    if minimum is not None and value < minimum:
        raise EvaluationError(f"{label} is below {minimum}")
    return value


def _require_exact_bool(value: Any, expected: bool, label: str) -> bool:
    if type(value) is not bool or value is not expected:
        raise EvaluationError(f"{label} must be exact JSON {expected!r}")
    return value


def _audit_trainer_launch_contract(raw: Any, label: str) -> dict[str, Any]:
    _require_exact_json_equal(raw, FROZEN_TRAINER_LAUNCH_CONTRACT, label)
    environment = FROZEN_TRAINER_LAUNCH_CONTRACT["effective_environment"]
    environment_sha = _canonical_sha(
        {
            "schema": "controlled_real10k_20k_exact_child_environment_v2",
            "environment": environment,
        }
    )
    if environment_sha != FROZEN_TRAINER_LAUNCH_CONTRACT[
        "effective_environment_sha256"
    ]:
        raise EvaluationError(f"{label} effective-environment SHA differs")
    if any(key.upper().startswith("PYTHON") for key in environment):
        raise EvaluationError(f"{label} contains a PYTHON-prefixed environment key")
    return json.loads(json.dumps(FROZEN_TRAINER_LAUNCH_CONTRACT))


def _directory_identity_from_descriptor(
    descriptor: int, path: Path, label: str
) -> dict[str, Any]:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode):
        raise EvaluationError(f"{label} descriptor is not a directory")
    mode = stat.S_IMODE(observed.st_mode)
    if mode & 0o7000 or mode & 0o022:
        raise EvaluationError(f"{label} has unsafe directory mode {mode:04o}")
    try:
        lexical = path.lstat()
    except FileNotFoundError as exc:
        raise EvaluationError(f"{label} lexical path is missing") from exc
    if (lexical.st_dev, lexical.st_ino) != (observed.st_dev, observed.st_ino):
        raise EvaluationError(f"{label} lexical path/held descriptor mismatch")
    return {
        "path": str(path),
        "mode": f"{mode:04o}",
        "device": int(observed.st_dev),
        "inode": int(observed.st_ino),
    }


def _validate_directory_identity(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EvaluationError(f"{label} directory identity is not an object")
    _exact_keys(raw, {"path", "mode", "device", "inode"}, label)
    path = _absolute_lexical(str(raw.get("path") or ""))
    if str(path) != raw.get("path"):
        raise EvaluationError(f"{label} path is not exact absolute lexical form")
    mode = raw.get("mode")
    if not isinstance(mode, str) or not re.fullmatch(r"[0-7]{4}", mode):
        raise EvaluationError(f"{label} mode is malformed")
    numeric: dict[str, int] = {}
    for key in ("device", "inode"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EvaluationError(f"{label} {key} is invalid")
        numeric[key] = value
    return {"path": str(path), "mode": mode, **numeric}


def _assert_held_root_matches(
    descriptor: int, out_dir: Path, expected: Mapping[str, Any]
) -> dict[str, Any]:
    wanted = _validate_directory_identity(expected, "prepared output-root identity")
    observed = _directory_identity_from_descriptor(
        descriptor, out_dir, "prepared output root"
    )
    _require_exact_json_equal(observed, wanted, "prepared output-root identity")
    return observed


def _lease_state_bytes(
    state: str, nonce: str, root_identity: Mapping[str, Any]
) -> bytes:
    if state not in {"PREPARED", "CONSUMED"}:  # equal-width states are deliberate
        raise EvaluationError("one-time lease state is invalid")
    return _json_bytes(
        {
            "schema": LEASE_SCHEMA,
            "state": state,
            "nonce": nonce,
            "output_root_device": root_identity["device"],
            "output_root_inode": root_identity["inode"],
        }
    )


def _lease_identity_from_descriptor(
    descriptor: int, path: Path, state: str, payload: bytes
) -> dict[str, Any]:
    observed = os.fstat(descriptor)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise EvaluationError("one-time release lease is not a single-link regular file")
    try:
        lexical = path.lstat()
    except FileNotFoundError as exc:
        raise EvaluationError("one-time release lease path is missing") from exc
    if (lexical.st_dev, lexical.st_ino) != (observed.st_dev, observed.st_ino):
        raise EvaluationError("one-time release lease path/descriptor mismatch")
    return {
        "schema": LEASE_SCHEMA,
        "state": state,
        "path": str(path),
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "mode": f"{stat.S_IMODE(observed.st_mode):04o}",
        "device": int(observed.st_dev),
        "inode": int(observed.st_ino),
        "nlink": int(observed.st_nlink),
        "ctime_ns": int(observed.st_ctime_ns),
    }


def _validate_lease_identity(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EvaluationError(f"{label} is not an object")
    _exact_keys(
        raw,
        {
            "schema",
            "state",
            "path",
            "sha256",
            "size_bytes",
            "mode",
            "device",
            "inode",
            "nlink",
            "ctime_ns",
        },
        label,
    )
    if raw.get("schema") != LEASE_SCHEMA or raw.get("state") != "PREPARED":
        raise EvaluationError(f"{label} schema/state differs")
    path = _absolute_lexical(str(raw.get("path") or ""))
    if str(path) != raw.get("path"):
        raise EvaluationError(f"{label} path is not exact absolute lexical form")
    sha = _require_sha_token(raw.get("sha256"), f"{label} SHA")
    mode = raw.get("mode")
    if mode != "0600":
        raise EvaluationError(f"{label} prepared mode differs")
    numeric: dict[str, int] = {}
    for key in ("size_bytes", "device", "inode", "nlink", "ctime_ns"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EvaluationError(f"{label} {key} is invalid")
        numeric[key] = value
    if numeric["nlink"] != 1:
        raise EvaluationError(f"{label} nlink differs")
    return {
        "schema": LEASE_SCHEMA,
        "state": "PREPARED",
        "path": str(path),
        "sha256": sha,
        "mode": mode,
        **numeric,
    }


def _require_file_sha(path: Path, expected: Any, label: str) -> str:
    wanted = _require_sha_token(expected, f"{label} expected SHA")
    actual = _sha256(path)
    if actual != wanted:
        raise EvaluationError(f"{label} SHA mismatch: {actual} != {wanted}")
    return actual


def _pinned_file_identity(
    path: Path,
    label: str,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Hash one exact inode and bind every property needed for later openat use."""

    path = _file(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mode,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mode,
        after.st_nlink,
    ):
        raise EvaluationError(f"{label} changed while its identity was frozen")
    lexical = path.lstat()
    if (lexical.st_dev, lexical.st_ino) != (after.st_dev, after.st_ino):
        raise EvaluationError(f"{label} pathname changed while its identity was frozen")
    digest_token = digest.hexdigest()
    if expected_sha256 is not None and digest_token != _require_sha_token(
        expected_sha256, f"{label} expected SHA"
    ):
        raise EvaluationError(f"{label} SHA mismatch")
    return {
        "path": str(path),
        "sha256": digest_token,
        "size_bytes": int(after.st_size),
        "mode": f"{stat.S_IMODE(after.st_mode):04o}",
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "nlink": int(after.st_nlink),
    }


def _validate_pinned_identity(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EvaluationError(f"{label} pinned identity is not an object")
    _exact_keys(
        raw,
        {"path", "sha256", "size_bytes", "mode", "device", "inode", "nlink"},
        f"{label} pinned identity",
    )
    path = _absolute_lexical(str(raw.get("path") or ""))
    if str(path) != raw.get("path"):
        raise EvaluationError(f"{label} pinned path is not exact absolute lexical form")
    sha = _require_sha_token(raw.get("sha256"), f"{label} pinned SHA")
    mode = str(raw.get("mode") or "")
    if not re.fullmatch(r"[0-7]{4}", mode):
        raise EvaluationError(f"{label} pinned mode is malformed")
    numeric = {}
    for key in ("size_bytes", "device", "inode", "nlink"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EvaluationError(f"{label} pinned {key} is invalid")
        numeric[key] = value
    if numeric["nlink"] != 1:
        raise EvaluationError(f"{label} pinned nlink is not one")
    return {
        "path": str(path),
        "sha256": sha,
        "mode": mode,
        **numeric,
    }


def _validate_controlled_singleton_identity(
    raw: Any, label: str
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise EvaluationError(f"{label} is not an object")
    _exact_keys(
        raw,
        {
            "schema",
            "path",
            "sha256",
            "size_bytes",
            "device",
            "inode",
            "nlink",
            "lock_mode",
        },
        label,
    )
    if raw.get("schema") != CONTROLLED_SINGLETON_SCHEMA:
        raise EvaluationError(f"{label} schema differs")
    if raw.get("lock_mode") != CONTROLLED_SINGLETON_LOCK_MODE:
        raise EvaluationError(f"{label} lock mode differs")
    path = _absolute_lexical(str(raw.get("path") or ""))
    if str(path) != raw.get("path"):
        raise EvaluationError(f"{label} path is not exact absolute lexical form")
    sha = _require_sha_token(raw.get("sha256"), f"{label} SHA")
    numeric = {
        key: _require_exact_int(raw.get(key), f"{label} {key}", minimum=0)
        for key in ("size_bytes", "device", "inode", "nlink")
    }
    if numeric["nlink"] != 1:
        raise EvaluationError(f"{label} nlink differs")
    return {
        "schema": CONTROLLED_SINGLETON_SCHEMA,
        "path": str(path),
        "sha256": sha,
        **numeric,
        "lock_mode": CONTROLLED_SINGLETON_LOCK_MODE,
    }


def _verify_controlled_singleton_descriptor(
    descriptor: int, expected: Mapping[str, Any], label: str
) -> dict[str, Any]:
    identity = _validate_controlled_singleton_identity(expected, label)
    try:
        held = os.fstat(descriptor)
        lexical = Path(identity["path"]).lstat()
    except OSError as exc:
        raise EvaluationError(f"cannot inspect {label}") from exc
    if (
        stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or not stat.S_ISREG(held.st_mode)
        or (lexical.st_dev, lexical.st_ino) != (held.st_dev, held.st_ino)
        or held.st_dev != identity["device"]
        or held.st_ino != identity["inode"]
        or held.st_size != identity["size_bytes"]
        or held.st_nlink != identity["nlink"]
    ):
        raise EvaluationError(f"{label} descriptor/path identity changed")
    mode = stat.S_IMODE(held.st_mode)
    if mode & 0o7000 or mode & 0o022:
        raise EvaluationError(f"{label} mode became unsafe: {mode:04o}")
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    if digest.hexdigest() != identity["sha256"] or offset != identity["size_bytes"]:
        raise EvaluationError(f"{label} held bytes changed")
    return identity


@contextlib.contextmanager
def _held_controlled_singleton(args: argparse.Namespace):
    if args.fixture_mode:
        yield None
        return
    pinned = _pinned_file_identity(
        Path(args.controlled_singleton_lock),
        "controlled package singleton lock",
        expected_sha256=args.expected_controlled_singleton_lock_sha256,
    )
    identity = {
        "schema": CONTROLLED_SINGLETON_SCHEMA,
        "path": pinned["path"],
        "sha256": pinned["sha256"],
        "size_bytes": pinned["size_bytes"],
        "device": pinned["device"],
        "inode": pinned["inode"],
        "nlink": pinned["nlink"],
        "lock_mode": CONTROLLED_SINGLETON_LOCK_MODE,
    }
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(identity["path"], flags)
    try:
        _verify_controlled_singleton_descriptor(
            descriptor, identity, "controlled package singleton before lock"
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EvaluationError("controlled package singleton lock is already held") from exc
        _verify_controlled_singleton_descriptor(
            descriptor, identity, "controlled package singleton after lock"
        )
        try:
            yield dict(identity)
        finally:
            _verify_controlled_singleton_descriptor(
                descriptor, identity, "controlled package singleton before unlock"
            )
    finally:
        os.close(descriptor)


def _open_held_inputs(
    records: Mapping[str, Any], stack: ExitStack
) -> dict[str, Any]:
    handles: dict[str, Any] = {}
    paths: set[str] = set()
    for role in sorted(records):
        if role == "controlled_singleton_lock":
            record = _validate_controlled_singleton_identity(
                records[role], f"release input {role}"
            )
        else:
            record = _validate_pinned_identity(
                records[role], f"release input {role}"
            )
        if record["path"] in paths:
            raise EvaluationError(f"release input path is duplicated across roles: {role}")
        paths.add(record["path"])
        path = _file(record["path"], f"release input {role}")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(os.fspath(path), flags)
        handle = stack.enter_context(os.fdopen(descriptor, "rb", closefd=True))
        observed = os.fstat(handle.fileno())
        exact = {
            "size_bytes": int(observed.st_size),
            "device": int(observed.st_dev),
            "inode": int(observed.st_ino),
            "nlink": int(observed.st_nlink),
        }
        if role != "controlled_singleton_lock":
            exact["mode"] = f"{stat.S_IMODE(observed.st_mode):04o}"
        if any(record[key] != value for key, value in exact.items()):
            raise EvaluationError(
                f"release input {role} descriptor identity changed before consumption"
            )
        lexical = path.lstat()
        if (lexical.st_dev, lexical.st_ino) != (observed.st_dev, observed.st_ino):
            raise EvaluationError(f"release input {role} pathname/descriptor mismatch")
        handles[role] = handle
    return handles


def _snapshot_held_input(handle: Any, record: Mapping[str, Any], role: str) -> bytes:
    if role == "controlled_singleton_lock":
        expected = _validate_controlled_singleton_identity(
            record, f"release input {role}"
        )
    else:
        expected = _validate_pinned_identity(record, f"release input {role}")
    before = os.fstat(handle.fileno())
    handle.seek(0)
    payload = handle.read()
    after = os.fstat(handle.fileno())
    immutable_fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink")
    if any(getattr(before, key) != getattr(after, key) for key in immutable_fields):
        raise EvaluationError(f"release input {role} changed during held read")
    observed = {
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "nlink": int(after.st_nlink),
    }
    if role != "controlled_singleton_lock":
        observed["mode"] = f"{stat.S_IMODE(after.st_mode):04o}"
    if any(expected[key] != value for key, value in observed.items()):
        raise EvaluationError(f"release input {role} held-byte identity mismatch")
    return payload


def _write_bytes_x(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o644)
        os.fsync(handle.fileno())
    _durable_directory_entry(path.parent, include_parent=True)


def _write_json_x(path: Path, payload: Any) -> None:
    _write_bytes_x(path, _json_bytes(payload))


def _write_index_x(path: Path, root: Path, names: Sequence[str]) -> None:
    unique = list(dict.fromkeys(names))
    if not unique or path.name in unique:
        raise EvaluationError("SHA index member list is empty or self-referential")
    lines: list[str] = []
    for name in unique:
        if Path(name).is_absolute() or Path(name).name != name:
            raise EvaluationError(f"SHA index member is not a top-level filename: {name}")
        member = _file(root / name, f"SHA index member {name}")
        lines.append(f"{_sha256(member)}  {name}\n")
    _write_bytes_x(path, "".join(lines).encode("ascii"))


def _verify_index(path: Path, root: Path, expected_names: set[str]) -> None:
    seen: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if "  " not in line:
            raise EvaluationError(f"SHA index line {line_number} is malformed")
        expected, name = line.split("  ", 1)
        expected = _require_sha_token(expected, f"SHA index line {line_number}")
        if Path(name).is_absolute() or Path(name).name != name or name in seen:
            raise EvaluationError(f"SHA index line {line_number} has an invalid path")
        member = _file(root / name, f"SHA index member {name}")
        if _sha256(member) != expected:
            raise EvaluationError(f"SHA index member changed: {name}")
        seen[name] = expected
    if set(seen) != expected_names:
        raise EvaluationError(
            f"SHA index closure mismatch: {set(seen)} != {expected_names}"
        )


def _read_bytes_at(
    root_descriptor: int, name: str, label: str
) -> tuple[bytes, dict[str, Any]]:
    if Path(name).is_absolute() or Path(name).name != name:
        raise EvaluationError(f"{label} is not a top-level filename")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=root_descriptor)
    except OSError as exc:
        raise EvaluationError(f"cannot open held-root {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise EvaluationError(f"held-root {label} is not a single-link regular file")
        mode = stat.S_IMODE(before.st_mode)
        if mode & 0o7000 or mode & 0o022:
            raise EvaluationError(f"held-root {label} has unsafe mode {mode:04o}")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        payload = b"".join(blocks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if any(
        getattr(before, field) != getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink")
    ):
        raise EvaluationError(f"held-root {label} changed during read")
    return payload, {
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "mode": f"{stat.S_IMODE(after.st_mode):04o}",
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "nlink": int(after.st_nlink),
    }


def _sha256_at(root_descriptor: int, name: str, label: str | None = None) -> str:
    return _read_bytes_at(root_descriptor, name, label or name)[1]["sha256"]


def _size_at(root_descriptor: int, name: str, label: str | None = None) -> int:
    return _read_bytes_at(root_descriptor, name, label or name)[1]["size_bytes"]


def _durable_held_directories(root_descriptor: int, parent_descriptor: int) -> None:
    os.fsync(root_descriptor)
    os.fsync(parent_descriptor)


def _write_bytes_at_x(
    root_descriptor: int,
    parent_descriptor: int,
    name: str,
    payload: bytes,
    *,
    mode: int = 0o644,
) -> None:
    if Path(name).is_absolute() or Path(name).name != name:
        raise EvaluationError(f"create-once output is not a top-level filename: {name}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=root_descriptor)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EvaluationError(f"short write creating {name}")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _durable_held_directories(root_descriptor, parent_descriptor)


def _write_json_at_x(
    root_descriptor: int, parent_descriptor: int, name: str, payload: Any
) -> None:
    _write_bytes_at_x(
        root_descriptor, parent_descriptor, name, _json_bytes(payload)
    )


def _write_index_at_x(
    root_descriptor: int,
    parent_descriptor: int,
    name: str,
    members: Sequence[str],
) -> None:
    unique = list(dict.fromkeys(members))
    if not unique or name in unique:
        raise EvaluationError("SHA index member list is empty or self-referential")
    lines: list[str] = []
    for member in unique:
        if Path(member).is_absolute() or Path(member).name != member:
            raise EvaluationError(f"SHA index member is not top-level: {member}")
        lines.append(
            f"{_sha256_at(root_descriptor, member, f'SHA index member {member}')}  {member}\n"
        )
    _write_bytes_at_x(
        root_descriptor, parent_descriptor, name, "".join(lines).encode("ascii")
    )


def _parse_index_bytes(
    payload: bytes,
    snapshots: Mapping[str, bytes],
    expected_names: set[str],
    label: str,
) -> None:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise EvaluationError(f"{label} is not ASCII") from exc
    seen: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        if "  " not in line:
            raise EvaluationError(f"{label} line {line_number} is malformed")
        expected, name = line.split("  ", 1)
        expected = _require_sha_token(expected, f"{label} line {line_number}")
        if Path(name).is_absolute() or Path(name).name != name or name in seen:
            raise EvaluationError(f"{label} line {line_number} has an invalid path")
        if name not in snapshots or _sha256_bytes(snapshots[name]) != expected:
            raise EvaluationError(f"{label} member changed: {name}")
        seen[name] = expected
    if set(seen) != expected_names:
        raise EvaluationError(f"{label} closure mismatch")


def _verify_index_at(
    root_descriptor: int, index_name: str, expected_names: set[str]
) -> None:
    snapshots = {
        name: _read_bytes_at(root_descriptor, name, f"indexed output {name}")[0]
        for name in expected_names
    }
    index_bytes = _read_bytes_at(
        root_descriptor, index_name, f"SHA index {index_name}"
    )[0]
    _parse_index_bytes(index_bytes, snapshots, expected_names, index_name)


def _open_text_at_x(root_descriptor: int, name: str) -> Any:
    if Path(name).is_absolute() or Path(name).name != name:
        raise EvaluationError(f"CSV output is not a top-level filename: {name}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=root_descriptor)
    return os.fdopen(descriptor, "w", newline="", encoding="utf-8", closefd=True)


def _verify_paired_final_index(path: Path, root: Path) -> dict[str, Any]:
    """Rehash the runner's recursive, lexically ordered terminal closure."""

    root = root.resolve()
    if path != root / PAIRED_FINAL_INDEX_NAME:
        raise EvaluationError("paired final SHA index path is not canonical")
    seen: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if "  " not in line:
            raise EvaluationError(f"paired final SHA index line {line_number} is malformed")
        expected, name = line.split("  ", 1)
        expected = _require_sha_token(
            expected, f"paired final SHA index line {line_number}"
        )
        relative = Path(name)
        if (
            relative.is_absolute()
            or not name
            or relative.as_posix() != name
            or any(part in {"", ".", ".."} for part in relative.parts)
            or name in seen
        ):
            raise EvaluationError(f"paired final SHA index line {line_number} path is invalid")
        member = _file(root / relative, f"paired final SHA index member {name}")
        try:
            member.relative_to(root)
        except ValueError as exc:
            raise EvaluationError("paired final SHA index path escapes controller root") from exc
        if _sha256(member) != expected:
            raise EvaluationError(f"paired final SHA index member changed: {name}")
        seen[name] = expected
    entries = list(root.rglob("*"))
    if any(
        entry.is_symlink() or (not entry.is_file() and not entry.is_dir())
        for entry in entries
    ):
        raise EvaluationError("paired controller tree contains a symlink/non-regular entry")
    excluded = {"controller.lock", "SHA256SUMS.txt", PAIRED_FINAL_INDEX_NAME}
    actual = {
        entry.relative_to(root).as_posix()
        for entry in entries
        if entry.is_file() and entry.relative_to(root).as_posix() not in excluded
    }
    if set(seen) != actual or list(seen) != sorted(actual):
        raise EvaluationError("paired final SHA index has incomplete/nonlexical closure")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "indexed_regular_file_count": len(seen),
        "indexed_relative_paths": list(seen),
        "indexed_sha256": dict(seen),
    }


def _binding(raw: Any, base: Path, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EvaluationError(f"{label} binding is not an object")
    raw_path = raw.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise EvaluationError(f"{label} binding lacks path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base / path
    path = _file(path, label)
    sha = _require_file_sha(path, raw.get("sha256"), label)
    if "size_bytes" in raw:
        _require_exact_int(
            raw["size_bytes"],
            f"{label} size binding",
            expected=path.stat().st_size,
        )
    return {"path": str(path), "sha256": sha, "size_bytes": path.stat().st_size}


def _show_config_sha256() -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        np.show_config()
    return _sha256_bytes(buffer.getvalue().encode("utf-8"))


def _runtime_identity(args: argparse.Namespace | None = None) -> dict[str, Any]:
    if _SEALED_RUNTIME_IMPORT:
        if args is None:
            raise EvaluationError("sealed runtime identity requires parsed evaluator arguments")
        try:
            active = runtime_bootstrap.require_active_runtime(
                "evaluator", args.expected_runtime_closure_json_sha256
            )
            closure = runtime_bootstrap.audit_runtime_closure_paths(
                Path(args.runtime_closure_json),
                args.expected_runtime_closure_json_sha256,
                Path(args.runtime_closure_tree),
                Path(args.runtime_bootstrap),
                args.expected_runtime_bootstrap_sha256,
            )
        except runtime_bootstrap.RuntimeClosureError as exc:
            raise EvaluationError(f"descriptor runtime identity is invalid: {exc}") from exc
        pure_archive = Path(closure["tree_root"]) / closure["pure_archive"]["path"]
        files = {
            "python_executable": _pinned_file_identity(
                Path(sys.executable).resolve(strict=True), "Python executable"
            ),
            "runtime_bootstrap": _pinned_file_identity(
                Path(closure["bootstrap"]["path"]),
                "runtime bootstrap",
                expected_sha256=closure["bootstrap"]["sha256"],
            ),
            "runtime_manifest": _pinned_file_identity(
                Path(closure["manifest"]["path"]),
                "runtime closure manifest",
                expected_sha256=closure["manifest"]["sha256"],
            ),
            "runtime_pure_archive": _pinned_file_identity(
                pure_archive,
                "runtime pure archive",
                expected_sha256=closure["pure_archive"]["sha256"],
            ),
        }
        if (
            active.get("bootstrap_sha256") != closure["bootstrap"]["sha256"]
            or active.get("manifest_sha256") != closure["manifest"]["sha256"]
            or active.get("pure_archive_sha256") != closure["pure_archive"]["sha256"]
            or closure["numpy"].get("version") != str(np.__version__)
            or closure["role_bindings"]["shared_contract_code"]["sha256"]
            != FROZEN_SHARED_CONTRACT_SHA256
            or closure["role_bindings"]["evaluator_code"]["sha256"]
            != _MODULE_LOAD_EVALUATOR_IDENTITY["sha256"]
        ):
            raise EvaluationError("active descriptor runtime/role binding differs from audited closure")
        return {
            "schema": "controlled_real10k_20k_descriptor_runtime_binding_v1",
            "python_version": ".".join(str(value) for value in sys.version_info[:3]),
            "python_implementation": str(sys.implementation.name),
            "numpy_version": str(np.__version__),
            "numpy_show_config_sha256": _show_config_sha256(),
            "active_runtime": active,
            "descriptor_closure": closure,
            "role_bindings": closure["role_bindings"],
            "system_library_allowlist": closure["system_library_allowlist"],
            "files": files,
        }

    python_path = _file(Path(sys.executable).resolve(strict=True), "Python executable")
    core_path = _file(Path(numpy_core.__file__).resolve(strict=True), "NumPy core")
    config_path = _file(Path(numpy_config.__file__).resolve(strict=True), "NumPy config")
    files = {
        "python_executable": _pinned_file_identity(
            python_path, "Python executable"
        ),
        "numpy_core": _pinned_file_identity(core_path, "NumPy core"),
        "numpy_config": _pinned_file_identity(config_path, "NumPy config"),
    }
    scalars = {
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "python_implementation": str(sys.implementation.name),
        "numpy_version": str(np.__version__),
        "numpy_show_config_sha256": _show_config_sha256(),
    }
    module_load_files = {
        "python_executable": _MODULE_LOAD_PYTHON_IDENTITY,
        "numpy_core": _MODULE_LOAD_NUMPY_CORE_IDENTITY,
        "numpy_config": _MODULE_LOAD_NUMPY_CONFIG_IDENTITY,
    }
    if files != module_load_files or scalars != {
        **_MODULE_LOAD_RUNTIME_SCALARS,
        "numpy_show_config_sha256": _MODULE_LOAD_NUMPY_SHOW_CONFIG_SHA256,
    }:
        raise EvaluationError(
            "Python/NumPy path bytes or scalar identity changed after module import"
        )
    return {**scalars, "files": files}


def _audit_paired_runtime_identity(
    raw: Any, evaluator_runtime: Mapping[str, Any]
) -> dict[str, Any]:
    """Require the runner and evaluator to attest one identical sealed closure."""

    if type(raw) is not dict:
        raise EvaluationError("paired run-contract runtime identity is not an object")
    _exact_keys(
        raw,
        {"python", "numpy_version", "bootstrap", "descriptor_closure"},
        "paired run-contract runtime identity",
    )
    if evaluator_runtime.get("schema") != (
        "controlled_real10k_20k_descriptor_runtime_binding_v1"
    ):
        raise EvaluationError("production evaluator runtime is not descriptor sealed")
    closure = raw.get("descriptor_closure")
    expected_closure = evaluator_runtime.get("descriptor_closure")
    if type(closure) is not dict or type(expected_closure) is not dict:
        raise EvaluationError("paired/evaluator descriptor closure is missing")
    _require_exact_json_equal(
        closure, expected_closure, "paired/evaluator descriptor runtime closure"
    )
    _require_exact_json_equal(
        raw.get("bootstrap"),
        closure.get("bootstrap"),
        "paired runtime bootstrap/closure binding",
    )
    if raw.get("numpy_version") != evaluator_runtime.get("numpy_version"):
        raise EvaluationError("paired/evaluator NumPy version differs")

    python = raw.get("python")
    if type(python) is not dict:
        raise EvaluationError("paired runtime Python identity is not an object")
    _exact_keys(
        python,
        {
            "path",
            "resolved_path_at_open",
            "sha256",
            "size_bytes",
            "device",
            "inode",
            "nlink",
            "execution_mode",
        },
        "paired runtime Python identity",
    )
    if python.get("execution_mode") != "pinned_descriptor_procfd_executable_v1":
        raise EvaluationError("paired runtime Python execution mode differs")
    if not isinstance(python.get("path"), str) or not Path(
        python["path"]
    ).is_absolute():
        raise EvaluationError("paired runtime Python lexical path is not absolute")
    if not isinstance(python.get("resolved_path_at_open"), str) or not Path(
        python["resolved_path_at_open"]
    ).is_absolute():
        raise EvaluationError("paired runtime resolved Python path is not absolute")
    python_sha = _require_sha_token(
        python.get("sha256"), "paired runtime Python SHA"
    )
    numeric = {
        key: _require_exact_int(
            python.get(key), f"paired runtime Python {key}", minimum=0
        )
        for key in ("size_bytes", "device", "inode", "nlink")
    }
    if numeric["nlink"] < 1:
        raise EvaluationError("paired runtime Python nlink is zero")
    evaluator_python = _validate_pinned_identity(
        (evaluator_runtime.get("files") or {}).get("python_executable"),
        "evaluator runtime Python executable",
    )
    expected_core = {
        "path": evaluator_python["path"],
        "sha256": evaluator_python["sha256"],
        "size_bytes": evaluator_python["size_bytes"],
        "device": evaluator_python["device"],
        "inode": evaluator_python["inode"],
        "nlink": evaluator_python["nlink"],
    }
    observed_core = {
        "path": python["resolved_path_at_open"],
        "sha256": python_sha,
        **numeric,
    }
    _require_exact_json_equal(
        observed_core, expected_core, "paired/evaluator Python executable identity"
    )
    return {
        "python": dict(python),
        "numpy_version": str(raw["numpy_version"]),
        "bootstrap": dict(raw["bootstrap"]),
        "descriptor_closure": dict(closure),
    }


def _audit_paired_shared_member(
    raw: Any,
    evaluator_runtime: Mapping[str, Any],
    expected_sha256: str,
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise EvaluationError("paired shared scientific member binding is not an object")
    _exact_keys(
        raw,
        {"member", "sha256", "size_bytes"},
        "paired shared scientific member binding",
    )
    member = raw.get("member")
    if not isinstance(member, str) or not member or member.startswith("/"):
        raise EvaluationError("paired shared scientific member name is invalid")
    sha = _require_sha_token(raw.get("sha256"), "paired shared member SHA")
    size = _require_exact_int(
        raw.get("size_bytes"), "paired shared member size", minimum=1
    )
    role_binding = (evaluator_runtime.get("role_bindings") or {}).get(
        "shared_contract_code"
    )
    expected = {"member": member, "sha256": sha, "size_bytes": size}
    _require_exact_json_equal(
        expected, role_binding, "paired/evaluator shared member binding"
    )
    if sha != expected_sha256:
        raise EvaluationError("paired shared member differs from frozen contract SHA")
    try:
        payload, origin = runtime_bootstrap.active_member_source(
            "shared_contract_code", sha
        )
    except runtime_bootstrap.RuntimeClosureError as exc:
        raise EvaluationError("paired shared member is not active in sealed runtime") from exc
    if _sha256_bytes(payload) != sha or len(payload) != size or not origin:
        raise EvaluationError("paired shared member active bytes differ")
    return expected


def _audit_paired_controlled_singleton(
    raw: Any, held_identity: Mapping[str, Any]
) -> dict[str, Any]:
    paired = _validate_controlled_singleton_identity(
        raw, "paired run-contract controlled singleton"
    )
    held = _validate_controlled_singleton_identity(
        held_identity, "evaluator held controlled singleton"
    )
    _require_exact_json_equal(
        paired, held, "paired/evaluator controlled singleton identity"
    )
    return paired


def _shared_contract_identity() -> dict[str, Any]:
    if _SEALED_RUNTIME_IMPORT:
        try:
            payload, _origin = runtime_bootstrap.active_member_source(
                "shared_contract_code", FROZEN_SHARED_CONTRACT_SHA256
            )
        except runtime_bootstrap.RuntimeClosureError as exc:
            raise EvaluationError("active shared scientific contract is unavailable") from exc
        if (
            _sha256_bytes(payload) != _MODULE_LOAD_SHARED_IDENTITY["sha256"]
            or len(payload) != _MODULE_LOAD_SHARED_IDENTITY["size_bytes"]
        ):
            raise EvaluationError("sealed and display-path shared contract identities differ")
        if canonical_physical_cell_id.__module__ != shared_contract.__name__:
            raise EvaluationError("physical-cell encoder is not from the sealed shared contract")
        return dict(_MODULE_LOAD_SHARED_IDENTITY)

    module_path = _file(
        Path(str(shared_contract.__file__)).resolve(strict=True),
        "imported shared scientific contract",
    )
    if canonical_physical_cell_id.__module__ != shared_contract.__name__:
        raise EvaluationError("physical-cell encoder is not from the imported shared contract")
    if tuple(shared_contract.EXACT_PAIRED_SEEDS) != tuple(EXACT_PAIRED_SEEDS):
        raise EvaluationError("imported shared-contract paired seeds differ from evaluator")
    if tuple(shared_contract.INPUT_COLUMNS) != tuple(INPUT_COLUMNS) or tuple(
        shared_contract.GEOMETRY_COLUMNS
    ) != tuple(GEOMETRY_COLUMNS):
        raise EvaluationError("imported shared-contract column order differs from evaluator")
    identity = _pinned_file_identity(module_path, "imported shared scientific contract")
    if identity != _MODULE_LOAD_SHARED_IDENTITY:
        raise EvaluationError(
            "shared scientific contract path bytes changed after actual import"
        )
    return identity


def _verify_live_runtime_from_snapshots(
    expected: Mapping[str, Any], snapshots: Mapping[str, bytes]
) -> None:
    if not isinstance(expected, dict):
        raise EvaluationError("release runtime identity is missing")
    if expected.get("schema") == "controlled_real10k_20k_descriptor_runtime_binding_v1":
        _exact_keys(
            expected,
            {
                "schema",
                "python_version",
                "python_implementation",
                "numpy_version",
                "numpy_show_config_sha256",
                "active_runtime",
                "descriptor_closure",
                "role_bindings",
                "system_library_allowlist",
                "files",
            },
            "release descriptor runtime identity",
        )
        active_expected = expected.get("active_runtime")
        if type(active_expected) is not dict:
            raise EvaluationError("release active runtime binding is missing")
        try:
            active_live = runtime_bootstrap.require_active_runtime(
                "evaluator", active_expected.get("manifest_sha256")
            )
        except runtime_bootstrap.RuntimeClosureError as exc:
            raise EvaluationError("live descriptor runtime is not active") from exc
        _require_exact_json_equal(
            active_live, active_expected, "live descriptor runtime binding"
        )
        descriptor_closure = expected.get("descriptor_closure")
        if type(descriptor_closure) is not dict:
            raise EvaluationError("release descriptor closure binding is missing")
        if descriptor_closure.get("manifest", {}).get("sha256") != active_expected.get(
            "manifest_sha256"
        ):
            raise EvaluationError("release descriptor closure/active manifest differs")
        scalar = {
            "python_version": ".".join(str(value) for value in sys.version_info[:3]),
            "python_implementation": str(sys.implementation.name),
            "numpy_version": str(np.__version__),
            "numpy_show_config_sha256": _show_config_sha256(),
        }
        if any(expected.get(key) != value for key, value in scalar.items()):
            raise EvaluationError("live descriptor Python/NumPy scalar identity changed")
        files = expected.get("files")
        expected_file_roles = {
            "python_executable",
            "runtime_bootstrap",
            "runtime_manifest",
            "runtime_pure_archive",
        }
        if type(files) is not dict or set(files) != expected_file_roles:
            raise EvaluationError("release descriptor runtime file-role set is not exact")
        for role, raw in files.items():
            record = _validate_pinned_identity(raw, f"descriptor runtime {role}")
            snapshot = snapshots.get(f"runtime__{role}")
            if snapshot is None or _sha256_bytes(snapshot) != record["sha256"]:
                raise EvaluationError(f"live descriptor runtime snapshot differs: {role}")
        role_bindings = expected.get("role_bindings")
        if type(role_bindings) is not dict or set(role_bindings) != {
            "package_init_code",
            "runtime_bootstrap_code",
            "shared_contract_code",
            "splitter_code",
            "runner_code",
            "trainer_code",
            "materialization_gate_code",
            "materialization_builder_code",
            "evaluator_code",
            "native_smoke_test",
        }:
            raise EvaluationError("release descriptor runtime role binding set is not exact")
        _require_exact_json_equal(
            role_bindings,
            descriptor_closure.get("role_bindings"),
            "release descriptor closure role bindings",
        )
        if (
            role_bindings["shared_contract_code"].get("sha256")
            != FROZEN_SHARED_CONTRACT_SHA256
            or role_bindings["evaluator_code"].get("sha256")
            != _MODULE_LOAD_EVALUATOR_IDENTITY["sha256"]
        ):
            raise EvaluationError("live descriptor project role identities changed")
        return

    _exact_keys(
        expected,
        {
            "python_version",
            "python_implementation",
            "numpy_version",
            "numpy_show_config_sha256",
            "files",
        },
        "release runtime identity",
    )
    scalar = {
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "python_implementation": str(sys.implementation.name),
        "numpy_version": str(np.__version__),
        "numpy_show_config_sha256": _show_config_sha256(),
    }
    if any(expected.get(key) != value for key, value in scalar.items()):
        raise EvaluationError("live Python/NumPy scalar runtime identity changed")
    files = expected.get("files")
    if not isinstance(files, dict) or set(files) != {
        "python_executable",
        "numpy_core",
        "numpy_config",
    }:
        raise EvaluationError("release runtime file-role set is not exact")
    live_paths = {
        "python_executable": str(
            _file(Path(sys.executable).resolve(strict=True), "live Python executable")
        ),
        "numpy_core": str(
            _file(Path(numpy_core.__file__).resolve(strict=True), "live NumPy core")
        ),
        "numpy_config": str(
            _file(Path(numpy_config.__file__).resolve(strict=True), "live NumPy config")
        ),
    }
    for role, path in live_paths.items():
        record = _validate_pinned_identity(files[role], f"runtime {role}")
        if record["path"] != path or snapshots.get(f"runtime__{role}") is None:
            raise EvaluationError(f"live runtime {role} path/snapshot identity differs")


def _verify_live_shared_contract_from_snapshot(
    expected: Mapping[str, Any], snapshot: bytes
) -> None:
    record = _validate_pinned_identity(expected, "shared scientific contract")
    if _SEALED_RUNTIME_IMPORT:
        try:
            payload, _origin = runtime_bootstrap.active_member_source(
                "shared_contract_code", record["sha256"]
            )
        except runtime_bootstrap.RuntimeClosureError as exc:
            raise EvaluationError("live sealed shared contract is unavailable") from exc
        if (
            _sha256_bytes(payload) != record["sha256"]
            or _sha256_bytes(snapshot) != record["sha256"]
            or canonical_physical_cell_id.__module__ != shared_contract.__name__
        ):
            raise EvaluationError("live sealed shared scientific contract identity differs")
        return
    live_path = _file(
        Path(str(shared_contract.__file__)).resolve(strict=True),
        "live imported shared scientific contract",
    )
    if str(live_path) != record["path"] or _sha256_bytes(snapshot) != record["sha256"]:
        raise EvaluationError("live imported shared scientific contract identity differs")
    if canonical_physical_cell_id.__module__ != shared_contract.__name__:
        raise EvaluationError("live cell encoder provenance differs from shared contract")


def _verify_live_evaluator_from_snapshot(
    expected: Mapping[str, Any], snapshot: bytes
) -> None:
    record = _validate_pinned_identity(expected, "evaluator source")
    live_path = _file(Path(__file__), "live evaluator source")
    if (
        record != _MODULE_LOAD_EVALUATOR_IDENTITY
        or str(live_path) != record["path"]
        or _sha256_bytes(snapshot) != record["sha256"]
    ):
        raise EvaluationError(
            "executing evaluator code differs from the prepared/GO-bound source bytes"
        )


def _exact_keys(payload: Mapping[str, Any], required: set[str], label: str) -> None:
    if set(payload) != required:
        raise EvaluationError(
            f"{label} keyset mismatch: missing={sorted(required - set(payload))} "
            f"extra={sorted(set(payload) - required)}"
        )


def _all_true(raw: Any, label: str) -> None:
    if not isinstance(raw, dict) or not raw or not all(value is True for value in raw.values()):
        raise EvaluationError(f"{label} is missing or contains a non-true check")


def _model_key(seed: int, arm: str) -> str:
    return f"seed_{seed}__{arm}"


def _expected_model_keys() -> tuple[str, ...]:
    return tuple(
        _model_key(seed, arm)
        for seed in EXACT_PAIRED_SEEDS
        for arm in ("small", "large")
    )


def _read_archive_string(archive: Any, key: str) -> str:
    if key not in archive.files:
        raise EvaluationError(f"weights archive lacks {key}")
    values = np.asarray(archive[key]).reshape(-1)
    if values.size != 1:
        raise EvaluationError(f"weights archive {key} is not scalar")
    return str(values[0])


def _numbered_arrays(archive: Any, prefix: str) -> list[np.ndarray]:
    keys = sorted(
        (key for key in archive.files if key.startswith(prefix)),
        key=lambda key: int(key.removeprefix(prefix)),
    )
    if not keys:
        raise EvaluationError(f"weights archive lacks {prefix} arrays")
    arrays = [np.asarray(archive[key], dtype=np.float64).copy() for key in keys]
    if any(np.any(~np.isfinite(array)) for array in arrays):
        raise EvaluationError(f"weights archive has non-finite {prefix} arrays")
    return arrays


def _architecture(weights: Sequence[np.ndarray], biases: Sequence[np.ndarray]) -> list[int]:
    if not weights or len(weights) != len(biases):
        raise EvaluationError("network weight/bias layer counts differ")
    widths = [int(weights[0].shape[0])]
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        if weight.ndim != 2 or bias.ndim != 1:
            raise EvaluationError(f"network layer {index} has invalid rank")
        if weight.shape[0] != widths[-1] or weight.shape[1] != bias.shape[0]:
            raise EvaluationError(f"network layer {index} is disconnected")
        widths.append(int(weight.shape[1]))
    return widths


def _normalization_vectors(payload: dict[str, Any]) -> dict[str, np.ndarray]:
    if payload.get("schema") != NORMALIZATION_SCHEMA:
        raise EvaluationError("fixed normalization schema mismatch")
    if payload.get("input_columns") != list(INPUT_COLUMNS):
        raise EvaluationError("fixed normalization input columns mismatch")
    if payload.get("geometry_columns") != list(GEOMETRY_COLUMNS):
        raise EvaluationError("fixed normalization geometry columns mismatch")

    def vector(key: str, size: int) -> np.ndarray:
        try:
            values = np.asarray(payload[key], dtype=np.float64)
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationError(f"fixed normalization {key} is invalid") from exc
        if values.shape != (size,) or np.any(~np.isfinite(values)):
            raise EvaluationError(f"fixed normalization {key} is not {size} finite values")
        return values

    x_lower = vector("input_lower", 4)
    x_upper = vector("input_upper", 4)
    y_lower = vector("geometry_lower", 10)
    y_upper = vector("geometry_upper", 10)
    if np.any(x_upper <= x_lower) or np.any(y_upper <= y_lower):
        raise EvaluationError("fixed normalization bounds are not strictly increasing")
    expected_flags = {
        "train_arm_specific_statistics_used": False,
        "large_arm_empirical_statistics_used": False,
        "all_loaded_rows_required_inside_declared_bounds": True,
    }
    if any(payload.get(key) is not value for key, value in expected_flags.items()):
        raise EvaluationError("fixed normalization anti-leakage flags mismatch")
    return {
        "x_lower": x_lower,
        "x_upper": x_upper,
        "x_mean": 0.5 * (x_lower + x_upper),
        "x_scale": 0.5 * (x_upper - x_lower),
        "y_lower": y_lower,
        "y_upper": y_upper,
        "y_mean": 0.5 * (y_lower + y_upper),
        "y_scale": 0.5 * (y_upper - y_lower),
        "geometry_lower_normalized": -np.ones(10, dtype=np.float64),
        "geometry_upper_normalized": np.ones(10, dtype=np.float64),
    }


def _audit_weights(
    path: Path,
    normalization_sha: str,
    normalization: Mapping[str, np.ndarray],
    gradient_train_rows: int,
) -> dict[str, Any]:
    # allow_pickle=False is a release invariant, not merely a preference.
    with np.load(path, allow_pickle=False) as archive:
        archive_keys = set(archive.files)
        forward_weights = _numbered_arrays(archive, "forward_weight_")
        forward_biases = _numbered_arrays(archive, "forward_bias_")
        inverse_weights = _numbered_arrays(archive, "inverse_weight_")
        inverse_biases = _numbered_arrays(archive, "inverse_bias_")
        required_vectors = {
            "x_mean": "normalization__x_mean",
            "x_scale": "normalization__x_scale",
            "y_mean": "normalization__y_mean",
            "y_scale": "normalization__y_scale",
            "geometry_lower_normalized": "normalization__geometry_lower",
            "geometry_upper_normalized": "normalization__geometry_upper",
        }
        for expected_key, archive_key in required_vectors.items():
            if archive_key not in archive.files:
                raise EvaluationError(f"weights archive lacks {archive_key}")
            actual = np.asarray(archive[archive_key], dtype=np.float64)
            expected = np.asarray(normalization[expected_key], dtype=np.float64)
            if actual.shape != expected.shape or not np.array_equal(actual, expected):
                raise EvaluationError(f"weights {archive_key} differs from fixed normalization")
            if np.any(~np.isfinite(actual)):
                raise EvaluationError(f"weights {archive_key} is non-finite")
        additional_exact_vectors = {
            "normalization__feature_lower": np.asarray(INPUT_LOWER, dtype=np.float64),
            "normalization__feature_upper": np.asarray(INPUT_UPPER, dtype=np.float64),
            "normalization__response_loss_dimension_weights": np.ones(4, dtype=np.float64),
            "normalization__response_loss_physical_spans": np.asarray(INPUT_UPPER, dtype=np.float64)
            - np.asarray(INPUT_LOWER, dtype=np.float64),
            "optimizer_budget__forward_target_updates": np.asarray([1200], dtype=np.int64),
            "optimizer_budget__inverse_target_updates": np.asarray([1200], dtype=np.int64),
        }
        for archive_key, expected in additional_exact_vectors.items():
            if archive_key not in archive.files:
                raise EvaluationError(f"weights archive lacks {archive_key}")
            actual = np.asarray(archive[archive_key])
            if actual.shape != expected.shape or not np.array_equal(actual, expected):
                raise EvaluationError(f"weights {archive_key} differs from frozen contract")
        dimension_weights = np.asarray(
            archive["normalization__response_loss_dimension_weights"], dtype=np.float64
        )
        if dimension_weights.shape != (4,) or np.any(~np.isfinite(dimension_weights)) or np.any(dimension_weights <= 0):
            raise EvaluationError("weights response-loss dimension weights are invalid")
        projection = _read_archive_string(archive, "inverse_geometry_projection__mode")
        contract_mode = _read_archive_string(archive, "normalization_contract__mode")
        contract_sha = _read_archive_string(archive, "normalization_contract__sha256")
        if _read_archive_string(archive, "training_sampler__family") != "row_uniform":
            raise EvaluationError("weights sampler family changed")
        if _read_archive_string(archive, "optimizer_budget__mode") != "fixed_optimizer_updates":
            raise EvaluationError("weights optimizer budget mode changed")
        for key in (
            "training_sampler__fingerprint_sha256",
            "optimizer_budget__fingerprint_sha256",
        ):
            if not _is_sha(_read_archive_string(archive, key)):
                raise EvaluationError(f"weights {key} is not SHA-256")
        expected_sampler_counts = {
            "training_sampler__draws_per_epoch": int(gradient_train_rows),
            "training_sampler__optimizer_updates_per_epoch": int(
                math.ceil(gradient_train_rows / 1024.0)
            ),
        }
        for key, expected in expected_sampler_counts.items():
            value = np.asarray(archive[key]) if key in archive.files else np.asarray([])
            if (
                value.shape != (1,)
                or value.dtype.kind not in "biu"
                or int(value[0]) != expected
            ):
                raise EvaluationError(f"weights {key} differs from frozen row budget")
        try:
            topology = _strict_json_loads(
                _read_archive_string(
                    archive, "inverse_geometry_projection__topology_contract_json"
                ),
                "weights inverse geometry topology contract",
            )
        except EvaluationError as exc:
            raise EvaluationError("weights topology metadata is not JSON") from exc
        topology_overlap = (
            topology.get("power_line_port_ground_overlap")
            if type(topology) is dict
            else None
        )
        if (
            type(topology) is not dict
            or set(topology)
            != {
                "enabled",
                "weight",
                "geometry_columns",
                "power_line_port_ground_overlap",
            }
            or topology.get("enabled") is not False
            or type(topology.get("weight")) is not float
            or topology.get("weight") != 0.0
            or topology.get("geometry_columns") != list(GEOMETRY_COLUMNS)
            or type(topology_overlap) is not dict
            or set(topology_overlap) != {"enabled"}
            or topology_overlap.get("enabled") is not False
        ):
            raise EvaluationError("weights topology metadata differs from zero-weight contract")
        layer_keys = {
            *(f"forward_weight_{index}" for index in range(4)),
            *(f"forward_bias_{index}" for index in range(4)),
            *(f"inverse_weight_{index}" for index in range(4)),
            *(f"inverse_bias_{index}" for index in range(4)),
        }
        exact_keys = layer_keys | set(required_vectors.values()) | set(additional_exact_vectors) | {
            "normalization_contract__mode",
            "normalization_contract__sha256",
            "training_sampler__family",
            "training_sampler__fingerprint_sha256",
            "training_sampler__draws_per_epoch",
            "training_sampler__optimizer_updates_per_epoch",
            "optimizer_budget__mode",
            "optimizer_budget__fingerprint_sha256",
            "inverse_geometry_projection__mode",
            "inverse_geometry_projection__topology_contract_json",
        }
        if archive_keys != exact_keys or len(archive_keys) != 38:
            raise EvaluationError(
                "weights archive is not the exact 38-key controlled contract: "
                f"missing={sorted(exact_keys - archive_keys)} extra={sorted(archive_keys - exact_keys)}"
            )
        for key in archive_keys:
            array = np.asarray(archive[key])
            if array.dtype.kind == "O":
                raise EvaluationError(f"weights archive contains object array: {key}")
            if array.dtype.kind in "biufc" and np.any(~np.isfinite(array)):
                raise EvaluationError(f"weights archive contains non-finite numeric array: {key}")
    forward_arch = _architecture(forward_weights, forward_biases)
    inverse_arch = _architecture(inverse_weights, inverse_biases)
    if forward_arch != [10, 256, 256, 256, 4]:
        raise EvaluationError(f"forward architecture changed: {forward_arch}")
    if inverse_arch != [4, 256, 256, 256, 10]:
        raise EvaluationError(f"inverse architecture changed: {inverse_arch}")
    if projection != "independent_sigmoid":
        raise EvaluationError(f"decoder changed: {projection}")
    if contract_mode != "external_declared_midpoint_half_range":
        raise EvaluationError(f"normalization mode changed: {contract_mode}")
    if contract_sha != normalization_sha:
        raise EvaluationError("weights normalization-contract SHA mismatch")
    return {
        "forward_architecture": forward_arch,
        "inverse_architecture": inverse_arch,
        "decoder": projection,
        "normalization_contract_mode": contract_mode,
        "normalization_contract_sha256": contract_sha,
    }


def _audit_model_summary(
    path: Path,
    *,
    seed: int,
    arm: str,
    weights_sha: str,
    normalization_sha: str,
    holdout_sha: str,
    fixture_mode: bool,
) -> dict[str, Any]:
    payload = _json(path, f"{arm}/{seed} model summary")
    expected_training_count = 10_000 if arm == "small" else 20_000
    expected_split = {
        "train": 7_871 if arm == "small" else 17_871,
        "validation": 1_227,
        "test": 902,
    }
    if fixture_mode:
        expected_training_count = _require_exact_int(
            payload.get("training_count"),
            "fixture model summary training_count",
            minimum=1,
        )
        raw_fixture_split = (payload.get("split_audit") or {}).get("row_counts") or {}
        try:
            expected_split = {
                name: _require_exact_int(
                    raw_fixture_split[name],
                    f"fixture model summary split {name}",
                    minimum=1,
                )
                for name in ("train", "validation", "test")
            }
        except KeyError as exc:
            raise EvaluationError("fixture model summary lacks exact split row counts") from exc
    _require_exact_int(
        payload.get("training_count"),
        f"{arm}/{seed} model training_count",
        expected=expected_training_count,
    )
    checks = {
        "execution_pass": payload.get("execution_status") == "PASS",
        "validation_only_status": payload.get("overall_status")
        == "COMPLETE_REVIEW_REQUIRED",
        "validation_only_quality": payload.get("quality_status")
        == "REVIEW_REQUIRED_VALIDATION_ONLY",
        "evaluation_mode": payload.get("evaluation_mode") == "validation_only",
        "not_acceptance_eligible": payload.get("eligible_for_checkpoint_model_acceptance")
        is False,
        "not_success_claim_eligible": payload.get("eligible_for_model_success_claim") is False,
        "input_columns": payload.get("input_columns") == list(INPUT_COLUMNS),
        "geometry_columns": payload.get("geometry_columns") == list(GEOMETRY_COLUMNS),
        "training_count": True,
    }
    test_access = payload.get("test_access_contract") or {}
    checks.update(
        {
            "test_access_zero": _require_exact_int(
                test_access.get("test_access_event_count"),
                f"{arm}/{seed} summary test access count",
                expected=0,
            )
            == 0,
            "test_not_evaluated": test_access.get("test_evaluator_called") is False,
            "test_not_training": test_access.get("test_used_for_training") is False,
            "test_not_selection": test_access.get("test_used_for_model_or_hyperparameter_selection")
            is False,
        }
    )
    arguments = payload.get("arguments") or {}
    checks.update(
        {
            "seed": _require_exact_int(
                arguments.get("seed"), f"{arm}/{seed} summary seed", expected=seed
            )
            == seed,
            "local_refinement_zero": _require_exact_int(
                arguments.get("local_refinement_steps"),
                f"{arm}/{seed} summary local refinement steps",
                expected=0,
            )
            == 0,
            "projection": str(arguments.get("inverse_geometry_projection") or "")
            == "independent_sigmoid",
        }
    )
    norm = payload.get("normalization_contract") or {}
    split = payload.get("split_audit") or {}
    split_rows = split.get("row_counts") or {}
    checks.update(
        {
            "normalization_sha": norm.get("sha256") == normalization_sha,
            "normalization_mode": norm.get("mode")
            == "external_declared_midpoint_half_range",
            "holdout_sha": (
                (split.get("fixed_common_holdout_manifest") or {}).get("sha256")
                == holdout_sha
            ),
            "split_mode": split.get("split_mode") == "fixed_common_holdout_manifest",
            "split_rows": all(
                _require_exact_int(
                    split_rows.get(name),
                    f"{arm}/{seed} summary split {name}",
                    expected=count,
                )
                == count
                for name, count in expected_split.items()
            ),
        }
    )
    weights_field = str(payload.get("weights_npz") or "")
    if weights_field:
        weights_path = Path(weights_field).expanduser().resolve()
        checks["summary_weights_sha"] = weights_path.is_file() and _sha256(weights_path) == weights_sha
    if not all(checks.values()):
        failed = sorted(key for key, value in checks.items() if not value)
        raise EvaluationError(f"{arm}/{seed} model summary gates failed: {failed}")
    return {
        "training_count": expected_training_count,
        "gradient_train_rows": expected_split["train"],
        "evaluation_mode": "validation_only",
        "test_access_event_count": 0,
        "checks": checks,
    }


def _binding_core(binding: Mapping[str, Any]) -> dict[str, str]:
    return {"path": str(binding["path"]), "sha256": str(binding["sha256"])}


def _audit_runtime_module_origins(
    raw: Any,
    closure: Mapping[str, Any],
    label: str,
) -> dict[str, dict[str, str]]:
    """Audit controlled imports against sealed archive/native origins and roles."""

    if type(raw) is not dict or any(type(name) is not str or not name for name in raw):
        raise EvaluationError(f"{label} module origins are not an exact object")
    package = "rfic_transformer_inverse_design"
    role_by_module = {
        package: "package_init_code",
        f"{package}.controlled_real10k_20k_contract": "shared_contract_code",
        f"{package}.model_splitting": "splitter_code",
        runtime_bootstrap.BOOTSTRAP_MODULE: "runtime_bootstrap_code",
    }
    required_modules = {"numpy", *role_by_module}
    if not required_modules.issubset(raw):
        raise EvaluationError(f"{label} lacks required sealed module origins")
    roles = closure.get("role_bindings")
    if type(roles) is not dict:
        raise EvaluationError(f"{label} descriptor role bindings are missing")
    audited: dict[str, dict[str, str]] = {}
    for module_name, record in raw.items():
        if type(record) is not dict:
            raise EvaluationError(f"{label} module origin is not an object: {module_name}")
        _exact_keys(
            record,
            {"kind", "origin", "sha256"},
            f"{label} module origin {module_name}",
        )
        kind = record.get("kind")
        origin = record.get("origin")
        sha = _require_sha_token(
            record.get("sha256"), f"{label} module origin {module_name} SHA"
        )
        if kind == "sealed_pure_zip":
            if not isinstance(origin, str) or not origin.startswith(
                "descriptor-zip:/proc/self/fd/"
            ):
                raise EvaluationError(f"{label} pure module origin is not descriptor sealed")
        elif kind == "sealed_native_extension":
            if not isinstance(origin, str) or not origin.startswith("/proc/self/fd/"):
                raise EvaluationError(f"{label} native module origin is not descriptor sealed")
        else:
            raise EvaluationError(f"{label} module origin kind is not sealed")
        role = role_by_module.get(module_name)
        if role is not None:
            role_binding = roles.get(role)
            if type(role_binding) is not dict or sha != role_binding.get("sha256"):
                raise EvaluationError(
                    f"{label} module origin does not bind descriptor role {role}"
                )
        audited[module_name] = {"kind": kind, "origin": origin, "sha256": sha}
    return audited


def _read_runtime_attestation_once(
    path: Path,
    expected_binding: Mapping[str, Any],
    label: str,
) -> bytes:
    """Read one immutable attestation inode once with pathname continuity."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise EvaluationError(f"cannot open {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise EvaluationError(f"{label} is not a single-link regular file")
        if stat.S_IMODE(before.st_mode) != 0o400:
            raise EvaluationError(f"{label} mode is not immutable 0400")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        payload = b"".join(blocks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if any(
        getattr(before, field) != getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink")
    ):
        raise EvaluationError(f"{label} inode changed during its single read")
    lexical = path.lstat()
    if (lexical.st_dev, lexical.st_ino) != (after.st_dev, after.st_ino):
        raise EvaluationError(f"{label} pathname changed during its single read")
    if _sha256_bytes(payload) != expected_binding.get("sha256"):
        raise EvaluationError(f"{label} held-byte SHA differs from its receipt binding")
    _require_exact_int(
        expected_binding.get("size_bytes"),
        f"{label} held-byte size binding",
        expected=len(payload),
    )
    return payload


def _audit_runtime_attestation_file(
    binding: Mapping[str, Any],
    paired_runtime: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    """Independently audit both descriptor-runtime trainer attestations."""

    path = Path(str(binding["path"]))
    payload = _read_runtime_attestation_once(path, binding, label)
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise EvaluationError(f"{label} is not ASCII JSONL") from exc
    if len(lines) != 2 or not payload.endswith(b"\n"):
        raise EvaluationError(f"{label} must contain exactly two JSON records")
    records = [
        _strict_json_loads(line, f"{label} line {index}")
        for index, line in enumerate(lines, start=1)
    ]
    if any(type(record) is not dict for record in records):
        raise EvaluationError(f"{label} record is not an object")

    closure = paired_runtime.get("descriptor_closure")
    if type(closure) is not dict:
        raise EvaluationError(f"{label} paired descriptor closure is missing")
    for role in ("manifest", "pure_archive", "bootstrap"):
        if type(closure.get(role)) is not dict:
            raise EvaluationError(f"{label} descriptor {role} binding is missing")
    common = {
        "schema": runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "trainer",
        "manifest_sha256": _require_sha_token(
            closure["manifest"].get("sha256"), f"{label} manifest SHA"
        ),
        "pure_archive_sha256": _require_sha_token(
            closure["pure_archive"].get("sha256"), f"{label} pure archive SHA"
        ),
        "bootstrap_sha256": _require_sha_token(
            closure["bootstrap"].get("sha256"), f"{label} bootstrap SHA"
        ),
    }
    expected_system_libraries = closure.get("system_library_allowlist")
    if type(expected_system_libraries) is not list or any(
        type(value) is not str for value in expected_system_libraries
    ):
        raise EvaluationError(f"{label} descriptor system-library allowlist is invalid")
    _require_exact_json_equal(
        expected_system_libraries,
        list(runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
        f"{label} active bootstrap system-library allowlist",
    )
    expected_native_libraries: dict[str, str] = {}
    for record in closure.get("native_libraries", []):
        if type(record) is not dict or type(record.get("soname")) is not str:
            raise EvaluationError(f"{label} descriptor native-library record is invalid")
        soname = record["soname"]
        if not soname or soname in expected_native_libraries:
            raise EvaluationError(f"{label} descriptor native-library SONAME differs")
        expected_native_libraries[soname] = _require_sha_token(
            record.get("sha256"), f"{label} native library {soname} SHA"
        )
    expected_native_extensions: dict[str, str] = {}
    for record in closure.get("native_extensions", []):
        if type(record) is not dict or type(record.get("module")) is not str:
            raise EvaluationError(f"{label} descriptor native-extension record is invalid")
        module_name = record["module"]
        if not module_name or module_name in expected_native_extensions:
            raise EvaluationError(f"{label} descriptor native-extension module differs")
        expected_native_extensions[module_name] = _require_sha_token(
            record.get("sha256"), f"{label} native extension {module_name} SHA"
        )

    startup, terminal = records
    _exact_keys(
        startup,
        {
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
        },
        f"{label} startup record",
    )
    expected_python = closure.get("python")
    if type(expected_python) is not dict:
        raise EvaluationError(f"{label} descriptor Python identity is missing")
    _exact_keys(
        expected_python,
        {"implementation", "version", "abi_tag", "platform", "executable_sha256"},
        f"{label} descriptor Python identity",
    )
    trainer_role = (closure.get("role_bindings") or {}).get("trainer_code")
    if type(trainer_role) is not dict:
        raise EvaluationError(f"{label} descriptor trainer role binding is missing")
    expected_startup = {
        **common,
        "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
        "entrypoint_sha256": _require_sha_token(
            trainer_role.get("sha256"), f"{label} trainer entrypoint SHA"
        ),
        "python": {
            key: expected_python[key]
            for key in ("implementation", "version", "abi_tag", "platform")
        },
        "python_flags": {"isolated": 1, "no_site": 1, "dont_write_bytecode": True},
        "numpy_version": paired_runtime.get("numpy_version"),
        "native_library_sha256": expected_native_libraries,
        "native_extension_sha256": expected_native_extensions,
        "system_library_allowlist": expected_system_libraries,
        "site_initialization_disabled": True,
        "external_package_fallback_allowed": False,
    }
    for key, expected in expected_startup.items():
        _require_exact_json_equal(
            startup.get(key), expected, f"{label} startup {key}"
        )
    startup_modules = _audit_runtime_module_origins(
        startup.get("module_origins"), closure, f"{label} startup"
    )

    _exact_keys(
        terminal,
        {
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
        },
        f"{label} terminal record",
    )
    expected_terminal = {
        **common,
        "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
        "exit_code": 0,
        "system_library_allowlist": expected_system_libraries,
        "external_package_fallback_allowed": False,
    }
    for key, expected in expected_terminal.items():
        _require_exact_json_equal(
            terminal.get(key), expected, f"{label} terminal {key}"
        )
    terminal_modules = _audit_runtime_module_origins(
        terminal.get("module_origins"), closure, f"{label} terminal"
    )
    for module_name, startup_record in startup_modules.items():
        if terminal_modules.get(module_name) != startup_record:
            raise EvaluationError(
                f"{label} controlled module origin changed before terminal attestation"
            )
    return {
        "path": str(path),
        "sha256": str(binding["sha256"]),
        "size_bytes": len(payload),
        "record_count": 2,
        "startup_status": startup["status"],
        "terminal_status": terminal["status"],
        "manifest_sha256": common["manifest_sha256"],
        "pure_archive_sha256": common["pure_archive_sha256"],
        "bootstrap_sha256": common["bootstrap_sha256"],
    }


def _audit_arm_terminal(
    pointer_binding: dict[str, Any],
    *,
    controller_root: Path,
    run_contract_binding: dict[str, Any],
    seed: int,
    arm: str,
    normalization_sha: str,
    holdout_sha: str,
    normalization: Mapping[str, np.ndarray],
    paired_runtime: Mapping[str, Any] | None,
    controlled_singleton: Mapping[str, Any] | None,
    fixture_mode: bool,
) -> dict[str, Any]:
    pointer_path = Path(pointer_binding["path"])
    expected_pointer_path = (
        controller_root / "receipts" / f"seed_{seed}_{arm}" / "COMPLETE_RECEIPT.json"
    )
    if pointer_path != expected_pointer_path:
        raise EvaluationError(f"{arm}/{seed} completion-pointer path mismatch")
    pointer = _json(pointer_path, f"{arm}/{seed} completion pointer")
    _exact_keys(
        pointer,
        {"schema", "status", "seed", "arm", "attempt_complete"},
        f"{arm}/{seed} completion pointer",
    )
    if (
        pointer.get("schema") != ARM_TERMINAL_POINTER_SCHEMA
        or pointer.get("status") != "COMPLETE"
        or pointer.get("seed") != seed
        or pointer.get("arm") != arm
    ):
        raise EvaluationError(f"{arm}/{seed} completion-pointer semantic mismatch")
    attempt_binding = _binding(
        pointer.get("attempt_complete"),
        pointer_path.parent,
        f"{arm}/{seed} attempt completion receipt",
    )
    expected_attempt_path = pointer_path.parent / "attempt_0001" / "COMPLETE_RECEIPT.json"
    if Path(attempt_binding["path"]) != expected_attempt_path:
        raise EvaluationError(f"{arm}/{seed} does not bind exact attempt_0001")
    attempt = _json(expected_attempt_path, f"{arm}/{seed} attempt completion receipt")
    attempt_keys = {
        "schema",
        "generated_utc",
        "status",
        "seed",
        "arm",
        "returncode",
        "evaluation_mode",
        "test_access_event_count",
        "python_isolation_flags",
        "effective_environment",
        "effective_environment_sha256",
        "run_contract",
        "command",
        "intent",
        "running",
        "stdout",
        "stderr",
        "output_manifest",
        "contract_checks",
        "summary",
        "weights",
        "fresh_emx_accessed",
        "numerical_metrics_released",
    }
    audited_runtime_attestation: dict[str, Any] | None = None
    if not fixture_mode:
        attempt_keys |= {
            "runtime_attestation",
            "runtime_dependency_closure",
            "controlled_singleton",
        }
    _exact_keys(
        attempt,
        attempt_keys,
        f"{arm}/{seed} attempt completion receipt",
    )
    _parse_utc(attempt.get("generated_utc"), f"{arm}/{seed} completion time")
    expected_attempt_schema = (
        LEGACY_FIXTURE_ARM_TERMINAL_RECEIPT_SCHEMA
        if fixture_mode
        else ARM_TERMINAL_RECEIPT_SCHEMA
    )
    expected_attempt_scalars = {
        "schema": expected_attempt_schema,
        "status": "COMPLETE",
        "seed": seed,
        "arm": arm,
        "returncode": 0,
        "evaluation_mode": "validation_only",
        "test_access_event_count": 0,
        "python_isolation_flags": FROZEN_TRAINER_LAUNCH_CONTRACT[
            "python_isolation_flags"
        ],
        "effective_environment_sha256": FROZEN_TRAINER_LAUNCH_CONTRACT[
            "effective_environment_sha256"
        ],
        "fresh_emx_accessed": False,
        "numerical_metrics_released": False,
    }
    if (
        any(key not in attempt for key in expected_attempt_scalars)
    ):
        raise EvaluationError(f"{arm}/{seed} attempt completion contract mismatch")
    _require_exact_json_equal(
        {key: attempt[key] for key in expected_attempt_scalars},
        expected_attempt_scalars,
        f"{arm}/{seed} attempt completion scalars",
    )
    _require_exact_json_equal(
        attempt.get("effective_environment"),
        FROZEN_TRAINER_LAUNCH_CONTRACT["effective_environment"],
        f"{arm}/{seed} effective trainer environment",
    )
    _all_true(attempt.get("contract_checks"), f"{arm}/{seed} contract checks")
    live_contract = _binding(
        attempt.get("run_contract"), expected_attempt_path.parent, f"{arm}/{seed} run contract"
    )
    if _binding_core(live_contract) != _binding_core(run_contract_binding):
        raise EvaluationError(f"{arm}/{seed} run-contract SHA closure mismatch")
    canonical_attempt_artifacts = {
        "command": controller_root / "commands" / f"seed_{seed}_{arm}.json",
        "intent": expected_attempt_path.parent / "INTENT_RECEIPT.json",
        "running": expected_attempt_path.parent / "RUNNING_RECEIPT.json",
        "stdout": expected_attempt_path.parent / "stdout.log",
        "stderr": expected_attempt_path.parent / "stderr.log",
        "output_manifest": expected_attempt_path.parent / "OUTPUT_ARTIFACT_MANIFEST.json",
    }
    audited_attempt_artifacts: dict[str, dict[str, Any]] = {}
    for field, expected_path in canonical_attempt_artifacts.items():
        audited = _binding(
            attempt.get(field),
            expected_attempt_path.parent,
            f"{arm}/{seed} {field}",
        )
        if Path(audited["path"]) != expected_path:
            raise EvaluationError(f"{arm}/{seed} {field} canonical path mismatch")
        audited_attempt_artifacts[field] = audited
    if not fixture_mode:
        if paired_runtime is None or controlled_singleton is None:
            raise EvaluationError(f"{arm}/{seed} production runtime binding is missing")
        _require_exact_json_equal(
            attempt.get("runtime_dependency_closure"),
            paired_runtime.get("descriptor_closure"),
            f"{arm}/{seed} runtime dependency closure",
        )
        _require_exact_json_equal(
            attempt.get("controlled_singleton"),
            controlled_singleton,
            f"{arm}/{seed} controlled singleton",
        )
        runtime_attestation = attempt.get("runtime_attestation")
        if type(runtime_attestation) is not dict:
            raise EvaluationError(f"{arm}/{seed} runtime attestation binding is missing")
        _exact_keys(
            runtime_attestation,
            {
                "path",
                "sha256",
                "record_count",
                "startup_status",
                "terminal_status",
            },
            f"{arm}/{seed} runtime attestation binding",
        )
        attestation_binding = _binding(
            runtime_attestation,
            expected_attempt_path.parent,
            f"{arm}/{seed} runtime attestation",
        )
        if Path(attestation_binding["path"]) != (
            expected_attempt_path.parent / "RUNTIME_ATTESTATION.jsonl"
        ):
            raise EvaluationError(f"{arm}/{seed} runtime attestation path differs")
        _require_exact_int(
            runtime_attestation.get("record_count"),
            f"{arm}/{seed} runtime attestation record count",
            expected=2,
        )
        if (
            runtime_attestation.get("startup_status")
            != "PASS_DESCRIPTOR_CLOSED_STARTUP"
            or runtime_attestation.get("terminal_status")
            != "PASS_DESCRIPTOR_CLOSED_TERMINAL"
        ):
            raise EvaluationError(f"{arm}/{seed} runtime attestation status differs")
        audited_runtime_attestation = _audit_runtime_attestation_file(
            attestation_binding,
            paired_runtime,
            f"{arm}/{seed} runtime attestation",
        )
        _require_exact_json_equal(
            {
                key: audited_runtime_attestation[key]
                for key in (
                    "path",
                    "sha256",
                    "record_count",
                    "startup_status",
                    "terminal_status",
                )
            },
            runtime_attestation,
            f"{arm}/{seed} independently audited runtime attestation binding",
        )
    summary = _binding(
        attempt.get("summary"), expected_attempt_path.parent, f"{arm}/{seed} model summary"
    )
    weights_raw = attempt.get("weights")
    if not isinstance(weights_raw, dict):
        raise EvaluationError(f"{arm}/{seed} weights binding is not an object")
    if (
        weights_raw.get("required_layer_shapes_exact") is not True
        or weights_raw.get("all_numeric_finite") is not True
    ):
        raise EvaluationError(f"{arm}/{seed} runner weights audit flags failed")
    weights = _binding(
        weights_raw, expected_attempt_path.parent, f"{arm}/{seed} model weights"
    )
    expected_run_dir = controller_root / "runs" / f"seed_{seed}" / arm
    if Path(summary["path"]) != expected_run_dir / "physical_feature_tandem_inverse_summary.json":
        raise EvaluationError(f"{arm}/{seed} model-summary path mismatch")
    if Path(weights["path"]) != expected_run_dir / "physical_feature_tandem_inverse_weights.npz":
        raise EvaluationError(f"{arm}/{seed} model-weights path mismatch")
    output_manifest_binding = audited_attempt_artifacts["output_manifest"]
    output_manifest = _json(
        Path(output_manifest_binding["path"]), f"{arm}/{seed} output manifest"
    )
    if (
        output_manifest.get("schema") != "controlled_real10k_20k_arm_output_manifest_v1"
        or output_manifest.get("root") != str(expected_run_dir)
        or output_manifest.get("excluded_paths") != []
        or output_manifest.get("all_regular_outputs_indexed") is not True
    ):
        raise EvaluationError(f"{arm}/{seed} output-manifest boundary mismatch")
    output_records = output_manifest.get("artifacts")
    if not isinstance(output_records, list):
        raise EvaluationError(f"{arm}/{seed} output manifest lacks artifacts")
    output_by_path: dict[str, dict[str, Any]] = {}
    for record in output_records:
        if not isinstance(record, dict):
            raise EvaluationError(f"{arm}/{seed} output manifest record is invalid")
        _exact_keys(
            record,
            {"relative_path", "path", "sha256", "size_bytes"},
            f"{arm}/{seed} output artifact",
        )
        artifact = _binding(record, expected_run_dir, f"{arm}/{seed} output artifact")
        if Path(artifact["path"]) != expected_run_dir / str(record["relative_path"]):
            raise EvaluationError(f"{arm}/{seed} output artifact relative path mismatch")
        if artifact["path"] in output_by_path:
            raise EvaluationError(f"{arm}/{seed} output manifest duplicates an artifact")
        output_by_path[artifact["path"]] = artifact
    for artifact in (summary, weights):
        if output_by_path.get(artifact["path"]) != artifact:
            raise EvaluationError(f"{arm}/{seed} output manifest lacks model SHA closure")
    summary_audit = _audit_model_summary(
        Path(summary["path"]),
        seed=seed,
        arm=arm,
        weights_sha=weights["sha256"],
        normalization_sha=normalization_sha,
        holdout_sha=holdout_sha,
        fixture_mode=fixture_mode,
    )
    weights_audit = _audit_weights(
        Path(weights["path"]),
        normalization_sha,
        normalization,
        summary_audit["gradient_train_rows"],
    )
    return {
        "seed": seed,
        "arm": arm,
        "summary": summary,
        "weights": weights,
        "terminal_receipt": _binding_core(pointer_binding),
        "attempt_terminal_receipt": _binding_core(attempt_binding),
        "runtime_attestation": audited_runtime_attestation,
        "summary_audit": summary_audit,
        "weights_audit": weights_audit,
    }


def _audit_pair_receipt(
    binding: dict[str, Any],
    *,
    controller_root: Path,
    run_contract_binding: dict[str, Any],
    seed: int,
    normalization_sha: str,
    holdout_sha: str,
    normalization: Mapping[str, np.ndarray],
    paired_runtime: Mapping[str, Any] | None,
    controlled_singleton: Mapping[str, Any] | None,
    fixture_mode: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(binding["path"])
    expected_path = controller_root / "receipts" / f"seed_{seed}_PAIR_COMPLETE_RECEIPT.json"
    if path != expected_path:
        raise EvaluationError(f"seed {seed} pair-receipt path mismatch")
    payload = _json(path, f"seed {seed} pair receipt")
    pair_keys = {"schema", "status", "seed", "arm_completion_receipts", "checks"}
    if not fixture_mode:
        pair_keys |= {"runtime_dependency_closure", "controlled_singleton"}
    _exact_keys(
        payload,
        pair_keys,
        f"seed {seed} pair receipt",
    )
    if (
        payload.get("schema") != PAIR_TERMINAL_RECEIPT_SCHEMA
        or payload.get("status") != "COMPLETE"
        or payload.get("seed") != seed
    ):
        raise EvaluationError(f"seed {seed} pair-receipt semantic mismatch")
    if not fixture_mode:
        if paired_runtime is None or controlled_singleton is None:
            raise EvaluationError(f"seed {seed} production runtime binding is missing")
        _require_exact_json_equal(
            payload.get("runtime_dependency_closure"),
            paired_runtime.get("descriptor_closure"),
            f"seed {seed} pair runtime closure",
        )
        _require_exact_json_equal(
            payload.get("controlled_singleton"),
            controlled_singleton,
            f"seed {seed} pair controlled singleton",
        )
    checks = payload.get("checks")
    if not isinstance(checks, dict) or set(checks) != {
        "paired_seed_exact",
        "execution_order_exact",
        "both_validation_only",
        "both_test_access_zero",
        "both_complete",
    }:
        raise EvaluationError(f"seed {seed} pair check keyset mismatch")
    _all_true(checks, f"seed {seed} pair checks")
    raw_pointers = payload.get("arm_completion_receipts")
    if not isinstance(raw_pointers, list) or len(raw_pointers) != 2:
        raise EvaluationError(f"seed {seed} pair lacks two arm pointers")
    models: list[dict[str, Any]] = []
    for raw, arm in zip(raw_pointers, ("small", "large")):
        pointer_binding = _binding(raw, path.parent, f"seed {seed} {arm} completion pointer")
        models.append(
            _audit_arm_terminal(
                pointer_binding,
                controller_root=controller_root,
                run_contract_binding=run_contract_binding,
                seed=seed,
                arm=arm,
                normalization_sha=normalization_sha,
                holdout_sha=holdout_sha,
                normalization=normalization,
                paired_runtime=paired_runtime,
                controlled_singleton=controlled_singleton,
                fixture_mode=fixture_mode,
            )
        )
    return {"path": str(path), "sha256": binding["sha256"], "seed": seed}, models


def _audit_materialization_candidate_record(
    raw: Any,
    role: str,
    reduced: Mapping[str, Any],
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise EvaluationError(f"materialization candidate binding is not an object: {role}")
    _exact_keys(
        raw,
        {
            "role",
            "path",
            "sha256",
            "size_bytes",
            "mode_octal",
            "nlink",
            "st_dev",
            "st_ino",
        },
        f"materialization candidate binding {role}",
    )
    if raw.get("role") != role:
        raise EvaluationError(f"materialization candidate binding role differs: {role}")
    path = _file(raw.get("path", ""), f"materialization candidate role {role}")
    metadata = path.lstat()
    observed = {
        "role": role,
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": int(metadata.st_size),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "nlink": int(metadata.st_nlink),
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
    }
    _require_exact_json_equal(raw, observed, f"live materialization candidate role {role}")
    _exact_keys(
        reduced,
        {"path", "sha256"},
        f"paired reduced materialization binding {role}",
    )
    _require_exact_json_equal(
        dict(reduced),
        {"path": observed["path"], "sha256": observed["sha256"]},
        f"candidate/reduced materialization binding {role}",
    )
    return observed


def _audit_process_singleton_contract_payload(raw: Any) -> dict[str, Any]:
    if type(raw) is not dict:
        raise EvaluationError("package process-singleton contract is not an object")
    _exact_keys(
        raw,
        {
            "schema",
            "lock",
            "protected_entrypoints",
            "proc_audit",
            "lifetime",
            "conflict_policy",
        },
        "package process-singleton contract",
    )
    if raw.get("schema") != PROCESS_SINGLETON_CONTRACT_SCHEMA:
        raise EvaluationError("package process-singleton contract schema differs")
    _require_exact_json_equal(
        raw.get("lock"),
        {
            "relative_path": "CONTROLLED_SINGLETON.lock",
            "basename": "CONTROLLED_SINGLETON.lock",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "required_mode_octal": "0444",
            "required_nlink": 1,
            "mechanism": "fcntl.flock",
            "operation": "LOCK_EX|LOCK_NB",
            "open_flags": ["O_CLOEXEC", "O_NOFOLLOW", "O_RDONLY"],
            "scope": "one_active_controlled_controller_per_package_identity",
        },
        "package process-singleton lock contract",
    )
    expected_roles = {
        "evaluator_code": (True, "sealed_runtime_entrypoint", "evaluator"),
        "materialization_builder_code": (
            False,
            "sealed_in_process_member",
            "materialization",
        ),
        "materialization_gate_code": (
            True,
            "sealed_runtime_entrypoint",
            "materialization",
        ),
        "native_smoke_test": (False, "sealed_runtime_entrypoint", "native_smoke"),
        "preflight_code": (True, "raw_hash_bound_script", None),
        "runner_code": (True, "sealed_runtime_entrypoint", "runner"),
        "runtime_bootstrap_code": (False, "sealed_bootstrap_fd", None),
        "trainer_code": (False, "sealed_runtime_entrypoint", "trainer"),
    }
    protected = raw.get("protected_entrypoints")
    if type(protected) is not list or len(protected) != len(expected_roles):
        raise EvaluationError("package process-singleton protected role count differs")
    if [record.get("role") if type(record) is dict else None for record in protected] != sorted(
        expected_roles
    ):
        raise EvaluationError("package process-singleton protected role order differs")
    for record in protected:
        _exact_keys(
            record,
            {"role", "path", "controller", "execution_identity", "runtime_entrypoint"},
            "package process-singleton protected entrypoint",
        )
        role = record["role"]
        controller, execution_identity, runtime_entrypoint = expected_roles[role]
        path = record.get("path")
        if (
            type(path) is not str
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise EvaluationError("package process-singleton protected path is invalid")
        _require_exact_json_equal(
            {
                "controller": record.get("controller"),
                "execution_identity": record.get("execution_identity"),
                "runtime_entrypoint": record.get("runtime_entrypoint"),
            },
            {
                "controller": controller,
                "execution_identity": execution_identity,
                "runtime_entrypoint": runtime_entrypoint,
            },
            f"package process-singleton protected role {role}",
        )
    _require_exact_json_equal(
        raw.get("proc_audit"),
        {
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
        "package process-singleton proc audit contract",
    )
    _require_exact_json_equal(
        raw.get("lifetime"),
        {
            "owner": "top_level_controller_process",
            "acquire_before": "EXECUTE_state_audit_and_any_controlled_child_launch",
            "held_across_all_controlled_children": True,
            "release_after": "terminal_receipt_or_commit_file_and_parent_directory_fsync",
            "full_lifetime_required": True,
        },
        "package process-singleton lifetime contract",
    )
    _require_exact_json_equal(
        raw.get("conflict_policy"),
        {
            "verdict": "NO_GO_DUPLICATE_CONTROLLED_PROCESS",
            "controlled_process_start_authorized": False,
            "process_signal_authorized": False,
            "process_kill_authorized": False,
            "automatic_cleanup_authorized": False,
        },
        "package process-singleton conflict policy",
    )
    return json.loads(json.dumps(raw))


def _read_bound_json_file(
    path: Path,
    expected_sha256: Any,
    label: str,
    *,
    expected_record: Mapping[str, Any] | None = None,
    expected_mode_octal: str = "0444",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one exact immutable JSON inode through an O_NOFOLLOW descriptor."""

    path = _absolute_lexical(path)
    _reject_symlink_chain(path, label)
    wanted_sha = _require_sha_token(expected_sha256, f"{label} SHA")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise EvaluationError(f"cannot open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or f"{stat.S_IMODE(before.st_mode):04o}" != expected_mode_octal
        ):
            raise EvaluationError(f"{label} immutable file identity differs")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
        identity_fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink")
        if any(getattr(before, key) != getattr(after, key) for key in identity_fields):
            raise EvaluationError(f"{label} changed during held read")
        try:
            lexical = path.lstat()
        except FileNotFoundError as exc:
            raise EvaluationError(f"{label} pathname disappeared") from exc
        if any(getattr(after, key) != getattr(lexical, key) for key in identity_fields):
            raise EvaluationError(f"{label} pathname/descriptor identity differs")
        payload = b"".join(chunks)
        digest = _sha256_bytes(payload)
        observed = {
            "path": str(path),
            "sha256": digest,
            "size_bytes": int(after.st_size),
            "mode_octal": f"{stat.S_IMODE(after.st_mode):04o}",
            "nlink": int(after.st_nlink),
            "st_dev": int(after.st_dev),
            "st_ino": int(after.st_ino),
        }
        if digest != wanted_sha or len(payload) != observed["size_bytes"]:
            raise EvaluationError(f"{label} held-byte SHA/size differs")
        if expected_record is not None:
            _require_exact_json_equal(
                observed,
                {
                    key: expected_record[key]
                    for key in (
                        "path",
                        "sha256",
                        "size_bytes",
                        "mode_octal",
                        "nlink",
                        "st_dev",
                        "st_ino",
                    )
                },
                f"{label} materialization-bound identity",
            )
        return _json_from_bytes(payload, label), observed
    finally:
        os.close(descriptor)


def _live_directory_binding(
    path: Path, label: str, *, expected_mode_octal: str | None = None
) -> dict[str, Any]:
    path = _absolute_lexical(path)
    _reject_symlink_chain(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise EvaluationError(f"{label} is missing") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise EvaluationError(f"{label} is not a directory")
    mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
    if expected_mode_octal is not None and mode != expected_mode_octal:
        raise EvaluationError(f"{label} mode differs")
    return {
        "path": str(path),
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
        "mode_octal": mode,
    }


def _audit_package_build_attempt_closure(
    package: Mapping[str, Any],
    package_root: Path,
    audited_roles: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Require the PASS body plus its separate durable terminal marker."""

    body_record = audited_roles["package_build_attempt_body"]
    committed_record = audited_roles["package_build_attempt_committed"]
    expected_direct = {
        "build_attempt_body_path": body_record["path"],
        "build_attempt_body_sha256": body_record["sha256"],
        "build_attempt_committed_path": committed_record["path"],
        "build_attempt_committed_sha256": committed_record["sha256"],
    }
    _require_exact_json_equal(
        {key: package.get(key) for key in expected_direct},
        expected_direct,
        "MARS preflight/materialization package-build-attempt bindings",
    )
    body_path = Path(body_record["path"])
    committed_path = Path(committed_record["path"])
    attempt_root = body_path.parent
    if (
        body_path != attempt_root / PACKAGE_BUILD_ATTEMPT_BODY_NAME
        or committed_path != attempt_root / PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
    ):
        raise EvaluationError("package build-attempt paths are not canonical siblings")
    body, body_file = _read_bound_json_file(
        body_path,
        body_record["sha256"],
        "package build-attempt body",
        expected_record=body_record,
    )
    committed, committed_file = _read_bound_json_file(
        committed_path,
        committed_record["sha256"],
        "package build-attempt committed marker",
        expected_record=committed_record,
    )

    _exact_keys(
        body,
        {
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
        },
        "package build-attempt body",
    )
    _require_exact_json_equal(
        {
            "schema": body.get("schema"),
            "status": body.get("status"),
            "partial_output_preserved": body.get("partial_output_preserved"),
            "authorities": body.get("authorities"),
            "execution_authorized": body.get("execution_authorized"),
        },
        {
            "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            "partial_output_preserved": False,
            "authorities": PACKAGE_NO_AUTHORITY,
            "execution_authorized": False,
        },
        "package build-attempt body authority boundary",
    )
    started = _parse_utc(body.get("started_utc"), "package build-attempt start")
    completed = _parse_utc(body.get("completed_utc"), "package build-attempt completion")
    if completed < started:
        raise EvaluationError("package build-attempt completion predates its start")
    invocation = body.get("invocation")
    if type(invocation) is not dict:
        raise EvaluationError("package build-attempt invocation is not an object")
    _exact_keys(
        invocation,
        {
            "argv",
            "cwd",
            "output_dir",
            "failure_receipt_dir",
            "package_spec",
            "builder",
            "python",
            "runtime",
            "environment",
        },
        "package build-attempt invocation",
    )
    if (
        type(invocation.get("argv")) is not list
        or not invocation["argv"]
        or any(type(value) is not str for value in invocation["argv"])
        or invocation.get("output_dir") != str(package_root)
        or invocation.get("failure_receipt_dir") != str(attempt_root)
    ):
        raise EvaluationError("package build-attempt invocation paths/argv differ")
    for key in ("cwd", "package_spec", "builder", "python", "runtime", "environment"):
        if type(invocation.get(key)) is not dict:
            raise EvaluationError(f"package build-attempt invocation {key} is invalid")
    _exact_keys(
        invocation["cwd"],
        {"lexical", "resolved", "device", "inode"},
        "package build-attempt invocation cwd",
    )
    _exact_keys(
        invocation["package_spec"],
        {"path", "expected_sha256"},
        "package build-attempt invocation package spec",
    )
    _exact_keys(
        invocation["builder"],
        {"path", "expected_sha256"},
        "package build-attempt invocation builder",
    )
    _exact_keys(
        invocation["python"],
        {
            "implementation",
            "version",
            "version_info",
            "executable_lexical",
            "executable_resolved",
            "executable_sha256",
            "flags",
        },
        "package build-attempt invocation Python",
    )
    _exact_keys(
        invocation["runtime"],
        {"platform", "machine", "system", "release", "byteorder", "filesystem_encoding"},
        "package build-attempt invocation runtime",
    )
    _exact_keys(
        invocation["environment"],
        {
            "raw_values_recorded",
            "key_count",
            "keys",
            "keyset_sha256",
            "key_value_map_sha256",
        },
        "package build-attempt invocation environment",
    )
    if invocation["environment"].get("raw_values_recorded") is not False:
        raise EvaluationError("package build-attempt recorded raw environment values")
    environment_keys = invocation["environment"].get("keys")
    environment_count = _require_exact_int(
        invocation["environment"].get("key_count"),
        "package build-attempt environment key count",
        minimum=0,
    )
    if (
        type(environment_keys) is not list
        or any(type(key) is not str for key in environment_keys)
        or environment_keys != sorted(set(environment_keys))
        or len(environment_keys) != environment_count
    ):
        raise EvaluationError("package build-attempt environment key inventory differs")
    for container, key, label in (
        (invocation["package_spec"], "expected_sha256", "package spec"),
        (invocation["builder"], "expected_sha256", "builder"),
        (invocation["python"], "executable_sha256", "Python executable"),
        (invocation["environment"], "keyset_sha256", "environment keyset"),
        (invocation["environment"], "key_value_map_sha256", "environment map"),
    ):
        _require_sha_token(container.get(key), f"package build-attempt {label} SHA")

    package_summary = body.get("package")
    if type(package_summary) is not dict:
        raise EvaluationError("package build-attempt package summary is not an object")
    _exact_keys(
        package_summary,
        {
            "path",
            "manifest_sha256",
            "receipt_sha256",
            "independent_qa_required_sha256",
            "sha256sums_sha256",
            "package_commit_sha256",
            "file_count",
        },
        "package build-attempt package summary",
    )
    _require_exact_json_equal(
        {
            "path": package_summary.get("path"),
            "manifest_sha256": package_summary.get("manifest_sha256"),
            "receipt_sha256": package_summary.get("receipt_sha256"),
            "independent_qa_required_sha256": package_summary.get(
                "independent_qa_required_sha256"
            ),
            "sha256sums_sha256": package_summary.get("sha256sums_sha256"),
            "package_commit_sha256": package_summary.get("package_commit_sha256"),
        },
        {
            "path": str(package_root),
            "manifest_sha256": package.get("manifest_sha256"),
            "receipt_sha256": package.get("receipt_sha256"),
            "independent_qa_required_sha256": package.get(
                "independent_qa_required_sha256"
            ),
            "sha256sums_sha256": package.get("sha_index_sha256"),
            "package_commit_sha256": package.get("commit_sha256"),
        },
        "package build-attempt/preflight package summary",
    )
    _require_exact_int(
        package_summary.get("file_count"),
        "package build-attempt package file count",
        minimum=1,
    )
    observed_identity = body.get("observed_identity")
    if type(observed_identity) is not dict:
        raise EvaluationError("package build-attempt observed identity is invalid")
    _exact_keys(
        observed_identity,
        {
            "package_spec_sha256",
            "builder_sha256",
            "package_output_device",
            "package_output_inode",
        },
        "package build-attempt observed identity",
    )
    package_root_binding = _live_directory_binding(
        package_root, "package root in build-attempt", expected_mode_octal="0555"
    )
    _require_exact_json_equal(
        {
            "package_spec_sha256": observed_identity.get("package_spec_sha256"),
            "builder_sha256": observed_identity.get("builder_sha256"),
            "package_output_device": observed_identity.get("package_output_device"),
            "package_output_inode": observed_identity.get("package_output_inode"),
        },
        {
            "package_spec_sha256": invocation["package_spec"]["expected_sha256"],
            "builder_sha256": invocation["builder"]["expected_sha256"],
            "package_output_device": package_root_binding["st_dev"],
            "package_output_inode": package_root_binding["st_ino"],
        },
        "package build-attempt observed/live identity",
    )

    package_commit_path = package_root / "PACKAGE_COMMIT.json"
    package_commit, package_commit_file = _read_bound_json_file(
        package_commit_path,
        package.get("commit_sha256"),
        "MARS package commit",
    )
    _exact_keys(
        package_commit,
        {
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
        },
        "MARS package commit",
    )
    _require_exact_json_equal(
        {
            "schema": package_commit.get("schema"),
            "status": package_commit.get("status"),
            "package_version": package_commit.get("package_version"),
            "manifest": package_commit.get("manifest"),
            "receipt": package_commit.get("receipt"),
            "independent_qa_required": package_commit.get("independent_qa_required"),
            "sha256sums": package_commit.get("sha256sums"),
            "creation_order_contract": package_commit.get("creation_order_contract"),
            "authorities": package_commit.get("authorities"),
            "execution_authorized": package_commit.get("execution_authorized"),
        },
        {
            "schema": PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
            "package_version": PACKAGE_VERSION,
            "manifest": {
                "path": "MANIFEST.json",
                "sha256": package.get("manifest_sha256"),
            },
            "receipt": {
                "path": "RECEIPT.json",
                "sha256": package.get("receipt_sha256"),
            },
            "independent_qa_required": {
                "path": "INDEPENDENT_QA_REQUIRED.json",
                "sha256": package.get("independent_qa_required_sha256"),
            },
            "sha256sums": {
                "path": "SHA256SUMS.txt",
                "sha256": package.get("sha_index_sha256"),
            },
            "creation_order_contract": {
                "this_member_created_last": True,
                "post_commit_package_file_creation_permitted": False,
            },
            "authorities": PACKAGE_NO_AUTHORITY,
            "execution_authorized": False,
        },
        "MARS package commit boundary",
    )
    required_attempt = package_commit.get("required_external_pass_attempt")
    _require_exact_json_equal(
        required_attempt,
        {
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
        "MARS package commit required external PASS attempt",
    )

    _exact_keys(
        committed,
        {
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
        },
        "package build-attempt committed marker",
    )
    _require_exact_json_equal(
        {
            "schema": committed.get("schema"),
            "status": committed.get("status"),
            "body": committed.get("body"),
            "package_commit": committed.get("package_commit"),
            "publication": committed.get("publication"),
            "authorities": committed.get("authorities"),
            "execution_authorized": committed.get("execution_authorized"),
        },
        {
            "schema": PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
            "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
            "body": {
                "path": str(body_path),
                "sha256": body_record["sha256"],
                "schema": PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
                "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            },
            "package_commit": {
                "path": str(package_commit_path),
                "sha256": package_commit_file["sha256"],
                "schema": PACKAGE_COMMIT_SCHEMA,
                "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
            },
            "publication": PACKAGE_BUILD_ATTEMPT_PUBLICATION,
            "authorities": PACKAGE_NO_AUTHORITY,
            "execution_authorized": False,
        },
        "package build-attempt durable commit boundary",
    )
    committed_utc = _parse_utc(
        committed.get("committed_utc"), "package build-attempt durable commit"
    )
    if committed_utc < completed:
        raise EvaluationError("package build-attempt commit predates PASS body completion")
    attempt_root_binding = _live_directory_binding(
        attempt_root, "package build-attempt root", expected_mode_octal="0555"
    )
    attempt_parent_binding = _live_directory_binding(
        attempt_root.parent, "package build-attempt parent"
    )
    _require_exact_json_equal(
        committed.get("package_root"),
        package_root_binding,
        "package build-attempt committed package root",
    )
    _require_exact_json_equal(
        committed.get("attempt_root"),
        attempt_root_binding,
        "package build-attempt committed attempt root",
    )
    _require_exact_json_equal(
        committed.get("attempt_parent"),
        attempt_parent_binding,
        "package build-attempt committed attempt parent",
    )
    if set(os.listdir(attempt_root)) != {
        PACKAGE_BUILD_ATTEMPT_BODY_NAME,
        PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME,
    }:
        raise EvaluationError("package build-attempt terminal filename closure differs")
    return {
        "body": {"path": body_file["path"], "sha256": body_file["sha256"]},
        "committed": {
            "path": committed_file["path"],
            "sha256": committed_file["sha256"],
        },
        "package_commit": {
            "path": package_commit_file["path"],
            "sha256": package_commit_file["sha256"],
        },
        "attempt_root": attempt_root_binding,
        "package_root": package_root_binding,
    }


def _audit_materialization_gate_authority(
    outer: Mapping[str, Any],
    candidate: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
    sealed_runtime: Mapping[str, Any],
    audited_roles: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Independently reject pre-P1 candidate, GO, and COMPLETE authorities."""

    candidate_path = Path(str(candidate_manifest["path"]))
    candidate_root = candidate_path.parent
    candidate_index = _binding(
        outer.get("candidate_sha_index"),
        candidate_root,
        "materialization candidate SHA index",
    )
    if Path(candidate_index["path"]) != candidate_root / "SHA256SUMS.txt":
        raise EvaluationError("materialization candidate SHA-index path differs")
    complete_binding_raw = outer.get("complete")
    if type(complete_binding_raw) is not dict:
        raise EvaluationError("materialization COMPLETE binding is missing")
    _exact_keys(
        complete_binding_raw,
        {"path", "sha256"},
        "materialization COMPLETE binding",
    )
    complete_binding = _binding(
        complete_binding_raw,
        Path(str(complete_binding_raw.get("path", "."))).parent,
        "materialization COMPLETE receipt",
    )
    complete_path = Path(complete_binding["path"])
    if complete_path.name != "COMPLETE.json":
        raise EvaluationError("materialization COMPLETE path is not canonical")
    complete = _json(complete_path, "materialization COMPLETE receipt")
    _exact_keys(
        complete,
        {
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
        },
        "materialization COMPLETE receipt",
    )
    complete_boundary = {
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
        "next_legal_gate": (
            "FRESH_INDEPENDENT_QA_OF_MATERIALIZED_DATA_AND_TRAINING_CONTRACT"
        ),
    }
    _require_exact_json_equal(
        {key: complete.get(key) for key in complete_boundary},
        complete_boundary,
        "materialization COMPLETE authority boundary",
    )
    _parse_utc(complete.get("generated_utc"), "materialization COMPLETE timestamp")
    go_binding_raw = outer.get("materialization_go_authority")
    if type(go_binding_raw) is not dict:
        raise EvaluationError("materialization GO binding is missing")
    _exact_keys(go_binding_raw, {"path", "sha256"}, "materialization GO binding")
    go_binding = _binding(
        go_binding_raw,
        Path(str(go_binding_raw.get("path", "."))).parent,
        "materialization GO authority",
    )
    go_path = Path(go_binding["path"])
    if (
        go_path != complete_path.parent / "GO_AUTHORITY.json"
        or Path(candidate_index["path"]).parent != candidate_root
    ):
        raise EvaluationError("materialization GO/candidate authority paths differ")
    _require_exact_json_equal(
        {
            "candidate_manifest_sha256": complete.get("candidate_manifest_sha256"),
            "candidate_sha256sums_sha256": complete.get(
                "candidate_sha256sums_sha256"
            ),
            "go_sha256": complete.get("go_sha256"),
            "challenge_nonce": complete.get("challenge_nonce"),
            "candidate_manifest": complete.get("candidate_manifest"),
            "candidate_sha_index": complete.get("candidate_sha_index"),
            "materialization_go_authority": complete.get(
                "materialization_go_authority"
            ),
            "sealed_runtime": complete.get("sealed_runtime"),
        },
        {
            "candidate_manifest_sha256": candidate_manifest["sha256"],
            "candidate_sha256sums_sha256": candidate_index["sha256"],
            "go_sha256": go_binding["sha256"],
            "challenge_nonce": candidate.get("challenge_nonce"),
            "candidate_manifest": {
                "path": candidate_manifest["path"],
                "sha256": candidate_manifest["sha256"],
            },
            "candidate_sha_index": {
                "path": candidate_index["path"],
                "sha256": candidate_index["sha256"],
            },
            "materialization_go_authority": {
                "path": go_binding["path"],
                "sha256": go_binding["sha256"],
            },
            "sealed_runtime": sealed_runtime,
        },
        "materialization COMPLETE frozen authority bindings",
    )
    expected_artifact_sha = {
        role: audited_roles[role]["sha256"] for role in MATERIALIZATION_BOUND_ROLE_ORDER
    }
    _require_exact_json_equal(
        complete.get("frozen_closure_after_materialization"),
        {
            "candidate_manifest_sha256": candidate_manifest["sha256"],
            "candidate_sha256sums_sha256": candidate_index["sha256"],
            "artifact_sha256": expected_artifact_sha,
            "go_sha256": go_binding["sha256"],
            "held_snapshot_consumption": True,
            "path_reopen_for_consumed_inputs": False,
        },
        "materialization COMPLETE frozen closure",
    )
    output = complete.get("materialization_output")
    validation = complete.get("materialization_validation")
    if type(output) is not dict or set(output) != {
        "path",
        "sha256sums",
        "artifact_closure",
    }:
        raise EvaluationError("materialization COMPLETE output binding is invalid")
    if type(validation) is not dict or set(validation) != {
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
    }:
        raise EvaluationError("materialization COMPLETE validation keyset differs")
    output_index = output.get("sha256sums")
    if type(output_index) is not dict:
        raise EvaluationError("materialization COMPLETE output SHA-index is invalid")
    _exact_keys(
        output_index,
        {"path", "sha256"},
        "materialization COMPLETE output SHA-index",
    )
    _require_sha_token(
        output_index.get("sha256"), "materialization COMPLETE output-index SHA"
    )
    _require_exact_json_equal(
        {
            "status": validation.get("status"),
            "root": validation.get("root"),
            "artifact_closure": validation.get("artifact_closure"),
            "sha256sums_sha256": validation.get("sha256sums_sha256"),
            "training_authorized": validation.get("training_authorized"),
            "evaluation_authorized": validation.get("evaluation_authorized"),
            "fresh_emx_authorized": validation.get("fresh_emx_authorized"),
        },
        {
            "status": "PASS_MATERIALIZATION_DEEP_VALIDATED_RESULT_BLIND",
            "root": output.get("path"),
            "artifact_closure": outer.get("materialization_output_closure"),
            "sha256sums_sha256": output_index.get("sha256"),
            "training_authorized": False,
            "evaluation_authorized": False,
            "fresh_emx_authorized": False,
        },
        "materialization COMPLETE output/validation closure",
    )
    _require_exact_json_equal(
        output.get("artifact_closure"),
        outer.get("materialization_output_closure"),
        "materialization COMPLETE outer output closure",
    )

    go = _json(go_path, "materialization GO authority")
    _exact_keys(
        go,
        {
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
        },
        "materialization GO authority",
    )
    candidate_authorities = {
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
    go_authorities = dict(candidate_authorities)
    go_authorities["result_blind_data_materialization"] = True
    _require_exact_json_equal(
        {
            "schema": go.get("schema"),
            "status": go.get("status"),
            "scope": go.get("scope"),
            "challenge_nonce": go.get("challenge_nonce"),
            "authorities": go.get("authorities"),
        },
        {
            "schema": MATERIALIZATION_GO_SCHEMA,
            "status": "GO",
            "scope": "RESULT_BLIND_NESTED_10K_20K_MATERIALIZATION_ONLY",
            "challenge_nonce": candidate.get("challenge_nonce"),
            "authorities": go_authorities,
        },
        "materialization GO authority boundary",
    )
    issued = _parse_utc(go.get("issued_utc"), "materialization GO issued timestamp")
    expires = _parse_utc(go.get("expires_utc"), "materialization GO expiry timestamp")
    if expires <= issued:
        raise EvaluationError("materialization GO expiry is not after issue time")
    reviewer = go.get("reviewer")
    findings = go.get("findings")
    if type(reviewer) is not dict:
        raise EvaluationError("materialization GO reviewer is invalid")
    _exact_keys(
        reviewer,
        {
            "reviewer_id",
            "independent",
            "result_blind",
            "reviewed_without_numerical_results",
        },
        "materialization GO reviewer",
    )
    if (
        type(reviewer.get("reviewer_id")) is not str
        or not reviewer["reviewer_id"].strip()
    ):
        raise EvaluationError("materialization GO reviewer ID is invalid")
    _require_exact_json_equal(
        {
            "independent": reviewer.get("independent"),
            "result_blind": reviewer.get("result_blind"),
            "reviewed_without_numerical_results": reviewer.get(
                "reviewed_without_numerical_results"
            ),
        },
        {
            "independent": True,
            "result_blind": True,
            "reviewed_without_numerical_results": True,
        },
        "materialization GO reviewer boundary",
    )
    if type(findings) is not dict:
        raise EvaluationError("materialization GO findings are invalid")
    _exact_keys(findings, {"p0", "p1", "p2", "p3"}, "materialization GO findings")
    for key in ("p0", "p1", "p2", "p3"):
        _require_exact_int(findings.get(key), f"materialization GO {key}", minimum=0)
    if findings["p0"] != 0 or findings["p1"] != 0:
        raise EvaluationError("materialization GO has unresolved P0/P1 findings")
    go_bindings = go.get("bindings")
    if type(go_bindings) is not dict:
        raise EvaluationError("materialization GO bindings are invalid")
    expected_go_bindings = {
        "candidate_manifest_sha256": candidate_manifest["sha256"],
        "candidate_sha256sums_sha256": candidate_index["sha256"],
        "challenge_nonce": candidate.get("challenge_nonce"),
        "artifact_sha256": expected_artifact_sha,
        "materialization_out_dir": (candidate.get("future_paths") or {}).get(
            "materialization_out_dir"
        ),
        "execution_receipt_dir": (candidate.get("future_paths") or {}).get(
            "execution_receipt_dir"
        ),
        "runtime_identity_sha256": (candidate.get("runtime_identity") or {}).get(
            "identity_sha256"
        ),
        "host_identity_sha256": (candidate.get("host_identity") or {}).get(
            "identity_sha256"
        ),
        "materialization_contract_sha256": candidate.get(
            "materialization_contract_sha256"
        ),
        "sealed_runtime": sealed_runtime,
    }
    _require_exact_json_equal(
        go_bindings,
        expected_go_bindings,
        "materialization GO exact21 bindings",
    )
    if (
        output.get("path") != expected_go_bindings["materialization_out_dir"]
        or str(complete_path.parent)
        != expected_go_bindings["execution_receipt_dir"]
    ):
        raise EvaluationError("materialization GO/output/receipt paths differ")
    return {
        "complete": _binding_core(complete_binding),
        "candidate_sha_index": _binding_core(candidate_index),
        "go": _binding_core(go_binding),
        "schemas": {
            "candidate": MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA,
            "go": MATERIALIZATION_GO_SCHEMA,
            "complete": MATERIALIZATION_COMPLETE_SCHEMA,
        },
    }


def _preflight_record_identity(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    return {
        "st_dev": int(metadata.st_dev),
        "st_ino": int(metadata.st_ino),
        "st_uid": int(metadata.st_uid),
        "st_gid": int(metadata.st_gid),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "nlink": int(metadata.st_nlink),
        "size_bytes": int(metadata.st_size),
    }


def _audit_preflight_record(
    raw: Any,
    candidate: Mapping[str, Any],
    expected_record_path: str,
    label: str,
    *,
    schema: str | None = None,
    state: str | None = None,
) -> None:
    expected_keys = {"path", "sha256", "identity"}
    if schema is not None:
        expected_keys |= {"schema", "state"}
    if type(raw) is not dict:
        raise EvaluationError(f"{label} is not an object")
    _exact_keys(raw, expected_keys, label)
    path = Path(str(candidate["path"]))
    expected = {
        "path": expected_record_path,
        "sha256": candidate["sha256"],
        "identity": _preflight_record_identity(path),
    }
    if schema is not None:
        expected.update({"schema": schema, "state": state})
    _require_exact_json_equal(raw, expected, label)


def _audit_singleton_transmission_closure(
    material: Mapping[str, Any],
    paired_runtime: Mapping[str, Any],
    held_singleton: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild terminal->materialization->preflight->package singleton proof."""

    outer = material.get("outer_materialization_authority")
    if type(outer) is not dict:
        raise EvaluationError("paired materialization outer authority is missing")
    _exact_keys(
        outer,
        {
            "complete",
            "candidate_manifest",
            "candidate_sha_index",
            "materialization_go_authority",
            "candidate_bindings",
            "sealed_runtime",
            "materialization_output_closure",
        },
        "paired materialization outer authority",
    )
    reduced = outer.get("candidate_bindings")
    if type(reduced) is not dict or set(reduced) != set(MATERIALIZATION_BOUND_ROLE_ORDER):
        raise EvaluationError("paired materialization reduced role closure is not exact21")
    for role in MATERIALIZATION_BOUND_ROLE_ORDER:
        if type(reduced[role]) is not dict:
            raise EvaluationError(f"paired materialization reduced role is invalid: {role}")
        _exact_keys(
            reduced[role],
            {"path", "sha256"},
            f"paired materialization reduced role {role}",
        )

    manifest_binding_raw = outer.get("candidate_manifest")
    if type(manifest_binding_raw) is not dict:
        raise EvaluationError("materialization candidate manifest binding is invalid")
    _exact_keys(
        manifest_binding_raw,
        {"path", "sha256"},
        "materialization candidate manifest binding",
    )
    manifest_binding = _binding(
        manifest_binding_raw,
        Path(str(manifest_binding_raw.get("path", "."))).parent,
        "materialization candidate manifest",
    )
    candidate = _json(
        Path(manifest_binding["path"]), "materialization candidate manifest"
    )
    _exact_keys(
        candidate,
        {
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
        },
        "materialization candidate manifest",
    )
    _require_exact_json_equal(
        {
            "schema": candidate.get("schema"),
            "status": candidate.get("status"),
            "result_blind": candidate.get("result_blind"),
            "bound_role_order": candidate.get("bound_role_order"),
            "next_legal_gate": candidate.get("next_legal_gate"),
        },
        {
            "schema": MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA,
            "status": "PREPARED_IMMUTABLE_RESULT_BLIND_NO_AUTHORITY",
            "result_blind": True,
            "bound_role_order": list(MATERIALIZATION_BOUND_ROLE_ORDER),
            "next_legal_gate": MATERIALIZATION_GO_SCHEMA,
        },
        "materialization candidate manifest boundary",
    )
    full_bindings = candidate.get("bindings")
    if type(full_bindings) is not dict or set(full_bindings) != set(
        MATERIALIZATION_BOUND_ROLE_ORDER
    ):
        raise EvaluationError("materialization candidate full role closure is not exact21")
    audited_roles = {
        role: _audit_materialization_candidate_record(
            full_bindings[role], role, reduced[role]
        )
        for role in MATERIALIZATION_BOUND_ROLE_ORDER
    }

    closure = paired_runtime.get("descriptor_closure")
    if type(closure) is not dict:
        raise EvaluationError("paired descriptor closure is missing from singleton proof")
    expected_sealed_runtime = {
        "expected_runtime_closure_json_sha256": closure["manifest"]["sha256"],
        "attestation": {
            "schema": runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
            "entrypoint": "materialization",
            "manifest_sha256": closure["manifest"]["sha256"],
            "pure_archive_sha256": closure["pure_archive"]["sha256"],
            "bootstrap_sha256": closure["bootstrap"]["sha256"],
        },
        "runtime_manifest_role_identity": outer.get("sealed_runtime", {}).get(
            "runtime_manifest_role_identity"
        ),
        "runtime_tree_role_identity": outer.get("sealed_runtime", {}).get(
            "runtime_tree_role_identity"
        ),
        "required_external_entrypoint": "materialization",
        "raw_runtime_fallback_authorized": False,
    }
    sealed_runtime = outer.get("sealed_runtime")
    if type(sealed_runtime) is not dict:
        raise EvaluationError("paired materialization sealed runtime is missing")
    _exact_keys(
        sealed_runtime,
        {
            "expected_runtime_closure_json_sha256",
            "attestation",
            "runtime_manifest_role_identity",
            "runtime_tree_role_identity",
            "required_external_entrypoint",
            "raw_runtime_fallback_authorized",
        },
        "paired materialization sealed runtime",
    )
    _require_exact_json_equal(
        sealed_runtime, expected_sealed_runtime, "paired materialization sealed runtime"
    )
    _require_exact_json_equal(
        candidate.get("sealed_runtime"),
        sealed_runtime,
        "candidate/paired materialization sealed runtime",
    )
    materialization_gate = _audit_materialization_gate_authority(
        outer,
        candidate,
        manifest_binding,
        sealed_runtime,
        audited_roles,
    )

    preflight_root = Path(audited_roles["mars_preflight_committed"]["path"]).parent
    for role, filename in MARS_PREFLIGHT_ROLE_FILENAMES.items():
        if Path(audited_roles[role]["path"]) != preflight_root / filename:
            raise EvaluationError(f"MARS preflight role canonical path differs: {role}")
    committed = _json(
        Path(audited_roles["mars_preflight_committed"]["path"]),
        "MARS preflight committed marker",
    )
    _exact_keys(
        committed,
        {
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
        },
        "MARS preflight committed marker",
    )
    _require_exact_json_equal(
        {
            key: committed.get(key)
            for key in (
                "schema",
                "status",
                "preflight_pass",
                "exact_root_filenames",
                "failure_marker_absent_at_commit",
                "failure_marker_has_absolute_precedence",
                "body_is_not_authority",
                "next_legal_action",
            )
        },
        {
            "schema": MARS_PREFLIGHT_COMMITTED_SCHEMA,
            "status": "COMMITTED_PASS_PREFLIGHT_ONLY",
            "preflight_pass": True,
            "exact_root_filenames": list(MARS_PREFLIGHT_SUCCESS_FILES),
            "failure_marker_absent_at_commit": True,
            "failure_marker_has_absolute_precedence": True,
            "body_is_not_authority": True,
            "next_legal_action": (
                "SEPARATE_RESULT_BLIND_MATERIALIZATION_RECEIPT_AND_EXACT_"
                "AUTHORIZATION_REQUIRED"
            ),
        },
        "MARS preflight committed boundary",
    )
    preflight_root_metadata = preflight_root.lstat()
    preflight_parent_metadata = preflight_root.parent.lstat()
    committed_root_identity = {
        "st_dev": int(preflight_root_metadata.st_dev),
        "st_ino": int(preflight_root_metadata.st_ino),
        "st_uid": int(preflight_root_metadata.st_uid),
        "st_gid": int(preflight_root_metadata.st_gid),
        "mode_octal": f"{stat.S_IMODE(preflight_root_metadata.st_mode):04o}",
    }
    prepared_root_identity = dict(committed_root_identity)
    prepared_root_identity["mode_octal"] = "0700"
    parent_identity = {
        "st_dev": int(preflight_parent_metadata.st_dev),
        "st_ino": int(preflight_parent_metadata.st_ino),
        "st_uid": int(preflight_parent_metadata.st_uid),
        "st_gid": int(preflight_parent_metadata.st_gid),
        "mode_octal": f"{stat.S_IMODE(preflight_parent_metadata.st_mode):04o}",
    }
    _require_exact_json_equal(
        committed.get("receipt_root"),
        {
            "path": str(preflight_root),
            "prepared_identity": prepared_root_identity,
            "committed_identity": committed_root_identity,
        },
        "MARS preflight committed receipt-root identity",
    )
    _require_exact_json_equal(
        committed.get("receipt_parent"),
        {"path": str(preflight_root.parent), "identity": parent_identity},
        "MARS preflight committed receipt-parent identity",
    )
    if set(os.listdir(preflight_root)) != set(MARS_PREFLIGHT_SUCCESS_FILES):
        raise EvaluationError("MARS preflight live terminal filename closure differs")
    preflight_authorities = {
        "direct_data_materialization_authorized": False,
        "training_authorized": False,
        "common_test_access_authorized": False,
        "numerical_metric_access_authorized": False,
        "fresh_emx_authorized": False,
        "process_signal_authorized": False,
    }
    _require_exact_json_equal(
        committed.get("authorities"),
        preflight_authorities,
        "MARS preflight committed authorities",
    )
    prepared_records = committed.get("prepared_artifacts")
    if type(prepared_records) is not dict or set(prepared_records) != {
        "prepared_receipt",
        "execution_qa_required",
        "prepare_sha256sums",
    }:
        raise EvaluationError("MARS preflight prepared artifact binding set differs")
    committed_records = {
        "mars_preflight_prepared": prepared_records["prepared_receipt"],
        "mars_preflight_execution_qa_required": prepared_records[
            "execution_qa_required"
        ],
        "mars_preflight_prepare_sha_index": prepared_records["prepare_sha256sums"],
        "mars_preflight_receipt_body": committed.get("receipt_body"),
        "mars_preflight_sha_index": committed.get("sha256_index"),
    }
    for role, record in committed_records.items():
        _audit_preflight_record(
            record,
            audited_roles[role],
            MARS_PREFLIGHT_ROLE_FILENAMES[role],
            f"MARS committed {role}",
        )
    _audit_preflight_record(
        committed.get("consumed_external_one_use_lease"),
        audited_roles["mars_preflight_consumed_lease"],
        audited_roles["mars_preflight_consumed_lease"]["path"],
        "MARS committed consumed lease",
        schema=MARS_PREFLIGHT_LEASE_SCHEMA,
        state="CONSUMED",
    )

    body = _json(
        Path(audited_roles["mars_preflight_receipt_body"]["path"]),
        "MARS preflight receipt body",
    )
    body_keys = {
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
    }
    _exact_keys(body, body_keys, "MARS preflight receipt body")
    _require_exact_json_equal(
        {
            "schema": body.get("schema"),
            "status": body.get("status"),
            "preflight_pass": body.get("preflight_pass"),
            "committed_terminal_marker_required": body.get(
                "committed_terminal_marker_required"
            ),
            "next_legal_action": body.get("next_legal_action"),
        },
        {
            "schema": MARS_PREFLIGHT_BODY_SCHEMA,
            "status": "PASS_BODY_AWAITING_DURABLE_COMMIT",
            "preflight_pass": False,
            "committed_terminal_marker_required": "PREFLIGHT_COMMITTED.json",
            "next_legal_action": "NO_ACTION_UNTIL_DURABLE_COMMITTED_MARKER_IS_VERIFIED",
        },
        "MARS preflight body authority boundary",
    )
    _require_exact_json_equal(
        body.get("authorities"),
        preflight_authorities,
        "MARS preflight body authorities",
    )
    expected_body_checks = {
        "package_exact_regular_file_closure",
        "external_code_go_exact",
        "external_code_go_fresh",
        "external_code_go_single_use_receipt_dir_bound",
        "frozen_source_identities_exact",
        "frozen_preregistration_identities_exact",
        "host_uid_boot_id_exact",
        "python_3_12_13_exact",
        "numpy_2_5_0_exact",
        "descriptor_sealed_numpy_and_runtime_exact",
        "native_compile_and_import_pass",
        "candidate_outputs_absent",
        "current_uid_exact_controlled_entrypoint_count_zero",
        "no_training_builder_runner_or_trainer_spawned",
        "no_process_signals_sent",
        "no_training_test_metrics_or_fresh_emx_access",
    }
    checks = body.get("checks")
    if type(checks) is not dict or set(checks) != expected_body_checks:
        raise EvaluationError("MARS preflight body check keyset differs")
    _all_true(checks, "MARS preflight body checks")
    _require_exact_json_equal(
        committed.get("process_singleton"),
        body.get("process_singleton"),
        "MARS body/commit process-singleton closure",
    )
    singleton = body.get("process_singleton")
    if type(singleton) is not dict:
        raise EvaluationError("MARS preflight process-singleton proof is missing")
    _exact_keys(
        singleton,
        {
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
        },
        "MARS preflight process-singleton proof",
    )
    _require_exact_json_equal(
        {
            key: singleton.get(key)
            for key in (
                "lock_operation",
                "lock_held_for_full_execute_lifetime",
                "all_counts_zero",
                "current_uid_only",
            )
        },
        {
            "lock_operation": "LOCK_EX|LOCK_NB",
            "lock_held_for_full_execute_lifetime": True,
            "all_counts_zero": True,
            "current_uid_only": True,
        },
        "MARS preflight process-singleton lifetime proof",
    )
    for phase in ("before", "after"):
        process_audit = singleton.get(phase)
        if type(process_audit) is not dict:
            raise EvaluationError(f"MARS preflight {phase} process audit is missing")
        _exact_keys(
            process_audit,
            {
                "schema",
                "uid",
                "current_pid",
                "substring_matching_used",
                "exact_argv_executable_and_descriptor_identity_required",
                "matches",
                "match_count",
            },
            f"MARS preflight {phase} process audit",
        )
        _require_exact_json_equal(
            {
                "schema": process_audit.get("schema"),
                "substring_matching_used": process_audit.get(
                    "substring_matching_used"
                ),
                "exact_argv_executable_and_descriptor_identity_required": (
                    process_audit.get(
                        "exact_argv_executable_and_descriptor_identity_required"
                    )
                ),
                "matches": process_audit.get("matches"),
                "match_count": process_audit.get("match_count"),
            },
            {
                "schema": "controlled_real10k_20k_preflight_process_audit_v2",
                "substring_matching_used": False,
                "exact_argv_executable_and_descriptor_identity_required": True,
                "matches": [],
                "match_count": 0,
            },
            f"MARS preflight {phase} zero-process audit",
        )
        _require_exact_int(
            process_audit.get("uid"), f"MARS preflight {phase} audit UID", minimum=0
        )
        _require_exact_int(
            process_audit.get("current_pid"),
            f"MARS preflight {phase} audit PID",
            minimum=1,
        )
    if (
        singleton["before"]["uid"] != singleton["after"]["uid"]
        or singleton["before"]["current_pid"]
        != singleton["after"]["current_pid"]
    ):
        raise EvaluationError("MARS preflight process-audit identity changed")
    _audit_preflight_record(
        singleton.get("contract"),
        audited_roles["package_process_singleton_contract"],
        audited_roles["package_process_singleton_contract"]["path"],
        "MARS singleton contract binding",
    )
    _audit_preflight_record(
        singleton.get("lock"),
        audited_roles["package_singleton_lock"],
        audited_roles["package_singleton_lock"]["path"],
        "MARS singleton lock binding",
    )
    contract_payload = _json(
        Path(audited_roles["package_process_singleton_contract"]["path"]),
        "package process-singleton contract",
    )
    audited_contract = _audit_process_singleton_contract_payload(contract_payload)
    _require_exact_json_equal(
        singleton.get("contract_payload"),
        audited_contract,
        "MARS preflight/live process-singleton contract payload",
    )
    _require_exact_json_equal(
        singleton.get("protected_entrypoints"),
        audited_contract["protected_entrypoints"],
        "MARS preflight protected-entrypoint contract",
    )
    _require_exact_json_equal(
        singleton.get("proc_audit_contract"),
        audited_contract["proc_audit"],
        "MARS preflight process-audit contract",
    )

    package = body.get("package")
    if type(package) is not dict:
        raise EvaluationError("MARS preflight package proof is missing")
    _exact_keys(
        package,
        {
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
        },
        "MARS preflight package proof",
    )
    package_root = Path(str(package.get("root", "")))
    build_attempt = _audit_package_build_attempt_closure(
        package, package_root, audited_roles
    )
    package_manifest_path = _file(package_root / "MANIFEST.json", "MARS package manifest")
    if _sha256(package_manifest_path) != _require_sha_token(
        package.get("manifest_sha256"), "MARS package manifest SHA"
    ):
        raise EvaluationError("MARS preflight package-manifest SHA closure differs")
    package_manifest = _json(package_manifest_path, "MARS package manifest")
    _exact_keys(
        package_manifest,
        {
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
        },
        "MARS package manifest",
    )
    _require_exact_json_equal(
        {
            "schema": package_manifest.get("schema"),
            "package_version": package_manifest.get("package_version"),
            "execution_authorized": package_manifest.get("execution_authorized"),
            "result_accessed": package_manifest.get("result_accessed"),
            "numerical_metrics_accessed": package_manifest.get(
                "numerical_metrics_accessed"
            ),
        },
        {
            "schema": "controlled_real10k_20k_mars_package_v2",
            "package_version": PACKAGE_VERSION,
            "execution_authorized": False,
            "result_accessed": False,
            "numerical_metrics_accessed": False,
        },
        "MARS package manifest authority boundary",
    )
    _require_exact_json_equal(
        package.get("role_identity"),
        package_manifest.get("role_identity"),
        "preflight/package-manifest role identity",
    )
    role_destinations = package_manifest.get("role_destinations")
    role_identity = package_manifest.get("role_identity")
    if type(role_destinations) is not dict or type(role_identity) is not dict:
        raise EvaluationError("MARS package role identity/destinations are missing")
    contract_role = role_identity.get("process_singleton_contract_json")
    if type(contract_role) is not dict:
        raise EvaluationError("MARS package singleton-contract role is missing")
    expected_contract_path = package_root / str(
        role_destinations.get("process_singleton_contract_json", "")
    )
    if (
        expected_contract_path
        != Path(audited_roles["package_process_singleton_contract"]["path"])
        or contract_role.get("path")
        != role_destinations.get("process_singleton_contract_json")
        or contract_role.get("sha256")
        != audited_roles["package_process_singleton_contract"]["sha256"]
    ):
        raise EvaluationError("MARS package singleton-contract role closure differs")
    runtime = package_manifest.get("runtime")
    if type(runtime) is not dict:
        raise EvaluationError("MARS package runtime declaration is missing")
    _exact_keys(
        runtime,
        {"entrypoints", "import_graph", "dependency_closure", "process_singleton_contract"},
        "MARS package runtime declaration",
    )
    declaration = runtime.get("process_singleton_contract")
    _require_exact_json_equal(
        declaration,
        {
            "schema": PROCESS_SINGLETON_CONTRACT_SCHEMA,
            "path": role_destinations["process_singleton_contract_json"],
            "sha256": audited_roles["package_process_singleton_contract"]["sha256"],
            "lock_path": "CONTROLLED_SINGLETON.lock",
            "lock_sha256": audited_roles["package_singleton_lock"]["sha256"],
            "protected_entrypoints": audited_contract["protected_entrypoints"],
        },
        "MARS package singleton declaration",
    )
    _require_exact_json_equal(
        sealed_runtime["runtime_manifest_role_identity"],
        role_identity.get("runtime_dependency_closure_json"),
        "materialization/package runtime-manifest role identity",
    )
    _require_exact_json_equal(
        sealed_runtime["runtime_tree_role_identity"],
        role_identity.get("runtime_dependency_closure_tree"),
        "materialization/package runtime-tree role identity",
    )
    for protected in audited_contract["protected_entrypoints"]:
        if role_destinations.get(protected["role"]) != protected["path"]:
            raise EvaluationError(
                "MARS package protected-entrypoint destination differs from contract"
            )

    flat_lock = _validate_controlled_singleton_identity(
        held_singleton, "evaluator held controlled singleton"
    )
    lock_record = audited_roles["package_singleton_lock"]
    _require_exact_json_equal(
        {
            "path": lock_record["path"],
            "sha256": lock_record["sha256"],
            "size_bytes": lock_record["size_bytes"],
            "device": lock_record["st_dev"],
            "inode": lock_record["st_ino"],
            "nlink": lock_record["nlink"],
            "lock_mode": CONTROLLED_SINGLETON_LOCK_MODE,
            "schema": CONTROLLED_SINGLETON_SCHEMA,
        },
        flat_lock,
        "package/evaluator flat singleton lock identity",
    )
    if lock_record["mode_octal"] != audited_contract["lock"]["required_mode_octal"]:
        raise EvaluationError("package singleton lock mode differs from full contract")
    if lock_record["nlink"] != audited_contract["lock"]["required_nlink"]:
        raise EvaluationError("package singleton lock nlink differs from full contract")
    if lock_record["sha256"] != audited_contract["lock"]["sha256"]:
        raise EvaluationError("package singleton lock SHA differs from full contract")
    if Path(lock_record["path"]) != package_root / declaration["lock_path"]:
        raise EvaluationError("package singleton lock path differs from package declaration")

    lease_payload = _json(
        Path(audited_roles["mars_preflight_consumed_lease"]["path"]),
        "MARS consumed preflight lease",
    )
    _exact_keys(
        lease_payload,
        {
            "schema",
            "state",
            "challenge_nonce",
            "receipt_root",
            "created_utc",
            "consumed_utc",
            "single_use",
            "retry_authorized",
            "authorities",
        },
        "MARS consumed preflight lease",
    )
    _require_exact_json_equal(
        {
            "schema": lease_payload.get("schema"),
            "state": lease_payload.get("state"),
            "single_use": lease_payload.get("single_use"),
            "retry_authorized": lease_payload.get("retry_authorized"),
        },
        {
            "schema": MARS_PREFLIGHT_LEASE_SCHEMA,
            "state": "CONSUMED",
            "single_use": True,
            "retry_authorized": False,
        },
        "MARS consumed preflight lease state",
    )
    _require_exact_json_equal(
        lease_payload.get("authorities"),
        preflight_authorities,
        "MARS consumed preflight lease authorities",
    )
    if (lease_payload.get("receipt_root") or {}).get("path") != str(preflight_root):
        raise EvaluationError("MARS consumed lease receipt-root binding differs")
    receipt_transaction = body.get("receipt_transaction")
    if type(receipt_transaction) is not dict or set(receipt_transaction) != {
        "prepared_binding",
        "consumed_external_one_use_lease",
    }:
        raise EvaluationError("MARS preflight body receipt-transaction keyset differs")
    _require_exact_json_equal(
        receipt_transaction.get("consumed_external_one_use_lease"),
        committed.get("consumed_external_one_use_lease"),
        "MARS body/commit consumed lease binding",
    )
    return {
        "candidate_manifest": _binding_core(manifest_binding),
        "materialization_gate": materialization_gate,
        "preflight_committed": {
            "path": audited_roles["mars_preflight_committed"]["path"],
            "sha256": audited_roles["mars_preflight_committed"]["sha256"],
        },
        "preflight_consumed_lease": {
            "path": audited_roles["mars_preflight_consumed_lease"]["path"],
            "sha256": audited_roles["mars_preflight_consumed_lease"]["sha256"],
        },
        "package_manifest": {
            "path": str(package_manifest_path),
            "sha256": package["manifest_sha256"],
        },
        "package_build_attempt": build_attempt,
        "process_singleton_contract": {
            "path": audited_roles["package_process_singleton_contract"]["path"],
            "sha256": audited_roles["package_process_singleton_contract"]["sha256"],
        },
        "flat_singleton": flat_lock,
        "exact_bound_role_order": list(MATERIALIZATION_BOUND_ROLE_ORDER),
    }


def _audit_terminal_manifest(
    path: Path,
    *,
    material_path: Path,
    material_sha: str,
    holdout_path: Path,
    holdout_sha: str,
    normalization_path: Path,
    normalization_sha: str,
    trainer_path: Path,
    trainer_sha: str,
    shared_contract_path: Path,
    shared_contract_sha: str,
    evaluator_runtime: Mapping[str, Any],
    evaluator_controlled_singleton: Mapping[str, Any] | None,
    normalization: Mapping[str, np.ndarray],
    fixture_mode: bool,
) -> dict[str, Any]:
    payload = _json(path, "six-arm controller terminal receipt")
    terminal_keys = {
        "schema",
        "status",
        "run_contract",
        "pairs",
        "final_artifact_manifest",
        "exact_paired_seeds",
        "evaluation_mode",
        "test_access_event_count",
        "one_time_common_test_evaluation_performed",
        "fresh_emx_accessed",
        "numerical_metrics_released",
        "next_legal_gate",
    }
    if not fixture_mode:
        terminal_keys |= {"runtime_dependency_closure", "controlled_singleton"}
    _exact_keys(
        payload,
        terminal_keys,
        "six-arm controller terminal receipt",
    )
    expected_scalars = {
        "schema": (
            LEGACY_FIXTURE_SIX_ARM_TERMINAL_SCHEMA
            if fixture_mode
            else SIX_ARM_TERMINAL_SCHEMA
        ),
        "status": "READY_FOR_ONE_TIME_COMMON_TEST_EVALUATION",
        "exact_paired_seeds": list(EXACT_PAIRED_SEEDS),
        "evaluation_mode": "validation_only",
        "test_access_event_count": 0,
        "one_time_common_test_evaluation_performed": False,
        "fresh_emx_accessed": False,
        "numerical_metrics_released": False,
        "next_legal_gate": "INDEPENDENT_QA_FOR_ONE_TIME_COMMON_TEST_EVALUATOR",
    }
    if any(key not in payload for key in expected_scalars):
        raise EvaluationError("six-arm controller terminal status/test-seal is incomplete")
    _require_exact_json_equal(
        {key: payload[key] for key in expected_scalars},
        expected_scalars,
        "six-arm controller terminal status/test seal",
    )
    controller_root = path.parent.parent
    if path != controller_root / "receipts" / "COMPLETE_RECEIPT.json":
        raise EvaluationError("six-arm controller terminal path is not canonical")
    run_contract_binding = _binding(
        payload.get("run_contract"), path.parent, "paired run contract"
    )
    if Path(run_contract_binding["path"]) != controller_root / "run_contract.json":
        raise EvaluationError("paired run-contract path mismatch")
    run_contract = _json(Path(run_contract_binding["path"]), "paired run contract")
    expected_run_schema = (
        LEGACY_FIXTURE_PAIRED_RUN_CONTRACT_SCHEMA
        if fixture_mode
        else PAIRED_RUN_CONTRACT_SCHEMA
    )
    if not fixture_mode:
        _exact_keys(
            run_contract,
            {
                "schema",
                "out_dir",
                "runner",
                "shared_contract",
                "trainer",
                "runtime",
                "controlled_singleton",
                "production_hard_identities",
                "materialization",
                "paired_seeds",
                "arm_order_within_seed",
                "process_contract",
                "training_contract",
                "release_boundary",
                "qa_challenge_nonce",
            },
            "paired production run contract",
        )
    if (
        run_contract.get("schema") != expected_run_schema
        or run_contract.get("out_dir") != str(controller_root)
        or run_contract.get("paired_seeds") != list(EXACT_PAIRED_SEEDS)
        or run_contract.get("arm_order_within_seed") != ["small", "large"]
    ):
        raise EvaluationError("paired run-contract identity/order mismatch")
    runner = _binding(
        run_contract.get("runner"), Path(run_contract_binding["path"]).parent, "paired runner"
    )
    if not fixture_mode and runner["sha256"] != FROZEN_PAIRED_RUNNER_SHA256:
        raise EvaluationError("paired terminal was not produced by the frozen runner SHA")
    trainer_launch_contract = _audit_trainer_launch_contract(
        (run_contract.get("process_contract") or {}).get("trainer_launch"),
        "paired run-contract trainer launch",
    )
    paired_runtime: dict[str, Any] | None = None
    paired_singleton: dict[str, Any] | None = None
    if fixture_mode:
        paired_shared = _binding(
            run_contract.get("shared_contract"),
            Path(run_contract_binding["path"]).parent,
            "paired fixture shared scientific contract",
        )
        if (
            Path(paired_shared["path"]) != shared_contract_path
            or paired_shared["sha256"] != shared_contract_sha
        ):
            raise EvaluationError(
                "paired fixture run-contract shared binding differs from actual imported bytes"
            )
        paired_shared_evidence: dict[str, Any] = _binding_core(paired_shared)
    else:
        if evaluator_controlled_singleton is None:
            raise EvaluationError("production evaluator controlled singleton is missing")
        paired_runtime = _audit_paired_runtime_identity(
            run_contract.get("runtime"), evaluator_runtime
        )
        paired_singleton = _audit_paired_controlled_singleton(
            run_contract.get("controlled_singleton"),
            evaluator_controlled_singleton,
        )
        paired_shared_evidence = _audit_paired_shared_member(
            run_contract.get("shared_contract"),
            evaluator_runtime,
            shared_contract_sha,
        )
        _require_exact_json_equal(
            payload.get("runtime_dependency_closure"),
            paired_runtime["descriptor_closure"],
            "controller terminal runtime closure",
        )
        _require_exact_json_equal(
            payload.get("controlled_singleton"),
            paired_singleton,
            "controller terminal controlled singleton",
        )
    trainer = _binding(
        run_contract.get("trainer"), Path(run_contract_binding["path"]).parent, "paired trainer"
    )
    if Path(trainer["path"]) != trainer_path or trainer["sha256"] != trainer_sha:
        raise EvaluationError("paired run-contract trainer binding mismatch")
    if not fixture_mode:
        assert paired_runtime is not None
        sealed_trainer = (
            paired_runtime["descriptor_closure"].get("role_bindings") or {}
        ).get("trainer_code")
        if type(sealed_trainer) is not dict or trainer["sha256"] != sealed_trainer.get(
            "sha256"
        ):
            raise EvaluationError(
                "paired trainer display-path bytes differ from sealed trainer member"
            )
    material = run_contract.get("materialization")
    if not isinstance(material, dict):
        raise EvaluationError("paired run contract lacks materialization closure")
    singleton_transmission: dict[str, Any] | None = None
    if not fixture_mode:
        _exact_keys(
            material,
            {
                "summary",
                "receipt",
                "independent_qa_required",
                "sha_index",
                "artifacts",
                "historical_model_summary",
                "shared_contract",
                "outer_materialization_authority",
                "counts",
                "audits",
                "material_gate_consumption",
            },
            "paired production materialization closure",
        )
        if paired_runtime is None or paired_singleton is None:
            raise EvaluationError("paired production singleton/runtime closure is missing")
        singleton_transmission = _audit_singleton_transmission_closure(
            material,
            paired_runtime,
            paired_singleton,
        )
    material_summary = _binding(
        material.get("summary"), Path(run_contract_binding["path"]).parent, "paired material summary"
    )
    if Path(material_summary["path"]) != material_path or material_summary["sha256"] != material_sha:
        raise EvaluationError("paired run-contract materialization-summary mismatch")
    material_artifacts = material.get("artifacts")
    if not isinstance(material_artifacts, dict):
        raise EvaluationError("paired run contract lacks materialized artifacts")
    for key, wanted_path, wanted_sha in (
        ("common_holdout", holdout_path, holdout_sha),
        ("fixed_normalization", normalization_path, normalization_sha),
    ):
        artifact = _binding(
            material_artifacts.get(key),
            Path(run_contract_binding["path"]).parent,
            f"paired material {key}",
        )
        if Path(artifact["path"]) != wanted_path or artifact["sha256"] != wanted_sha:
            raise EvaluationError(f"paired run-contract {key} binding mismatch")
    training_contract = run_contract.get("training_contract") or {}
    release_boundary = run_contract.get("release_boundary") or {}
    _require_exact_int(
        training_contract.get("test_access_event_count"),
        "paired run-contract test access count",
        expected=0,
    )
    if (
        training_contract.get("input_columns") != list(INPUT_COLUMNS)
        or training_contract.get("geometry_columns") != list(GEOMETRY_COLUMNS)
        or training_contract.get("forward_hidden_widths") != [256, 256, 256]
        or training_contract.get("inverse_hidden_widths") != [256, 256, 256]
        or training_contract.get("inverse_geometry_projection") != "independent_sigmoid"
        or training_contract.get("evaluation_mode") != "validation_only"
        or release_boundary.get("fresh_emx_accessed") is not False
        or release_boundary.get("test_evaluation_performed") is not False
        or release_boundary.get("numerical_metrics_released") is not False
        or release_boundary.get("success_after_training")
        != "READY_FOR_ONE_TIME_COMMON_TEST_EVALUATION"
    ):
        raise EvaluationError("paired run-contract scientific/test-isolation mismatch")
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list) or len(raw_pairs) != 3:
        raise EvaluationError("six-arm controller terminal lacks three pair receipts")
    models: dict[str, dict[str, Any]] = {}
    pairs: list[dict[str, Any]] = []
    for raw, expected_seed in zip(raw_pairs, EXACT_PAIRED_SEEDS):
        pair_binding = _binding(
            raw, path.parent, f"seed {expected_seed} pair completion receipt"
        )
        pair_audit, pair_models = _audit_pair_receipt(
            pair_binding,
            controller_root=controller_root,
            run_contract_binding=run_contract_binding,
            seed=expected_seed,
            normalization_sha=normalization_sha,
            holdout_sha=holdout_sha,
            normalization=normalization,
            paired_runtime=paired_runtime,
            controlled_singleton=paired_singleton,
            fixture_mode=fixture_mode,
        )
        pairs.append(pair_audit)
        for model in pair_models:
            key = _model_key(model["seed"], model["arm"])
            if key in models:
                raise EvaluationError(f"duplicate terminal model: {key}")
            models[key] = model
    if tuple(models) != _expected_model_keys():
        raise EvaluationError("six-arm terminal model ordering/identity differs")
    final_manifest_binding = _binding(
        payload.get("final_artifact_manifest"), path.parent, "controller final artifact manifest"
    )
    if Path(final_manifest_binding["path"]) != controller_root / "FINAL_ARTIFACT_MANIFEST.json":
        raise EvaluationError("controller final-artifact-manifest path mismatch")
    final_manifest = _json(
        Path(final_manifest_binding["path"]), "controller final artifact manifest"
    )
    if (
        final_manifest.get("schema") != FINAL_ARTIFACT_MANIFEST_SCHEMA
        or final_manifest.get("root") != str(controller_root)
        or final_manifest.get("excluded_paths")
        != [
            "controller.lock",
            "SHA256SUMS.txt",
            "FINAL_SHA256SUMS.txt",
            "FINAL_ARTIFACT_MANIFEST.json",
            "receipts/COMPLETE_RECEIPT.json",
        ]
        or final_manifest.get("all_other_regular_outputs_indexed") is not True
    ):
        raise EvaluationError("controller final-artifact-manifest boundary mismatch")
    final_records = final_manifest.get("artifacts")
    if not isinstance(final_records, list):
        raise EvaluationError("controller final artifact manifest lacks artifacts")
    final_by_path: dict[str, dict[str, Any]] = {}
    final_by_relative: dict[str, dict[str, Any]] = {}
    final_relative_order: list[str] = []
    for record in final_records:
        if not isinstance(record, dict):
            raise EvaluationError("controller final artifact record is invalid")
        _exact_keys(
            record,
            {"relative_path", "path", "sha256", "size_bytes"},
            "controller final artifact",
        )
        artifact = _binding(record, controller_root, "controller final artifact")
        if Path(artifact["path"]) != controller_root / str(record["relative_path"]):
            raise EvaluationError("controller final artifact relative path mismatch")
        if artifact["path"] in final_by_path:
            raise EvaluationError("controller final artifact manifest contains a duplicate")
        final_by_path[artifact["path"]] = artifact
        relative_name = str(record["relative_path"])
        if relative_name in final_by_relative:
            raise EvaluationError("controller final artifact manifest duplicates a relative path")
        final_by_relative[relative_name] = artifact
        final_relative_order.append(relative_name)
    required_terminal_artifacts = [run_contract_binding, *[{
        "path": pair["path"], "sha256": pair["sha256"], "size_bytes": Path(pair["path"]).stat().st_size
    } for pair in pairs]]
    for model in models.values():
        for field in ("summary", "weights"):
            required_terminal_artifacts.append(model[field])
        for field in ("terminal_receipt", "attempt_terminal_receipt"):
            record = model[field]
            required_terminal_artifacts.append(
                {
                    **record,
                    "size_bytes": Path(record["path"]).stat().st_size,
                }
            )
        runtime_attestation = model.get("runtime_attestation")
        if runtime_attestation is not None:
            required_terminal_artifacts.append(
                {
                    key: runtime_attestation[key]
                    for key in ("path", "sha256", "size_bytes")
                }
            )
    for artifact in required_terminal_artifacts:
        if final_by_path.get(str(artifact["path"])) != dict(artifact):
            raise EvaluationError("controller final manifest lacks terminal/model SHA closure")
    final_index = _verify_paired_final_index(
        _file(
            controller_root / PAIRED_FINAL_INDEX_NAME,
            "paired final SHA index",
        ),
        controller_root,
    )
    expected_manifest_relative = sorted(
        set(final_index["indexed_relative_paths"])
        - {"FINAL_ARTIFACT_MANIFEST.json", "receipts/COMPLETE_RECEIPT.json"}
    )
    if final_relative_order != expected_manifest_relative:
        raise EvaluationError(
            "controller final artifact manifest is not the exact lexical nonexcluded file set"
        )
    for relative_name in expected_manifest_relative:
        if (
            final_by_relative[relative_name]["sha256"]
            != final_index["indexed_sha256"][relative_name]
        ):
            raise EvaluationError(
                "controller final manifest and final SHA index disagree on artifact bytes"
            )
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "run_contract": _binding_core(run_contract_binding),
        "runner": _binding_core(runner),
        "trainer_launch_contract": trainer_launch_contract,
        "shared_contract": paired_shared_evidence,
        "runtime": paired_runtime,
        "controlled_singleton": paired_singleton,
        "singleton_transmission_closure": singleton_transmission,
        "final_artifact_manifest": _binding_core(final_manifest_binding),
        "final_sha_index": final_index,
        "models": models,
        "pair_receipts": pairs,
        "test_access_event_count_total": 0,
        "evaluation_mode": "validation_only",
    }


def _audit_holdout(
    path: Path, *, expected_rows: int, fixture_mode: bool
) -> dict[str, Any]:
    payload = _json(path, "common holdout")
    if payload.get("schema") != HOLDOUT_SCHEMA:
        raise EvaluationError("common holdout schema mismatch")
    if payload.get("identity_kind") != "canonical_geometry_sha256":
        raise EvaluationError("common holdout identity kind mismatch")
    raw_test = payload.get("test_geometry_identities")
    if not isinstance(raw_test, list):
        raise EvaluationError("common holdout lacks test identities")
    test = [str(value).strip().lower() for value in raw_test]
    if len(test) != expected_rows or len(set(test)) != expected_rows:
        raise EvaluationError("common holdout test identity count/uniqueness mismatch")
    if any(not _is_sha(value) for value in test):
        raise EvaluationError("common holdout test identity is not SHA-256")
    if not fixture_mode and expected_rows != EXPECTED_COMMON_TEST_ROWS:
        raise EvaluationError("production common holdout denominator changed")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "test_row_count": expected_rows,
        "test_identity_order_sha256": hashlib.sha256(
            "".join(f"{value}\n" for value in test).encode("ascii")
        ).hexdigest(),
    }


def _audit_fixed_targets(
    path: Path,
    *,
    expected_rows: int,
    expected_legacy: int,
    expected_extension: int,
    fixture_mode: bool,
) -> dict[str, Any]:
    payload = _json(path, "fixed target frame")
    rows = payload.get("targets")
    if not isinstance(rows, list) or len(rows) != expected_rows:
        raise EvaluationError("fixed target frame row count mismatch")
    ids: set[str] = set()
    legacy = 0
    extension = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise EvaluationError(f"fixed target row {index} is not an object")
        target_id = str(row.get("target_id") or "")
        try:
            values = tuple(float(row[key]) for key in FEATURE_KEYS)
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationError(f"fixed target row {index} has invalid values") from exc
        if not target_id or target_id in ids or any(not math.isfinite(value) for value in values):
            raise EvaluationError(f"fixed target row {index} identity/finite gate failed")
        ids.add(target_id)
        if values[3] <= 0.8:
            legacy += 1
        else:
            extension += 1
    if (legacy, extension) != (expected_legacy, expected_extension):
        raise EvaluationError("fixed target legacy/extension denominator mismatch")
    if not fixture_mode and (
        expected_rows,
        expected_legacy,
        expected_extension,
        _sha256(path),
    ) != (
        EXPECTED_FIXED_ROWS,
        EXPECTED_LEGACY_ROWS,
        EXPECTED_EXTENSION_ROWS,
        FIXED10K_SHA256,
    ):
        raise EvaluationError("production fixed10k immutable identity changed")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "row_count": expected_rows,
        "legacy_row_count": legacy,
        "extension_row_count": extension,
    }


def _material_artifact(
    summary: dict[str, Any], summary_path: Path, filename: str
) -> dict[str, Any]:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict) or filename not in artifacts:
        raise EvaluationError(f"materialization summary lacks {filename}")
    return _binding(artifacts[filename], summary_path.parent, f"material {filename}")


def _audit_preregistration_addendum(path: Path, expected_sha: str) -> dict[str, Any]:
    sha = _require_file_sha(path, expected_sha, "preregistration addendum v1.2")
    if sha != PREREGISTRATION_ADDENDUM_SHA256:
        raise EvaluationError("preregistration addendum is not the exact frozen v1.2 SHA")
    payload = _json(path, "preregistration addendum v1.2")
    if (
        payload.get("schema") != PREREGISTRATION_ADDENDUM_SCHEMA
        or payload.get("status")
        != "PRE_RESULT_ADDITIVE_SPATIAL_SENSITIVITY_CONTRACT_FROZEN"
        or payload.get("result_blind") is not True
        or payload.get("scientific_contract_changed") is not False
        or payload.get("results_or_metrics_accessed_before_freeze") is not False
        or payload.get("materialization_started_before_freeze") is not False
        or payload.get("training_started_before_freeze") is not False
        or payload.get("evaluation_started_before_freeze") is not False
        or payload.get("fresh_emx_started_before_freeze") is not False
    ):
        raise EvaluationError("preregistration addendum top-level contract mismatch")
    if payload.get("base_preregistration") != {
        "path": "CONTROLLED_EXPERIMENT_PREREGISTRATION_V1.json",
        "sha256": "19aca7778f4974fd3e7eadaca8b291783e8e08e99a53a9dca70b070a4bf16417",
    }:
        raise EvaluationError("preregistration addendum base-v1 binding mismatch")
    if payload.get("preceding_addendum") != {
        "path": "CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_1.json",
        "sha256": "9f1eb0e071ade0e5a42597b4242409282ed8d34cf159104f71df2d4d0d0a8633",
    }:
        raise EvaluationError("preregistration addendum v1.1 binding mismatch")
    contract = payload.get("spatial_sensitivity_contract")
    if not isinstance(contract, dict):
        raise EvaluationError("preregistration addendum lacks spatial contract")
    expected_scalars = {
        "method": "physical_cell_cluster_bootstrap_on_each_frozen_finite_frame",
        "replicates": SPATIAL_BOOTSTRAP_REPLICATES,
        "master_seed": SPATIAL_BOOTSTRAP_MASTER_SEED,
        "random_generator": "numpy.random.Generator(PCG64)",
        "frame_specific_seed_derivation": "unsigned_first_8_bytes_big_endian_of_SHA256(utf8(master_seed:frame_id))",
        "frames": list(SPATIAL_FRAME_IDS),
    }
    if any(contract.get(key) != value for key, value in expected_scalars.items()):
        raise EvaluationError("preregistration addendum bootstrap scalar/frame mismatch")
    cell = contract.get("physical_cell_definition") or {}
    if (
        cell.get("encoder") != "canonical_physical_cell_id_from_shared_contract"
        or cell.get("dimensions")
        != ["Lp_nH", "Ls_nH", "Q_min", "K_abs_for_clustering_only"]
        or cell.get("bins_per_dimension") != 4
        or cell.get("declared_lower") != list(INPUT_LOWER)
        or cell.get("declared_upper") != list(INPUT_UPPER)
        or cell.get("common_and_legacy_K_rule") != "use exact K_abs"
        or cell.get("highK_and_full10k_K_rule")
        != "use min(K_abs,0.8) for cluster assignment only"
        or cell.get("metric_K_rule") != "always retain and evaluate the original unclipped K_abs"
        or cell.get("highK_interpretation")
        != "Clipping is only a deterministic clustering extension of the frozen in-support cell partition; it is not model input clipping and does not convert high-K rows into in-support evidence."
    ):
        raise EvaluationError("preregistration addendum physical-cell rule mismatch")
    resampling = contract.get("resampling") or {}
    if (
        resampling.get("observed_cells_drawn_per_replicate")
        != "C draws with replacement from the C distinct cells observed within the reported frame or panel"
        or resampling.get("row_policy")
        != "include every member row of each drawn cell with the draw multiplicity"
        or resampling.get("cross_model_pairing")
        != "use the identical resampled row multiset for both arms and all three paired seeds"
        or resampling.get("panel_policy")
        != "cluster membership and resampling are computed separately within each reported frame or panel"
        or resampling.get("empty_frame_or_missing_arm_policy")
        != "fail closed and retain the failed denominator"
    ):
        raise EvaluationError("preregistration addendum resampling rule mismatch")
    reported = contract.get("reported_statistic") or {}
    if (
        reported.get("per_replicate")
        != "for every preregistered scalar metric, recompute each seed-level large-minus-small delta on the resampled row multiset and take the arithmetic mean across the three paired seeds"
        or reported.get("point_estimate")
        != "unbootstrapped arithmetic mean of the three paired seed deltas"
        or reported.get("interval")
        != "2.5th and 97.5th percentiles of the 2000 replicate statistics"
        or reported.get("p_values") is not False
        or reported.get("minimum_complete_pairs") != 3
    ):
        raise EvaluationError("preregistration addendum reported-statistic rule mismatch")
    scope = contract.get("scope") or {}
    if (
        scope.get("meaning")
        != "finite-frame spatial-composition sensitivity conditional on the six frozen trained models"
        or scope.get("not_training_seed_uncertainty") is not True
        or scope.get("not_deployment_population_uncertainty") is not True
        or scope.get("not_fresh_emx_evidence") is not True
        or scope.get("not_a_substitute_for_df2_paired_seed_interval") is not True
    ):
        raise EvaluationError("preregistration addendum interpretation boundary mismatch")
    return {"path": str(path), "sha256": sha, "schema": PREREGISTRATION_ADDENDUM_SCHEMA}


def _build_release_contract(
    args: argparse.Namespace,
    *,
    output_root_identity: Mapping[str, Any],
    one_time_release_lease: Mapping[str, Any],
) -> dict[str, Any]:
    evaluator_path = _file(Path(__file__), "evaluator source")
    evaluator_identity = _pinned_file_identity(evaluator_path, "evaluator source")
    if evaluator_identity != _MODULE_LOAD_EVALUATOR_IDENTITY:
        raise EvaluationError("evaluator source path bytes changed after module import")
    shared_identity = _shared_contract_identity()
    shared_path = Path(shared_identity["path"])
    shared_sha = str(shared_identity["sha256"])
    runtime = _runtime_identity(args)
    addendum_path = _file(args.preregistration_addendum, "preregistration addendum")
    addendum = _audit_preregistration_addendum(
        addendum_path, args.expected_preregistration_addendum_sha256
    )
    material_path = _file(args.materialization_summary, "materialization summary")
    material_sha = _require_file_sha(
        material_path,
        args.expected_materialization_summary_sha256,
        "materialization summary",
    )
    material = _json(material_path, "materialization summary")
    if material.get("schema") != MATERIAL_SCHEMA or material.get("status") != "PASS":
        raise EvaluationError("materialization is not a PASS v2 summary")
    holdout_path = _file(args.common_holdout, "common holdout")
    holdout_sha = _require_file_sha(
        holdout_path, args.expected_common_holdout_sha256, "common holdout"
    )
    normalization_path = _file(args.fixed_normalization, "fixed normalization")
    normalization_sha = _require_file_sha(
        normalization_path,
        args.expected_fixed_normalization_sha256,
        "fixed normalization",
    )
    targets_path = _file(args.fixed_targets_json, "fixed targets")
    targets_sha = _require_file_sha(
        targets_path, args.expected_fixed_targets_sha256, "fixed targets"
    )
    if not args.fixture_mode and targets_sha != FIXED10K_SHA256:
        raise EvaluationError("production fixed target SHA is not the frozen fixed10k identity")
    trainer_path = _file(args.trainer_source, "trainer source")
    trainer_sha = _require_file_sha(
        trainer_path, args.expected_trainer_sha256, "trainer source"
    )
    terminal_path = _file(args.six_arm_terminal_manifest, "six-arm terminal manifest")
    terminal_sha = _require_file_sha(
        terminal_path,
        args.expected_six_arm_terminal_manifest_sha256,
        "six-arm terminal manifest",
    )

    material_holdout = _material_artifact(
        material, material_path, "fixed_common_holdout_manifest.json"
    )
    material_normalization = _material_artifact(
        material,
        material_path,
        "declared_midpoint_half_range_normalization_contract.json",
    )
    small_csv = _material_artifact(material, material_path, "arm_source_n10000.csv")
    if Path(material_holdout["path"]) != holdout_path or material_holdout["sha256"] != holdout_sha:
        raise EvaluationError("explicit common holdout differs from materialization binding")
    if Path(material_normalization["path"]) != normalization_path or material_normalization["sha256"] != normalization_sha:
        raise EvaluationError("explicit fixed normalization differs from materialization binding")

    normalization_payload = _json(normalization_path, "fixed normalization")
    normalization = _normalization_vectors(normalization_payload)
    holdout = _audit_holdout(
        holdout_path,
        expected_rows=args.expected_common_test_rows,
        fixture_mode=args.fixture_mode,
    )
    fixed = _audit_fixed_targets(
        targets_path,
        expected_rows=args.expected_fixed_rows,
        expected_legacy=args.expected_legacy_rows,
        expected_extension=args.expected_extension_rows,
        fixture_mode=args.fixture_mode,
    )
    terminal = _audit_terminal_manifest(
        terminal_path,
        material_path=material_path,
        material_sha=material_sha,
        holdout_path=holdout_path,
        holdout_sha=holdout_sha,
        normalization_path=normalization_path,
        normalization_sha=normalization_sha,
        trainer_path=trainer_path,
        trainer_sha=trainer_sha,
        shared_contract_path=shared_path,
        shared_contract_sha=shared_sha,
        evaluator_runtime=runtime,
        evaluator_controlled_singleton=getattr(
            args, "controlled_singleton_identity", None
        ),
        normalization=normalization,
        fixture_mode=args.fixture_mode,
    )
    model_bindings = [
        {
            "model_key": key,
            "seed": model["seed"],
            "arm": model["arm"],
            "summary_sha256": model["summary"]["sha256"],
            "weights_sha256": model["weights"]["sha256"],
            "terminal_receipt_sha256": model["terminal_receipt"]["sha256"],
        }
        for key, model in terminal["models"].items()
    ]
    pair_bindings = [
        {"seed": pair["seed"], "receipt_sha256": pair["sha256"]}
        for pair in terminal["pair_receipts"]
    ]
    consumed_inputs: dict[str, dict[str, Any]] = {
        "evaluator_source": evaluator_identity,
        "shared_scientific_contract": shared_identity,
        "trainer_source": _pinned_file_identity(
            trainer_path, "trainer source", expected_sha256=trainer_sha
        ),
        "preregistration_addendum_v1_2": _pinned_file_identity(
            addendum_path,
            "preregistration addendum v1.2",
            expected_sha256=addendum["sha256"],
        ),
        "materialization_summary": _pinned_file_identity(
            material_path,
            "materialization summary",
            expected_sha256=material_sha,
        ),
        "common_source_csv": _pinned_file_identity(
            Path(small_csv["path"]),
            "common source CSV",
            expected_sha256=small_csv["sha256"],
        ),
        "common_holdout": _pinned_file_identity(
            holdout_path, "common holdout", expected_sha256=holdout_sha
        ),
        "fixed_normalization": _pinned_file_identity(
            normalization_path,
            "fixed normalization",
            expected_sha256=normalization_sha,
        ),
        "fixed_targets": _pinned_file_identity(
            targets_path, "fixed targets", expected_sha256=targets_sha
        ),
        "six_arm_terminal_manifest": _pinned_file_identity(
            terminal_path,
            "six-arm terminal manifest",
            expected_sha256=terminal_sha,
        ),
        "paired_run_contract": _pinned_file_identity(
            Path(terminal["run_contract"]["path"]),
            "paired run contract",
            expected_sha256=terminal["run_contract"]["sha256"],
        ),
        "paired_runner": _pinned_file_identity(
            Path(terminal["runner"]["path"]),
            "paired runner",
            expected_sha256=terminal["runner"]["sha256"],
        ),
        "paired_final_artifact_manifest": _pinned_file_identity(
            Path(terminal["final_artifact_manifest"]["path"]),
            "paired final artifact manifest",
            expected_sha256=terminal["final_artifact_manifest"]["sha256"],
        ),
        "paired_final_sha_index": _pinned_file_identity(
            Path(terminal["final_sha_index"]["path"]),
            "paired final SHA index",
            expected_sha256=terminal["final_sha_index"]["sha256"],
        ),
    }
    for role, identity in runtime["files"].items():
        consumed_inputs[f"runtime__{role}"] = dict(identity)
    for key, model in terminal["models"].items():
        consumed_inputs[f"model_summary__{key}"] = _pinned_file_identity(
            Path(model["summary"]["path"]),
            f"{key} model summary",
            expected_sha256=model["summary"]["sha256"],
        )
        consumed_inputs[f"model_weights__{key}"] = _pinned_file_identity(
            Path(model["weights"]["path"]),
            f"{key} model weights",
            expected_sha256=model["weights"]["sha256"],
        )
    for pair in terminal["pair_receipts"]:
        consumed_inputs[f"pair_receipt__seed_{pair['seed']}"] = _pinned_file_identity(
            Path(pair["path"]),
            f"seed {pair['seed']} pair receipt",
            expected_sha256=pair["sha256"],
        )
    controlled_singleton = getattr(args, "controlled_singleton_identity", None)
    if controlled_singleton is not None:
        consumed_inputs["controlled_singleton_lock"] = dict(controlled_singleton)
    release_bindings = {
        "evaluation_output_root": str(_absolute_lexical(args.out_dir)),
        "evaluation_output_root_identity": dict(output_root_identity),
        "one_time_release_lease": dict(one_time_release_lease),
        "shared_scientific_contract": shared_identity,
        "numerical_runtime": runtime,
        "paired_runtime": terminal["runtime"],
        "paired_shared_contract": terminal["shared_contract"],
        "consumed_inputs": consumed_inputs,
        "preregistration_addendum_v1_2_sha256": addendum["sha256"],
        "materialization_summary_sha256": material_sha,
        "common_test_source_csv_sha256": small_csv["sha256"],
        "common_holdout_sha256": holdout_sha,
        "fixed_normalization_sha256": normalization_sha,
        "fixed10k_sha256": targets_sha,
        "trainer_source_sha256": trainer_sha,
        "six_arm_terminal_manifest_sha256": terminal_sha,
        "paired_run_contract_sha256": terminal["run_contract"]["sha256"],
        "paired_runner_sha256": terminal["runner"]["sha256"],
        "trainer_launch_contract": terminal["trainer_launch_contract"],
        "paired_final_artifact_manifest_sha256": terminal[
            "final_artifact_manifest"
        ]["sha256"],
        "paired_final_sha_index_sha256": terminal["final_sha_index"]["sha256"],
        "models": model_bindings,
        "pair_receipts": pair_bindings,
    }
    if controlled_singleton is not None:
        release_bindings["controlled_singleton"] = dict(controlled_singleton)
    return {
        "schema": "controlled_real10k_20k_common_release_contract_v1",
        "fixture_only": bool(args.fixture_mode),
        "release_bindings": release_bindings,
        "release_contract_sha256": _canonical_sha(release_bindings),
        "paths": {
            "evaluator_source": str(evaluator_path),
            "shared_scientific_contract": str(shared_path),
            "preregistration_addendum": str(addendum_path),
            "materialization_summary": str(material_path),
            "common_holdout": str(holdout_path),
            "fixed_normalization": str(normalization_path),
            "fixed_targets": str(targets_path),
            "trainer_source": str(trainer_path),
            "six_arm_terminal_manifest": str(terminal_path),
            "paired_run_contract": terminal["run_contract"]["path"],
            "paired_runner": terminal["runner"]["path"],
            "paired_final_artifact_manifest": terminal[
                "final_artifact_manifest"
            ]["path"],
            "paired_final_sha_index": terminal["final_sha_index"]["path"],
            "small_source_csv": small_csv["path"],
        },
        "small_source_csv": small_csv,
        "normalization": {
            key: [float(value) for value in np.asarray(vector)]
            for key, vector in normalization.items()
        },
        "holdout": holdout,
        "fixed_targets": fixed,
        "models": terminal["models"],
        "pair_receipts": terminal["pair_receipts"],
        "scientific_contract": {
            "primary_common_holdout_estimand": "forward_prediction_vs_stored_real_emx_label",
            "secondary_estimands": [
                "inverse_to_own_forward_proxy_self_consistency",
                "inverse_geometry_to_recorded_label_distance_nonunique_inverse",
            ],
            "fixed10k_evidence": "own_forward_one_shot_proxy_not_fresh_emx",
            "fresh_emx_generated": False,
            "fixed10k_regenerated": False,
            "K_target_relative_APE_primary": False,
            "paired_delta": "large_minus_small_within_seed",
            "spatial_sensitivity": {
                "method": "physical_cell_cluster_bootstrap_on_each_frozen_finite_frame",
                "replicates": int(args.bootstrap_replicates),
                "master_seed": SPATIAL_BOOTSTRAP_MASTER_SEED,
                "frame_ids": list(SPATIAL_FRAME_IDS),
                "preregistration_addendum_sha256": addendum["sha256"],
            },
        },
        "denominators": {
            "common_real_emx_holdout_rows": args.expected_common_test_rows,
            "fixed10k_full_rows": args.expected_fixed_rows,
            "fixed10k_legacy_rows": args.expected_legacy_rows,
            "fixed10k_extension_rows": args.expected_extension_rows,
            "model_arms": 6,
            "paired_seeds": 3,
        },
    }


def _prepare(args: argparse.Namespace) -> int:
    out_dir = _absolute_lexical(args.out_dir)
    _reject_symlink_chain(out_dir.parent, "evaluation output parent")
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise EvaluationError(f"prepare output already exists: {out_dir}") from exc
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(os.fspath(out_dir.parent), directory_flags)
    root_descriptor = os.open(
        out_dir.name, directory_flags, dir_fd=parent_descriptor
    )
    challenge_nonce = secrets.token_hex(16)
    if not GO_NONCE_PATTERN.fullmatch(challenge_nonce):
        os.close(root_descriptor)
        os.close(parent_descriptor)
        raise EvaluationError("generated evaluator QA challenge nonce is invalid")
    root_identity = _directory_identity_from_descriptor(
        root_descriptor, out_dir, "new evaluation output root"
    )
    lease_name = (
        ".controlled_real10k20k_lease_"
        f"{hashlib.sha256(str(out_dir).encode('utf-8')).hexdigest()[:16]}_"
        f"{challenge_nonce}"
    )
    lease_path = out_dir.parent / lease_name
    lease_payload = _lease_state_bytes("PREPARED", challenge_nonce, root_identity)
    lease_flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    lease_descriptor = os.open(
        lease_name, lease_flags, 0o600, dir_fd=parent_descriptor
    )
    try:
        view = memoryview(lease_payload)
        while view:
            written = os.write(lease_descriptor, view)
            if written <= 0:
                raise EvaluationError("short write creating one-time release lease")
            view = view[written:]
        os.fchmod(lease_descriptor, 0o600)
        os.fsync(lease_descriptor)
        os.fsync(parent_descriptor)
        lease_identity = _lease_identity_from_descriptor(
            lease_descriptor, lease_path, "PREPARED", lease_payload
        )
    finally:
        os.close(lease_descriptor)
    try:
        contract = _build_release_contract(
            args,
            output_root_identity=root_identity,
            one_time_release_lease=lease_identity,
        )
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "PASS_PREPARED_RESULT_BLIND_NOT_AUTHORIZED",
            "result_blind": True,
            "test_rows_read": False,
            "inference_performed": False,
            "test_release_authorized": False,
            "fixture_only": bool(args.fixture_mode),
            "qa_challenge_nonce": challenge_nonce,
            "release_contract": contract,
        }
        _write_json_at_x(
            root_descriptor, parent_descriptor, MANIFEST_NAME, manifest
        )
        _write_bytes_at_x(root_descriptor, parent_descriptor, LOCK_NAME, b"")
        prepared = {
            "schema": PREPARED_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "PASS",
            "verdict": "PREPARED_AWAITING_FRESH_INDEPENDENT_EXACT_GO",
            "result_blind": True,
            "test_access_event_count": 0,
            "test_rows_read": False,
            "inference_performed": False,
            "evaluation_manifest": {
                "path": str(out_dir / MANIFEST_NAME),
                "sha256": _sha256_at(root_descriptor, MANIFEST_NAME),
            },
            "qa_challenge_nonce": challenge_nonce,
            "evaluator_source": contract["release_bindings"]["consumed_inputs"][
                "evaluator_source"
            ],
            "shared_scientific_contract": contract["release_bindings"][
                "shared_scientific_contract"
            ],
            "numerical_runtime": contract["release_bindings"]["numerical_runtime"],
            "paired_runtime": contract["release_bindings"]["paired_runtime"],
            "paired_shared_contract": contract["release_bindings"][
                "paired_shared_contract"
            ],
            "controlled_singleton": contract["release_bindings"].get(
                "controlled_singleton"
            ),
            "release_contract_sha256": contract["release_contract_sha256"],
            "next_legal_gate": "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO",
            "execution_authorized": False,
        }
        _write_json_at_x(
            root_descriptor, parent_descriptor, PREPARED_NAME, prepared
        )
        qa = {
            "schema": QA_REQUIRED_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "INDEPENDENT_QA_REQUIRED",
            "execution_authorized": False,
            "required_go_schema": GO_SCHEMA,
            "required_go_scope": GO_SCOPE,
            "qa_challenge_nonce": challenge_nonce,
            "required_go_freshness": {
                "timestamp_format": "YYYY-MM-DDTHH:MM:SSZ",
                "issued_lte_now_lt_expires": True,
                "maximum_lifetime_seconds": int(MAX_GO_VALIDITY.total_seconds()),
                "nonce_pattern": "^[0-9a-f]{32}$",
            },
            "required_zero_findings": {"p0": 0, "p1": 0},
            "prepared_receipt_sha256": _sha256_at(root_descriptor, PREPARED_NAME),
            "evaluation_manifest_sha256": _sha256_at(root_descriptor, MANIFEST_NAME),
            "release_contract_sha256": contract["release_contract_sha256"],
            "required_checks": dict(GO_CHECKS),
            "required_bindings": [
                "prepared_receipt",
                "evaluation_manifest",
                "independent_qa_required",
                "prepare_sha_index",
                "release_contract_sha256",
                "release_bindings",
            ],
            "required_authorities": {
                "one_time_common_test_release": True,
                "common_real_emx_holdout_evaluation": True,
                "fixed10k_own_forward_proxy_evaluation": True,
                "fresh_emx": False,
                "data_generation": False,
                "training": False,
                "process_signal": False,
                "retry_after_claim": False,
                "fixture_only": bool(args.fixture_mode),
            },
        }
        _write_json_at_x(root_descriptor, parent_descriptor, QA_REQUIRED_NAME, qa)
        _write_index_at_x(
            root_descriptor,
            parent_descriptor,
            PREPARE_INDEX_NAME,
            [MANIFEST_NAME, PREPARED_NAME, QA_REQUIRED_NAME, LOCK_NAME],
        )
        _assert_held_root_matches(root_descriptor, out_dir, root_identity)
    except Exception as exc:
        try:
            os.stat("PREPARE_FAIL.json", dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            _write_json_at_x(
                root_descriptor,
                parent_descriptor,
                "PREPARE_FAIL.json",
                {
                    "schema": "controlled_real10k_20k_common_evaluation_prepare_fail_v1",
                    "generated_utc": _utc_now(),
                    "status": "FAIL_NO_GO",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "test_rows_read": False,
                    "inference_performed": False,
                    "execution_authorized": False,
                },
            )
        raise
    finally:
        os.close(root_descriptor)
        os.close(parent_descriptor)
    print("status=PASS_PREPARED_RESULT_BLIND_NOT_AUTHORIZED")
    print(f"prepared={out_dir / PREPARED_NAME}")
    return 0


def _load_prepared(
    out_dir: Path, root_descriptor: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes]]:
    expected_names = {MANIFEST_NAME, PREPARED_NAME, QA_REQUIRED_NAME, LOCK_NAME}
    actual_names = set(os.listdir(root_descriptor))
    if actual_names != PREPARE_FILE_NAMES:
        raise EvaluationError(
            f"execute requires untouched prepared directory; found {sorted(actual_names)}"
        )
    snapshots = {
        name: _read_bytes_at(root_descriptor, name, f"prepared file {name}")[0]
        for name in PREPARE_FILE_NAMES
    }
    _parse_index_bytes(
        snapshots[PREPARE_INDEX_NAME],
        snapshots,
        expected_names,
        "prepare SHA index",
    )
    prepared = _json_from_bytes(snapshots[PREPARED_NAME], "prepared receipt")
    manifest = _json_from_bytes(snapshots[MANIFEST_NAME], "evaluation manifest")
    qa = _json_from_bytes(snapshots[QA_REQUIRED_NAME], "QA-required marker")
    _exact_keys(
        prepared,
        {
            "schema",
            "generated_utc",
            "status",
            "verdict",
            "result_blind",
            "test_access_event_count",
            "test_rows_read",
            "inference_performed",
            "evaluation_manifest",
            "qa_challenge_nonce",
            "evaluator_source",
            "shared_scientific_contract",
            "numerical_runtime",
            "paired_runtime",
            "paired_shared_contract",
            "controlled_singleton",
            "release_contract_sha256",
            "next_legal_gate",
            "execution_authorized",
        },
        "prepared receipt",
    )
    _exact_keys(
        manifest,
        {
            "schema",
            "generated_utc",
            "status",
            "result_blind",
            "test_rows_read",
            "inference_performed",
            "test_release_authorized",
            "fixture_only",
            "qa_challenge_nonce",
            "release_contract",
        },
        "evaluation manifest",
    )
    _exact_keys(
        qa,
        {
            "schema",
            "generated_utc",
            "status",
            "execution_authorized",
            "required_go_schema",
            "required_go_scope",
            "qa_challenge_nonce",
            "required_go_freshness",
            "required_zero_findings",
            "prepared_receipt_sha256",
            "evaluation_manifest_sha256",
            "release_contract_sha256",
            "required_checks",
            "required_bindings",
            "required_authorities",
        },
        "QA-required marker",
    )
    _require_exact_json_equal(
        {
            key: prepared.get(key)
            for key in (
                "schema",
                "status",
                "verdict",
                "result_blind",
                "test_access_event_count",
                "test_rows_read",
                "inference_performed",
                "next_legal_gate",
                "execution_authorized",
            )
        },
        {
            "schema": PREPARED_SCHEMA,
            "status": "PASS",
            "verdict": "PREPARED_AWAITING_FRESH_INDEPENDENT_EXACT_GO",
            "result_blind": True,
            "test_access_event_count": 0,
            "test_rows_read": False,
            "inference_performed": False,
            "next_legal_gate": "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO",
            "execution_authorized": False,
        },
        "prepared receipt semantic gate",
    )
    _require_exact_json_equal(
        {
            key: manifest.get(key)
            for key in (
                "schema",
                "status",
                "result_blind",
                "test_rows_read",
                "inference_performed",
                "test_release_authorized",
            )
        },
        {
            "schema": MANIFEST_SCHEMA,
            "status": "PASS_PREPARED_RESULT_BLIND_NOT_AUTHORIZED",
            "result_blind": True,
            "test_rows_read": False,
            "inference_performed": False,
            "test_release_authorized": False,
        },
        "evaluation manifest result-blind gate",
    )
    if (
        qa.get("schema") != QA_REQUIRED_SCHEMA
        or qa.get("status") != "INDEPENDENT_QA_REQUIRED"
        or qa.get("execution_authorized") is not False
        or qa.get("prepared_receipt_sha256")
        != _sha256_bytes(snapshots[PREPARED_NAME])
        or qa.get("evaluation_manifest_sha256")
        != _sha256_bytes(snapshots[MANIFEST_NAME])
        or qa.get("required_go_schema") != GO_SCHEMA
        or qa.get("required_go_scope") != GO_SCOPE
        or qa.get("required_bindings")
        != [
            "prepared_receipt",
            "evaluation_manifest",
            "independent_qa_required",
            "prepare_sha_index",
            "release_contract_sha256",
            "release_bindings",
        ]
    ):
        raise EvaluationError("QA-required marker gate failed")
    _require_exact_json_equal(
        qa.get("required_zero_findings"),
        {"p0": 0, "p1": 0},
        "QA-required zero findings",
    )
    _require_exact_json_equal(
        qa.get("required_checks"), GO_CHECKS, "QA-required checks"
    )
    if prepared.get("evaluation_manifest", {}).get("sha256") != _sha256_bytes(
        snapshots[MANIFEST_NAME]
    ):
        raise EvaluationError("prepared receipt does not bind manifest")
    contract = manifest.get("release_contract")
    if not isinstance(contract, dict):
        raise EvaluationError("prepared manifest lacks release contract")
    if type(contract.get("fixture_only")) is not bool:
        raise EvaluationError("release contract fixture-only boundary is not a JSON boolean")
    fixture_only = contract["fixture_only"]
    _require_exact_bool(
        manifest.get("fixture_only"),
        fixture_only,
        "evaluation manifest fixture-only boundary",
    )
    expected_authorities = {
        "one_time_common_test_release": True,
        "common_real_emx_holdout_evaluation": True,
        "fixed10k_own_forward_proxy_evaluation": True,
        "fresh_emx": False,
        "data_generation": False,
        "training": False,
        "process_signal": False,
        "retry_after_claim": False,
        "fixture_only": fixture_only,
    }
    _require_exact_json_equal(
        qa.get("required_authorities"),
        expected_authorities,
        "QA-required authority scope",
    )
    challenge = manifest.get("qa_challenge_nonce")
    if (
        not isinstance(challenge, str)
        or not GO_NONCE_PATTERN.fullmatch(challenge)
        or prepared.get("qa_challenge_nonce") != challenge
        or qa.get("qa_challenge_nonce") != challenge
        or qa.get("release_contract_sha256") != contract.get("release_contract_sha256")
        or prepared.get("release_contract_sha256")
        != contract.get("release_contract_sha256")
    ):
        raise EvaluationError("prepared challenge/release-contract closure mismatch")
    release_bindings = contract.get("release_bindings") or {}
    for key in (
        "numerical_runtime",
        "paired_runtime",
        "paired_shared_contract",
        "controlled_singleton",
    ):
        _require_exact_json_equal(
            prepared.get(key),
            release_bindings.get(key),
            f"prepared {key} binding",
        )
    return prepared, manifest, snapshots


def _open_held_external_json(
    path: Path, expected_sha: str, label: str, stack: ExitStack
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """Open a review receipt once; hash and parse only those held bytes."""

    path = _absolute_lexical(path)
    _safe_file_metadata(path, label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(os.fspath(path), flags)
    handle = stack.enter_context(os.fdopen(descriptor, "rb", closefd=True))
    before = os.fstat(handle.fileno())
    payload_bytes = handle.read()
    after = os.fstat(handle.fileno())
    if any(
        getattr(before, field) != getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink")
    ):
        raise EvaluationError(f"{label} changed during its single held read")
    if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
        raise EvaluationError(f"{label} descriptor is not a single-link regular file")
    mode = stat.S_IMODE(after.st_mode)
    if mode & 0o7000 or mode & 0o022:
        raise EvaluationError(f"{label} descriptor mode is unsafe: {mode:04o}")
    lexical = path.lstat()
    if (lexical.st_dev, lexical.st_ino) != (after.st_dev, after.st_ino):
        raise EvaluationError(f"{label} pathname changed during its single held read")
    sha = _sha256_bytes(payload_bytes)
    if sha != _require_sha_token(expected_sha, f"{label} expected SHA"):
        raise EvaluationError(f"{label} SHA mismatch")
    payload = _json_from_bytes(payload_bytes, label)
    identity = {
        "path": str(path),
        "sha256": sha,
        "size_bytes": len(payload_bytes),
        "mode": f"{mode:04o}",
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "nlink": int(after.st_nlink),
    }
    return payload, identity, handle


def _assert_held_external_file_unchanged(handle: Any, identity: Mapping[str, Any]) -> None:
    observed = os.fstat(handle.fileno())
    exact = {
        "size_bytes": int(observed.st_size),
        "mode": f"{stat.S_IMODE(observed.st_mode):04o}",
        "device": int(observed.st_dev),
        "inode": int(observed.st_ino),
        "nlink": int(observed.st_nlink),
    }
    if any(identity[key] != value for key, value in exact.items()):
        raise EvaluationError("held independent GO descriptor identity changed before claim")
    try:
        lexical = Path(str(identity["path"])).lstat()
    except FileNotFoundError as exc:
        raise EvaluationError("held independent GO pathname disappeared before claim") from exc
    if (lexical.st_dev, lexical.st_ino) != (observed.st_dev, observed.st_ino):
        raise EvaluationError("held independent GO pathname was replaced before claim")


def _audit_go(
    path: Path,
    expected_sha: str,
    *,
    out_dir: Path,
    prepared: dict[str, Any],
    prepared_snapshots: Mapping[str, bytes],
    manifest: dict[str, Any],
    now: datetime,
    stack: ExitStack,
) -> tuple[dict[str, Any], Any]:
    path = _absolute_lexical(path)
    try:
        path.relative_to(out_dir)
    except ValueError:
        pass
    else:
        raise EvaluationError("independent exact-GO receipt must be external")
    payload, held_identity, held_handle = _open_held_external_json(
        path, expected_sha, "independent exact-GO receipt", stack
    )
    _exact_keys(
        payload,
        {
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
            "authorities",
        },
        "independent exact-GO receipt",
    )
    if (
        payload.get("schema") != GO_SCHEMA
        or payload.get("status") != "PASS"
        or payload.get("verdict") != "EXACT_GO"
        or payload.get("scope") != GO_SCOPE
        or payload.get("nonce") != manifest.get("qa_challenge_nonce")
    ):
        raise EvaluationError("independent GO receipt semantic gate failed")
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, dict):
        raise EvaluationError("independent GO reviewer is missing")
    _exact_keys(
        reviewer,
        {"role", "identity", "independent_of_builder_and_execution", "result_blind"},
        "independent GO reviewer",
    )
    if (
        reviewer.get("role") != "independent_qa"
        or not isinstance(reviewer.get("identity"), str)
        or not reviewer["identity"]
        or reviewer["identity"] != reviewer["identity"].strip()
        or reviewer.get("independent_of_builder_and_execution") is not True
        or reviewer.get("result_blind") is not True
    ):
        raise EvaluationError("independent GO reviewer contract failed")
    _require_exact_json_equal(
        payload.get("findings"),
        {"p0": 0, "p1": 0},
        "independent GO zero P0/P1 findings",
    )
    _require_exact_json_equal(
        payload.get("checks"),
        GO_CHECKS,
        "independent GO check keyset/values are not exact",
    )
    issued_raw = payload.get("issued_utc")
    expires_raw = payload.get("expires_utc")
    if (
        not isinstance(issued_raw, str)
        or not GO_UTC_PATTERN.fullmatch(issued_raw)
        or not isinstance(expires_raw, str)
        or not GO_UTC_PATTERN.fullmatch(expires_raw)
    ):
        raise EvaluationError("independent GO timestamps are not exact UTC-Z seconds")
    issued = _parse_utc(issued_raw, "GO issued_utc")
    expires = _parse_utc(expires_raw, "GO expires_utc")
    if (
        issued > now
        or expires <= now
        or expires <= issued
        or expires - issued > MAX_GO_VALIDITY
        or now - issued > MAX_GO_VALIDITY
    ):
        raise EvaluationError("independent GO is future-issued, stale, or overlong")
    contract = manifest["release_contract"]
    wanted = {
        "prepared_receipt": {
            "path": str(out_dir / PREPARED_NAME),
            "sha256": _sha256_bytes(prepared_snapshots[PREPARED_NAME]),
        },
        "evaluation_manifest": {
            "path": str(out_dir / MANIFEST_NAME),
            "sha256": _sha256_bytes(prepared_snapshots[MANIFEST_NAME]),
        },
        "independent_qa_required": {
            "path": str(out_dir / QA_REQUIRED_NAME),
            "sha256": _sha256_bytes(prepared_snapshots[QA_REQUIRED_NAME]),
        },
        "prepare_sha_index": {
            "path": str(out_dir / PREPARE_INDEX_NAME),
            "sha256": _sha256_bytes(prepared_snapshots[PREPARE_INDEX_NAME]),
        },
        "release_contract_sha256": contract["release_contract_sha256"],
        "release_bindings": contract["release_bindings"],
    }
    _require_exact_json_equal(
        payload.get("bindings"), wanted, "independent GO exact bindings mismatch"
    )
    wanted_authorities = {
        "one_time_common_test_release": True,
        "common_real_emx_holdout_evaluation": True,
        "fixed10k_own_forward_proxy_evaluation": True,
        "fresh_emx": False,
        "data_generation": False,
        "training": False,
        "process_signal": False,
        "retry_after_claim": False,
        "fixture_only": bool(contract.get("fixture_only")),
    }
    _require_exact_json_equal(
        payload.get("authorities"),
        wanted_authorities,
        "independent GO authority scope mismatch",
    )
    return {
        "path": str(path),
        "sha256": held_identity["sha256"],
        "held_file_identity": held_identity,
        "exact_payload": payload,
        "nonce": payload["nonce"],
        "reviewer_identity": reviewer["identity"],
        "findings": {"p0": 0, "p1": 0},
        "issued_utc": issued_raw,
        "expires_utc": expires_raw,
        "authorities": wanted_authorities,
    }, held_handle


def _load_trainer(source_bytes: bytes, path: Path, expected_sha256: str) -> ModuleType:
    digest = _sha256_bytes(source_bytes)
    if digest != _require_sha_token(expected_sha256, "held trainer expected SHA"):
        raise EvaluationError("held trainer source SHA mismatch")
    module_name = f"_controlled_common_trainer_{digest[:16]}"
    module = ModuleType(module_name)
    module.__dict__.update(
        {
            "__file__": str(path),
            "__package__": "",
            "__name__": module_name,
        }
    )
    sys.modules[module_name] = module
    try:
        code = compile(source_bytes, str(path), "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise EvaluationError(f"cannot import held trainer snapshot: {exc}") from exc
    for symbol in ("_predict", "_predict_inverse"):
        if not callable(getattr(module, symbol, None)):
            raise EvaluationError(f"trainer lacks callable {symbol}")
    return module


def _load_model(payload: bytes, expected_sha256: str) -> dict[str, Any]:
    if _sha256_bytes(payload) != _require_sha_token(
        expected_sha256, "held model expected SHA"
    ):
        raise EvaluationError("held model weights SHA mismatch")
    with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
        model = {
            "forward_weights": _numbered_arrays(archive, "forward_weight_"),
            "forward_biases": _numbered_arrays(archive, "forward_bias_"),
            "inverse_weights": _numbered_arrays(archive, "inverse_weight_"),
            "inverse_biases": _numbered_arrays(archive, "inverse_bias_"),
            "x_mean": np.asarray(archive["normalization__x_mean"], dtype=np.float64).copy(),
            "x_scale": np.asarray(archive["normalization__x_scale"], dtype=np.float64).copy(),
            "y_mean": np.asarray(archive["normalization__y_mean"], dtype=np.float64).copy(),
            "y_scale": np.asarray(archive["normalization__y_scale"], dtype=np.float64).copy(),
            "geometry_lower": np.asarray(
                archive["normalization__geometry_lower"], dtype=np.float64
            ).copy(),
            "geometry_upper": np.asarray(
                archive["normalization__geometry_upper"], dtype=np.float64
            ).copy(),
        }
    for key, value in model.items():
        arrays = value if isinstance(value, list) else [value]
        if any(np.any(~np.isfinite(array)) for array in arrays):
            raise EvaluationError(f"model contains non-finite {key}")
    return model


def _load_common_test(
    csv_bytes: bytes,
    holdout_bytes: bytes,
    *,
    expected_rows: int,
) -> tuple[list[dict[str, str]], np.ndarray, np.ndarray, list[str]]:
    holdout = _json_from_bytes(holdout_bytes, "common holdout after release")
    identities = [str(value).strip().lower() for value in holdout["test_geometry_identities"]]
    if len(identities) != expected_rows:
        raise EvaluationError("released common holdout denominator changed")
    by_identity: dict[str, dict[str, str]] = {}
    with io.StringIO(csv_bytes.decode("utf-8-sig"), newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("controlled_split_assignment") != "test":
                continue
            identity = str(row.get("canonical_geometry_identity_sha256") or "").strip().lower()
            if identity in by_identity:
                raise EvaluationError("common test CSV has duplicate geometry identity")
            by_identity[identity] = dict(row)
    if set(by_identity) != set(identities):
        raise EvaluationError("released test rows differ from frozen holdout identities")
    ordered = [by_identity[identity] for identity in identities]
    x_values: list[list[float]] = []
    y_values: list[list[float]] = []
    for index, row in enumerate(ordered):
        try:
            x = [float(row[column]) for column in INPUT_COLUMNS]
            y = [float(row[column]) for column in GEOMETRY_COLUMNS]
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationError(f"common test row {index} is invalid") from exc
        if any(not math.isfinite(value) for value in x + y):
            raise EvaluationError(f"common test row {index} is non-finite")
        if not _is_sha(str(row.get("touchstone_sha256") or "").strip().lower()):
            raise EvaluationError(f"common test row {index} lacks real-EMX content SHA")
        if not str(row.get("evaluation") or "") or not str(row.get("touchstone_path") or ""):
            raise EvaluationError(f"common test row {index} lacks real-EMX provenance")
        x_values.append(x)
        y_values.append(y)
    x_array = np.asarray(x_values, dtype=np.float64)
    y_array = np.asarray(y_values, dtype=np.float64)
    if x_array.shape != (expected_rows, 4) or y_array.shape != (expected_rows, 10):
        raise EvaluationError("common test matrix shape mismatch")
    return ordered, x_array, y_array, identities


def _load_fixed_matrix(
    payload_bytes: bytes, expected_rows: int
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    payload = _json_from_bytes(payload_bytes, "fixed target frame after release")
    rows = payload.get("targets")
    if not isinstance(rows, list) or len(rows) != expected_rows:
        raise EvaluationError("fixed target frame changed after preparation")
    ids: list[str] = []
    values: list[list[float]] = []
    for row in rows:
        ids.append(str(row["target_id"]))
        values.append([float(row[key]) for key in FEATURE_KEYS])
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.shape != (expected_rows, 4) or np.any(~np.isfinite(matrix)):
        raise EvaluationError("fixed target matrix shape/finite gate failed")
    legacy = matrix[:, 3] <= 0.8
    extension = ~legacy
    return ids, matrix, legacy, extension


def _physical_cell_ids(
    response: np.ndarray, *, cap_high_k_for_clustering_only: bool
) -> tuple[list[str], np.ndarray]:
    """Encode the frozen physical cells without changing metric/model inputs."""

    values = np.asarray(response, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 4 or np.any(~np.isfinite(values)):
        raise EvaluationError("physical-cell response matrix shape/finite gate failed")
    clustering = values.copy()
    capped = np.zeros(values.shape[0], dtype=bool)
    if cap_high_k_for_clustering_only:
        capped = clustering[:, 3] > INPUT_UPPER[3]
        clustering[:, 3] = np.minimum(clustering[:, 3], INPUT_UPPER[3])
    try:
        cell_ids = [canonical_physical_cell_id(row) for row in clustering]
    except ValueError as exc:
        raise EvaluationError(f"physical-cell encoding failed: {exc}") from exc
    return cell_ids, capped


def _distribution(values: np.ndarray) -> dict[str, float]:
    vector = np.asarray(values, dtype=np.float64).reshape(-1)
    if vector.size == 0 or np.any(~np.isfinite(vector)):
        raise EvaluationError("metric distribution is empty or non-finite")
    return {
        "P50": float(np.percentile(vector, 50.0)),
        "P90": float(np.percentile(vector, 90.0)),
        "P95": float(np.percentile(vector, 95.0)),
        "P99": float(np.percentile(vector, 99.0)),
        "Max": float(np.max(vector)),
    }


def _response_metrics(
    target: np.ndarray,
    predicted: np.ndarray,
    *,
    declared_spans: np.ndarray,
    evidence_class: str,
    requested_rows: int,
) -> dict[str, Any]:
    target = np.asarray(target, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)
    spans = np.asarray(declared_spans, dtype=np.float64)
    if (
        target.shape != predicted.shape
        or target.ndim != 2
        or target.shape[1] != 4
        or spans.shape != (4,)
        or np.any(spans <= 0)
        or np.any(~np.isfinite(target))
        or np.any(~np.isfinite(predicted))
    ):
        raise EvaluationError("response metric input shape/finite/span gate failed")
    residual = predicted - target
    absolute = np.abs(residual)
    per_feature: dict[str, Any] = {}
    for index, (feature, unit) in enumerate(zip(FEATURE_KEYS, FEATURE_UNITS)):
        feature_abs = absolute[:, index]
        per_feature[feature] = {
            "unit": unit,
            "MAE": float(np.mean(feature_abs)),
            "RMSE": float(np.sqrt(np.mean(residual[:, index] ** 2))),
            **_distribution(feature_abs),
            "target_relative_APE_is_primary": False if feature == "K_abs" else None,
        }
    declared_scaled = residual / spans[None, :]
    fixed_scaled = residual / FIXED_RESPONSE_SPANS[None, :]
    q_shortfall = np.maximum(target[:, 2] - predicted[:, 2], 0.0)
    engineering_scaled = fixed_scaled.copy()
    engineering_scaled[:, 2] = q_shortfall / FIXED_RESPONSE_SPANS[2]
    declared_row = np.sqrt(np.mean(declared_scaled**2, axis=1))
    fixed_row = np.sqrt(np.mean(fixed_scaled**2, axis=1))
    engineering_row = np.sqrt(np.mean(engineering_scaled**2, axis=1))
    evaluated = int(target.shape[0])
    return {
        "evidence_class": evidence_class,
        "denominator": {
            "requested_rows": int(requested_rows),
            "evaluated_rows": evaluated,
            "failed_rows": int(requested_rows - evaluated),
            "finite_rows": evaluated,
        },
        "per_feature": per_feature,
        "joint": {
            "declared_range_joint_NRMSE": float(np.sqrt(np.mean(declared_scaled**2))),
            "declared_range_per_row": _distribution(declared_row),
            "fixed_span_symmetric_joint_NRMSE": float(np.sqrt(np.mean(fixed_scaled**2))),
            "fixed_span_symmetric_per_row": _distribution(fixed_row),
            "fixed_span_engineering_joint_error": float(
                np.sqrt(np.mean(engineering_scaled**2))
            ),
            "fixed_span_engineering_per_row": _distribution(engineering_row),
            "declared_spans": [float(value) for value in spans],
            "fixed_spans": [float(value) for value in FIXED_RESPONSE_SPANS],
        },
        "Q_guardrail": {
            "target_met_rate": float(np.mean(predicted[:, 2] >= target[:, 2])),
            "shortfall_MAE": float(np.mean(q_shortfall)),
            "shortfall_RMSE": float(np.sqrt(np.mean(q_shortfall**2))),
            **{f"shortfall_{key}": value for key, value in _distribution(q_shortfall).items()},
            "semantics": "minimum_target_guardrail",
        },
        "K_policy": {
            "target_relative_APE_reported_as_primary": False,
            "primary_metrics": "absolute_error_and_fixed_span_error",
            "reason": "target-relative K error is unstable near zero",
        },
    }


def _geometry_metrics(
    target: np.ndarray,
    predicted: np.ndarray,
    *,
    requested_rows: int,
) -> dict[str, Any]:
    target = np.asarray(target, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)
    spans = np.asarray(GEOMETRY_UPPER, dtype=np.float64) - np.asarray(
        GEOMETRY_LOWER, dtype=np.float64
    )
    if target.shape != predicted.shape or target.ndim != 2 or target.shape[1] != 10:
        raise EvaluationError("geometry metric matrix shape mismatch")
    if np.any(~np.isfinite(target)) or np.any(~np.isfinite(predicted)):
        raise EvaluationError("geometry metric matrix is non-finite")
    residual = predicted - target
    absolute = np.abs(residual)
    per_feature: dict[str, Any] = {}
    for index, feature in enumerate(GEOMETRY_COLUMNS):
        per_feature[feature] = {
            "unit": "um",
            "MAE": float(np.mean(absolute[:, index])),
            "RMSE": float(np.sqrt(np.mean(residual[:, index] ** 2))),
            **_distribution(absolute[:, index]),
        }
    scaled = residual / spans[None, :]
    row_joint = np.sqrt(np.mean(scaled**2, axis=1))
    evaluated = int(target.shape[0])
    return {
        "evidence_class": "GEOMETRY_LABEL_DISTANCE_SECONDARY_NONUNIQUE_INVERSE",
        "interpretation_boundary": (
            "Distance to one recorded geometry is secondary; inverse solutions are non-unique."
        ),
        "denominator": {
            "requested_rows": int(requested_rows),
            "evaluated_rows": evaluated,
            "failed_rows": int(requested_rows - evaluated),
            "finite_rows": evaluated,
        },
        "per_feature": per_feature,
        "joint": {
            "declared_geometry_range_joint_NRMSE": float(np.sqrt(np.mean(scaled**2))),
            "declared_geometry_range_per_row": _distribution(row_joint),
            "declared_geometry_spans": [float(value) for value in spans],
        },
        "Q_guardrail": {"applicable": False},
        "K_policy": {"applicable": False},
    }


def _metric_scalars(payload: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    values: dict[str, float] = {}
    excluded = {
        "requested_rows",
        "evaluated_rows",
        "failed_rows",
        "finite_rows",
        "declared_spans",
        "fixed_spans",
        "declared_geometry_spans",
    }
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if key in excluded:
            continue
        if isinstance(value, Mapping):
            values.update(_metric_scalars(value, path))
        elif isinstance(value, bool) or value is None or isinstance(value, str):
            continue
        elif isinstance(value, (int, float)):
            numeric = float(value)
            if not math.isfinite(numeric):
                raise EvaluationError(f"non-finite metric scalar: {path}")
            values[path] = numeric
    return values


def _paired_statistics(
    records: Mapping[str, Mapping[str, Any]], estimand: str
) -> dict[str, Any]:
    per_seed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    metric_deltas: dict[str, list[float]] = {}
    for seed in EXACT_PAIRED_SEEDS:
        small = records.get(_model_key(seed, "small"))
        large = records.get(_model_key(seed, "large"))
        if not small or not large or small.get("status") != "PASS" or large.get("status") != "PASS":
            failures.append(
                {
                    "seed": seed,
                    "small_status": (small or {}).get("status", "MISSING"),
                    "large_status": (large or {}).get("status", "MISSING"),
                }
            )
            continue
        small_scalars = _metric_scalars(small["metrics"])
        large_scalars = _metric_scalars(large["metrics"])
        if set(small_scalars) != set(large_scalars):
            raise EvaluationError(f"paired metric key mismatch for {estimand}, seed {seed}")
        deltas = {
            key: float(large_scalars[key] - small_scalars[key])
            for key in sorted(small_scalars)
        }
        per_seed.append(
            {
                "seed": seed,
                "small": small_scalars,
                "large": large_scalars,
                "delta_large_minus_small": deltas,
            }
        )
        for key, value in deltas.items():
            metric_deltas.setdefault(key, []).append(value)
    summaries: dict[str, Any] = {}
    for metric, raw_values in metric_deltas.items():
        values = np.asarray(raw_values, dtype=np.float64)
        if values.size == 3:
            mean = float(np.mean(values))
            sd = float(np.std(values, ddof=1))
            margin = float(T95_DF2 * sd / math.sqrt(3.0))
            interval: list[float] | None = [mean - margin, mean + margin]
        else:
            mean = float(np.mean(values)) if values.size else None
            sd = None
            interval = None
        positive_favors_large = metric.endswith("Q_guardrail.target_met_rate")
        summaries[metric] = {
            "paired_deltas": [float(value) for value in values],
            "mean_paired_delta": mean,
            "sample_SD": sd,
            "degrees_of_freedom": 2 if values.size == 3 else None,
            "two_sided_t95_CI": interval,
            "t_critical_df2": T95_DF2 if values.size == 3 else None,
            "direction": (
                "positive_favors_large"
                if positive_favors_large
                else "negative_favors_large_for_error_metric"
            ),
        }
    return {
        "estimand": estimand,
        "paired_seed_denominator_requested": 3,
        "paired_seed_denominator_complete": len(per_seed),
        "paired_seed_denominator_failed_or_missing": len(failures),
        "failed_or_missing_pairs": failures,
        "per_seed": per_seed,
        "metric_summaries": summaries,
        "formal_three_pair_effect_complete": len(per_seed) == 3,
        "small_n_warning": (
            "Only three paired training seeds are available; df=2 intervals are descriptive "
            "training-replicate uncertainty conditional on the frozen finite data/frame contract."
        ),
    }


def _spatial_frame_seed(frame_id: str) -> int:
    if frame_id not in SPATIAL_FRAME_IDS:
        raise EvaluationError(f"unknown frozen spatial frame: {frame_id}")
    digest = hashlib.sha256(
        f"{SPATIAL_BOOTSTRAP_MASTER_SEED}:{frame_id}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _cluster_resampling_plan(
    cell_ids: Sequence[str], *, frame_id: str, replicates: int
) -> dict[str, Any]:
    if replicates <= 0 or not cell_ids:
        raise EvaluationError("spatial bootstrap has an empty frame/replicate denominator")
    cell_order = sorted(set(cell_ids))
    if not cell_order or any(not isinstance(value, str) or not value for value in cell_order):
        raise EvaluationError("spatial bootstrap has an invalid physical-cell identity")
    lookup = {value: index for index, value in enumerate(cell_order)}
    row_cell_index = np.asarray([lookup[value] for value in cell_ids], dtype=np.int64)
    cell_count = len(cell_order)
    cell_sizes = np.bincount(row_cell_index, minlength=cell_count).astype(np.int64)
    if np.any(cell_sizes <= 0):
        raise EvaluationError("spatial bootstrap contains an unobserved listed cell")
    seed = _spatial_frame_seed(frame_id)
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = rng.integers(
        0, cell_count, size=(replicates, cell_count), dtype=np.int64
    )
    draw_counts = np.zeros((replicates, cell_count), dtype=np.uint16)
    for replicate in range(replicates):
        counts = np.bincount(draws[replicate], minlength=cell_count)
        if np.any(counts > np.iinfo(np.uint16).max):
            raise EvaluationError("spatial bootstrap cell multiplicity overflow")
        draw_counts[replicate] = counts.astype(np.uint16)
    row_weights = draw_counts[:, row_cell_index]
    row_denominators = row_weights.sum(axis=1, dtype=np.int64)
    if np.any(row_denominators <= 0):
        raise EvaluationError("spatial bootstrap produced an empty row multiset")
    if np.any(row_denominators > np.iinfo(np.int32).max):
        raise EvaluationError("spatial bootstrap row multiplicity exceeds int32 audit bound")
    cell_order_bytes = "".join(f"{value}\n" for value in cell_order).encode("ascii")
    cell_order_sha = hashlib.sha256(cell_order_bytes).hexdigest()
    multiset_digest = hashlib.sha256()
    multiset_digest.update(cell_order_bytes)
    multiset_digest.update(np.asarray(cell_sizes, dtype=">u8").tobytes())
    multiset_digest.update(np.asarray(draw_counts, dtype=">u2").tobytes())
    return {
        "frame_seed": seed,
        "cell_order": cell_order,
        "cell_order_sha256": cell_order_sha,
        "cell_sizes": cell_sizes,
        "draw_counts": draw_counts,
        "row_weights": row_weights,
        "row_denominators": row_denominators,
        "resampled_row_multisets_sha256": multiset_digest.hexdigest(),
    }


def _weighted_mean_by_model(
    row_weights_float: np.ndarray,
    row_denominators_float: np.ndarray,
    values: np.ndarray,
) -> np.ndarray:
    """Return B x M x D integer-frequency-weighted means."""

    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim == 2:
        matrix = matrix[:, :, None]
    if matrix.ndim != 3:
        raise EvaluationError("weighted metric values must be M x N or M x N x D")
    model_count, row_count, dimension_count = matrix.shape
    if row_weights_float.ndim != 2 or row_weights_float.shape[1] != row_count:
        raise EvaluationError("weighted metric row denominator differs from frame")
    flattened = matrix.transpose(1, 0, 2).reshape(
        row_count, model_count * dimension_count
    )
    # ``einsum`` avoids spurious Accelerate/BLAS floating-point warnings seen
    # for tiny zero-heavy fixture matrices while retaining the same dot sum.
    means = np.einsum("bn,nk->bk", row_weights_float, flattened, optimize=True)
    means /= row_denominators_float[:, None]
    result = means.reshape(row_weights_float.shape[0], model_count, dimension_count)
    if np.any(~np.isfinite(result)):
        raise EvaluationError("weighted metric mean is non-finite")
    return result


def _integer_weight_percentiles(
    values: np.ndarray,
    row_weights: np.ndarray,
    quantiles: Sequence[float],
) -> np.ndarray:
    """Match NumPy linear percentiles on each integer-expanded row multiset."""

    vector = np.asarray(values, dtype=np.float64).reshape(-1)
    weights = np.asarray(row_weights)
    q = np.asarray(quantiles, dtype=np.float64)
    if (
        vector.size == 0
        or weights.ndim != 2
        or weights.shape[1] != vector.size
        or q.ndim != 1
        or q.size == 0
        or np.any(~np.isfinite(vector))
        or np.any(~np.isfinite(q))
        or np.any((q < 0.0) | (q > 100.0))
    ):
        raise EvaluationError("weighted percentile input shape/finite gate failed")
    order = np.argsort(vector, kind="mergesort")
    sorted_values = vector[order]
    ordered_weights = weights[:, order]
    cumulative = np.cumsum(ordered_weights, axis=1, dtype=np.int32)
    totals = cumulative[:, -1].astype(np.int64)
    if np.any(totals <= 0):
        raise EvaluationError("weighted percentile has an empty replicate")
    positions = (totals[:, None] - 1) * (q[None, :] / 100.0)
    lower_rank = np.floor(positions).astype(np.int64)
    upper_rank = np.ceil(positions).astype(np.int64)
    lower_index = np.empty_like(lower_rank)
    upper_index = np.empty_like(upper_rank)
    for replicate in range(weights.shape[0]):
        lower_index[replicate] = np.searchsorted(
            cumulative[replicate], lower_rank[replicate], side="right"
        )
        upper_index[replicate] = np.searchsorted(
            cumulative[replicate], upper_rank[replicate], side="right"
        )
    lower_values = sorted_values[lower_index]
    upper_values = sorted_values[upper_index]
    result = lower_values + (positions - lower_rank) * (upper_values - lower_values)
    if np.any(~np.isfinite(result)):
        raise EvaluationError("weighted percentile result is non-finite")
    return result


def _weighted_distribution_by_model(
    values: np.ndarray, row_weights: np.ndarray
) -> dict[str, np.ndarray]:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim == 2:
        matrix = matrix[:, :, None]
    if matrix.ndim != 3 or matrix.shape[1] != row_weights.shape[1]:
        raise EvaluationError("weighted distribution matrix differs from frame")
    quantile_names = ("P50", "P90", "P95", "P99", "Max")
    quantiles = (50.0, 90.0, 95.0, 99.0, 100.0)
    output = {
        name: np.empty(
            (row_weights.shape[0], matrix.shape[0], matrix.shape[2]),
            dtype=np.float64,
        )
        for name in quantile_names
    }
    for model_index in range(matrix.shape[0]):
        for dimension_index in range(matrix.shape[2]):
            percentiles = _integer_weight_percentiles(
                matrix[model_index, :, dimension_index], row_weights, quantiles
            )
            for quantile_index, name in enumerate(quantile_names):
                output[name][:, model_index, dimension_index] = percentiles[
                    :, quantile_index
                ]
    return output


def _response_bootstrap_scalar_matrices(
    target: np.ndarray,
    predicted: np.ndarray,
    *,
    declared_spans: np.ndarray,
    row_weights: np.ndarray,
) -> dict[str, np.ndarray]:
    target_matrix = np.asarray(target, dtype=np.float64)
    prediction_cube = np.asarray(predicted, dtype=np.float64)
    spans = np.asarray(declared_spans, dtype=np.float64)
    if (
        target_matrix.ndim != 2
        or target_matrix.shape[1] != 4
        or prediction_cube.ndim != 3
        or prediction_cube.shape[1:] != target_matrix.shape
        or prediction_cube.shape[0] != 6
        or spans.shape != (4,)
        or np.any(spans <= 0.0)
        or np.any(~np.isfinite(target_matrix))
        or np.any(~np.isfinite(prediction_cube))
    ):
        raise EvaluationError("response bootstrap input shape/finite/span gate failed")
    denominators = row_weights.sum(axis=1, dtype=np.int64)
    weights_float = row_weights.astype(np.float64)
    denominators_float = denominators.astype(np.float64)
    residual = prediction_cube - target_matrix[None, :, :]
    absolute = np.abs(residual)
    absolute_mean = _weighted_mean_by_model(
        weights_float, denominators_float, absolute
    )
    residual_square_mean = _weighted_mean_by_model(
        weights_float, denominators_float, residual**2
    )
    absolute_distribution = _weighted_distribution_by_model(absolute, row_weights)
    output: dict[str, np.ndarray] = {}
    for feature_index, feature in enumerate(FEATURE_KEYS):
        prefix = f"per_feature.{feature}"
        output[f"{prefix}.MAE"] = absolute_mean[:, :, feature_index]
        output[f"{prefix}.RMSE"] = np.sqrt(
            residual_square_mean[:, :, feature_index]
        )
        for name, values in absolute_distribution.items():
            output[f"{prefix}.{name}"] = values[:, :, feature_index]

    declared_scaled = residual / spans[None, None, :]
    fixed_scaled = residual / FIXED_RESPONSE_SPANS[None, None, :]
    q_shortfall = np.maximum(
        target_matrix[None, :, 2] - prediction_cube[:, :, 2], 0.0
    )
    engineering_scaled = fixed_scaled.copy()
    engineering_scaled[:, :, 2] = q_shortfall / FIXED_RESPONSE_SPANS[2]
    joint_records = {
        "declared_range": (
            declared_scaled,
            "declared_range_joint_NRMSE",
            "declared_range_per_row",
        ),
        "fixed_span_symmetric": (
            fixed_scaled,
            "fixed_span_symmetric_joint_NRMSE",
            "fixed_span_symmetric_per_row",
        ),
        "fixed_span_engineering": (
            engineering_scaled,
            "fixed_span_engineering_joint_error",
            "fixed_span_engineering_per_row",
        ),
    }
    for scaled, joint_name, row_name in joint_records.values():
        scaled_square = scaled**2
        component_mean = _weighted_mean_by_model(
            weights_float, denominators_float, scaled_square
        )
        output[f"joint.{joint_name}"] = np.sqrt(np.mean(component_mean, axis=2))
        per_row = np.sqrt(np.mean(scaled_square, axis=2))
        row_distribution = _weighted_distribution_by_model(per_row, row_weights)
        for name, values in row_distribution.items():
            output[f"joint.{row_name}.{name}"] = values[:, :, 0]

    target_met = prediction_cube[:, :, 2] >= target_matrix[None, :, 2]
    output["Q_guardrail.target_met_rate"] = _weighted_mean_by_model(
        weights_float, denominators_float, target_met.astype(np.float64)
    )[:, :, 0]
    shortfall_mean = _weighted_mean_by_model(
        weights_float, denominators_float, q_shortfall
    )[:, :, 0]
    shortfall_square_mean = _weighted_mean_by_model(
        weights_float, denominators_float, q_shortfall**2
    )[:, :, 0]
    output["Q_guardrail.shortfall_MAE"] = shortfall_mean
    output["Q_guardrail.shortfall_RMSE"] = np.sqrt(shortfall_square_mean)
    shortfall_distribution = _weighted_distribution_by_model(q_shortfall, row_weights)
    for name, values in shortfall_distribution.items():
        output[f"Q_guardrail.shortfall_{name}"] = values[:, :, 0]
    if any(value.shape != (row_weights.shape[0], 6) for value in output.values()):
        raise EvaluationError("response bootstrap scalar matrix shape mismatch")
    if any(np.any(~np.isfinite(value)) for value in output.values()):
        raise EvaluationError("response bootstrap scalar matrix is non-finite")
    return output


def _geometry_bootstrap_scalar_matrices(
    target: np.ndarray, predicted: np.ndarray, *, row_weights: np.ndarray
) -> dict[str, np.ndarray]:
    target_matrix = np.asarray(target, dtype=np.float64)
    prediction_cube = np.asarray(predicted, dtype=np.float64)
    if (
        target_matrix.ndim != 2
        or target_matrix.shape[1] != 10
        or prediction_cube.ndim != 3
        or prediction_cube.shape != (6, *target_matrix.shape)
        or np.any(~np.isfinite(target_matrix))
        or np.any(~np.isfinite(prediction_cube))
    ):
        raise EvaluationError("geometry bootstrap input shape/finite gate failed")
    denominators = row_weights.sum(axis=1, dtype=np.int64).astype(np.float64)
    weights_float = row_weights.astype(np.float64)
    residual = prediction_cube - target_matrix[None, :, :]
    absolute = np.abs(residual)
    absolute_mean = _weighted_mean_by_model(weights_float, denominators, absolute)
    square_mean = _weighted_mean_by_model(weights_float, denominators, residual**2)
    distribution = _weighted_distribution_by_model(absolute, row_weights)
    output: dict[str, np.ndarray] = {}
    for feature_index, feature in enumerate(GEOMETRY_COLUMNS):
        prefix = f"per_feature.{feature}"
        output[f"{prefix}.MAE"] = absolute_mean[:, :, feature_index]
        output[f"{prefix}.RMSE"] = np.sqrt(square_mean[:, :, feature_index])
        for name, values in distribution.items():
            output[f"{prefix}.{name}"] = values[:, :, feature_index]
    spans = np.asarray(GEOMETRY_UPPER, dtype=np.float64) - np.asarray(
        GEOMETRY_LOWER, dtype=np.float64
    )
    scaled_square = (residual / spans[None, None, :]) ** 2
    component_mean = _weighted_mean_by_model(
        weights_float, denominators, scaled_square
    )
    output["joint.declared_geometry_range_joint_NRMSE"] = np.sqrt(
        np.mean(component_mean, axis=2)
    )
    row_joint = np.sqrt(np.mean(scaled_square, axis=2))
    row_distribution = _weighted_distribution_by_model(row_joint, row_weights)
    for name, values in row_distribution.items():
        output[f"joint.declared_geometry_range_per_row.{name}"] = values[:, :, 0]
    if any(value.shape != (row_weights.shape[0], 6) for value in output.values()):
        raise EvaluationError("geometry bootstrap scalar matrix shape mismatch")
    if any(np.any(~np.isfinite(value)) for value in output.values()):
        raise EvaluationError("geometry bootstrap scalar matrix is non-finite")
    return output


def _bootstrap_paired_mean_deltas(
    scalar_matrices: Mapping[str, np.ndarray]
) -> dict[str, np.ndarray]:
    output: dict[str, np.ndarray] = {}
    for metric, raw in scalar_matrices.items():
        values = np.asarray(raw, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != 6 or np.any(~np.isfinite(values)):
            raise EvaluationError(f"bootstrap scalar shape/finite gate failed: {metric}")
        paired = np.stack(
            (
                values[:, 1] - values[:, 0],
                values[:, 3] - values[:, 2],
                values[:, 5] - values[:, 4],
            ),
            axis=1,
        )
        output[metric] = np.mean(paired, axis=1)
    return output


def _bootstrap_estimand_summary(
    *,
    estimand: str,
    scalar_matrices: Mapping[str, np.ndarray],
    paired_point: Mapping[str, Any],
    replicates: int,
    evidence_class: str,
) -> dict[str, Any]:
    if paired_point.get("formal_three_pair_effect_complete") is not True:
        raise EvaluationError(f"bootstrap {estimand} lacks three complete paired seeds")
    replicate_deltas = _bootstrap_paired_mean_deltas(scalar_matrices)
    point_metrics = paired_point.get("metric_summaries")
    if not isinstance(point_metrics, dict) or set(point_metrics) != set(replicate_deltas):
        raise EvaluationError(f"bootstrap {estimand} scalar keyset differs from point estimate")
    digest = hashlib.sha256()
    metric_summaries: dict[str, Any] = {}
    for metric in sorted(replicate_deltas):
        values = np.asarray(replicate_deltas[metric], dtype=np.float64)
        if values.shape != (replicates,) or np.any(~np.isfinite(values)):
            raise EvaluationError(f"bootstrap {estimand}/{metric} replicate finite gate failed")
        point = point_metrics[metric].get("mean_paired_delta")
        if not isinstance(point, (int, float)) or not math.isfinite(float(point)):
            raise EvaluationError(f"bootstrap {estimand}/{metric} point estimate is invalid")
        interval = np.percentile(values, [2.5, 97.5])
        digest.update(metric.encode("utf-8") + b"\0")
        digest.update(np.asarray(values, dtype=">f8").tobytes())
        metric_summaries[metric] = {
            "unbootstrapped_mean_of_three_paired_seed_deltas": float(point),
            "bootstrap_percentile_95_interval": [
                float(interval[0]),
                float(interval[1]),
            ],
            "lower_percentile": 2.5,
            "upper_percentile": 97.5,
            "bootstrap_replicates_requested": replicates,
            "bootstrap_replicates_finite": replicates,
            "bootstrap_replicates_failed": 0,
            "p_value_reported": False,
            "direction": point_metrics[metric]["direction"],
        }
    return {
        "status": "PASS",
        "estimand": estimand,
        "evidence_class": evidence_class,
        "paired_seed_denominator_requested": 3,
        "paired_seed_denominator_complete": 3,
        "paired_seed_denominator_failed_or_missing": 0,
        "bootstrap_replicate_statistics_sha256": digest.hexdigest(),
        "metric_summaries": metric_summaries,
    }


def _spatial_sensitivity_bootstrap(
    *,
    common_x: np.ndarray,
    common_y: np.ndarray,
    common_cell_ids: Sequence[str],
    fixed_x: np.ndarray,
    fixed_legacy: np.ndarray,
    fixed_extension: np.ndarray,
    fixed_cell_ids: Sequence[str],
    predictions_by_model: Mapping[str, Mapping[str, np.ndarray]],
    paired_effects: Mapping[str, Mapping[str, Any]],
    declared_spans: np.ndarray,
    replicates: int,
    fixture_mode: bool,
) -> dict[str, Any]:
    model_order = _expected_model_keys()
    missing_models = [key for key in model_order if key not in predictions_by_model]
    frame_specs = (
        {
            "frame_id": "common_real_emx_holdout_902",
            "target_response": common_x,
            "target_geometry": common_y,
            "cell_ids": list(common_cell_ids),
            "estimands": (
                (
                    "common_forward_primary",
                    "response",
                    "common_forward_primary",
                    "COMMON_HISTORICAL_REAL_EMX_HOLDOUT_FORWARD_LABEL_ERROR",
                ),
                (
                    "common_inverse_own_forward_secondary",
                    "response",
                    "common_inverse_own_forward_secondary",
                    "OWN_FORWARD_TANDEM_PROXY_SECONDARY_NONUNIQUE_INVERSE",
                ),
                (
                    "common_inverse_geometry_label_secondary",
                    "geometry",
                    "common_inverse_geometry_label_secondary",
                    "GEOMETRY_LABEL_DISTANCE_SECONDARY_NONUNIQUE_INVERSE",
                ),
            ),
        },
        {
            "frame_id": "fixed_target_full10k",
            "target_response": fixed_x,
            "target_geometry": None,
            "cell_ids": list(fixed_cell_ids),
            "estimands": (
                (
                    "fixed10k_full10k",
                    "response",
                    "fixed10k_own_forward_proxy",
                    "OWN_FORWARD_TANDEM_PROXY_FIXED_FINITE_FRAME_NOT_FRESH_EMX",
                ),
            ),
        },
        {
            "frame_id": "fixed_target_legacy_K_abs_le_0p8_8000",
            "target_response": fixed_x[fixed_legacy],
            "target_geometry": None,
            "cell_ids": [
                cell for cell, selected in zip(fixed_cell_ids, fixed_legacy) if selected
            ],
            "estimands": (
                (
                    "fixed10k_legacy8000",
                    "response",
                    "fixed10k_own_forward_proxy",
                    "OWN_FORWARD_TANDEM_PROXY_FIXED_FINITE_FRAME_NOT_FRESH_EMX",
                ),
            ),
            "prediction_mask": fixed_legacy,
        },
        {
            "frame_id": "fixed_target_highK_K_abs_gt_0p8_2000",
            "target_response": fixed_x[fixed_extension],
            "target_geometry": None,
            "cell_ids": [
                cell
                for cell, selected in zip(fixed_cell_ids, fixed_extension)
                if selected
            ],
            "estimands": (
                (
                    "fixed10k_highK2000",
                    "response",
                    "fixed10k_own_forward_proxy",
                    "OWN_FORWARD_TANDEM_PROXY_OUT_OF_SUPPORT_STRESS_NOT_FRESH_EMX",
                ),
            ),
            "prediction_mask": fixed_extension,
        },
    )
    frames: dict[str, Any] = {}
    if missing_models:
        for spec in frame_specs:
            frames[spec["frame_id"]] = {
                "status": "FAIL_MISSING_MODEL_ARM_RETAINED_DENOMINATOR",
                "original_row_denominator": len(spec["cell_ids"]),
                "missing_model_arms": missing_models,
                "bootstrap_replicates_requested": replicates,
                "bootstrap_replicates_complete": 0,
                "bootstrap_replicates_failed": replicates,
            }
        return {
            "schema": SPATIAL_SENSITIVITY_SCHEMA,
            "status": "FAIL_INCOMPLETE_SIX_ARM_DENOMINATOR",
            "fixture_only": fixture_mode,
            "preregistration_addendum_sha256": PREREGISTRATION_ADDENDUM_SHA256,
            "frames_requested": 4,
            "frames_complete": 0,
            "frames_failed": 4,
            "frames": frames,
        }

    for spec in frame_specs:
        frame_id = spec["frame_id"]
        target_response = np.asarray(spec["target_response"], dtype=np.float64)
        target_geometry = (
            None
            if spec["target_geometry"] is None
            else np.asarray(spec["target_geometry"], dtype=np.float64)
        )
        if target_response.shape[0] == 0 or len(spec["cell_ids"]) != target_response.shape[0]:
            raise EvaluationError(f"spatial bootstrap {frame_id} frame is empty/misaligned")
        plan = _cluster_resampling_plan(
            spec["cell_ids"], frame_id=frame_id, replicates=replicates
        )
        row_weights = plan["row_weights"]
        estimand_summaries: dict[str, Any] = {}
        for estimand, kind, prediction_key, evidence_class in spec["estimands"]:
            prediction_mask = spec.get("prediction_mask")
            stacked = np.stack(
                [
                    (
                        np.asarray(
                            predictions_by_model[key][prediction_key], dtype=np.float64
                        )[prediction_mask]
                        if prediction_mask is not None
                        else np.asarray(
                            predictions_by_model[key][prediction_key], dtype=np.float64
                        )
                    )
                    for key in model_order
                ],
                axis=0,
            )
            if kind == "response":
                scalar_matrices = _response_bootstrap_scalar_matrices(
                    target_response,
                    stacked,
                    declared_spans=declared_spans,
                    row_weights=row_weights,
                )
            elif kind == "geometry" and target_geometry is not None:
                scalar_matrices = _geometry_bootstrap_scalar_matrices(
                    target_geometry, stacked, row_weights=row_weights
                )
            else:
                raise EvaluationError(f"spatial bootstrap {estimand} kind is invalid")
            estimand_summaries[estimand] = _bootstrap_estimand_summary(
                estimand=estimand,
                scalar_matrices=scalar_matrices,
                paired_point=paired_effects[estimand],
                replicates=replicates,
                evidence_class=evidence_class,
            )
        row_denominators = plan["row_denominators"]
        cell_sizes = plan["cell_sizes"]
        frames[frame_id] = {
            "status": "PASS",
            "frame_seed": plan["frame_seed"],
            "random_generator": "numpy.random.Generator(PCG64)",
            "original_row_denominator": int(target_response.shape[0]),
            "distinct_observed_physical_cell_denominator": int(len(plan["cell_order"])),
            "observed_cell_draws_per_replicate": int(len(plan["cell_order"])),
            "bootstrap_replicates_requested": replicates,
            "bootstrap_replicates_complete": replicates,
            "bootstrap_replicates_failed": 0,
            "resampled_row_denominator": {
                "Min": int(np.min(row_denominators)),
                "P50": float(np.percentile(row_denominators, 50.0)),
                "Max": int(np.max(row_denominators)),
            },
            "observed_cell_row_denominator": {
                "Min": int(np.min(cell_sizes)),
                "P50": float(np.percentile(cell_sizes, 50.0)),
                "Max": int(np.max(cell_sizes)),
            },
            "physical_cell_order_sha256": plan["cell_order_sha256"],
            "resampled_row_multisets_sha256": plan[
                "resampled_row_multisets_sha256"
            ],
            "cross_model_row_multiset": (
                "identical for both arms and all three paired seeds within every replicate"
            ),
            "estimands": estimand_summaries,
        }
    return {
        "schema": SPATIAL_SENSITIVITY_SCHEMA,
        "status": "PASS_FINITE_FRAME_SPATIAL_COMPOSITION_SENSITIVITY",
        "fixture_only": fixture_mode,
        "preregistration_addendum_sha256": PREREGISTRATION_ADDENDUM_SHA256,
        "method": "physical_cell_cluster_bootstrap_on_each_frozen_finite_frame",
        "master_seed": SPATIAL_BOOTSTRAP_MASTER_SEED,
        "bootstrap_replicates": replicates,
        "frames_requested": 4,
        "frames_complete": 4,
        "frames_failed": 0,
        "interpretation_boundary": {
            "meaning": "finite-frame spatial-composition sensitivity conditional on the six frozen trained models",
            "not_training_seed_uncertainty": True,
            "not_deployment_population_uncertainty": True,
            "not_fresh_emx_evidence": True,
            "not_a_substitute_for_df2_paired_seed_interval": True,
            "p_values_reported": False,
        },
        "highK_clustering_rule": (
            "min(K_abs,0.8) for clustering only; original unclipped K_abs retained for all metrics"
        ),
        "frames": frames,
    }


COMMON_CSV_FIELDS = [
    "model_key",
    "arm",
    "seed",
    "model_summary_sha256",
    "model_weights_sha256",
    "row_index",
    "geometry_identity_sha256",
    "touchstone_sha256",
    "physical_cell_4d",
    "K_abs_capped_to_0p8_for_clustering_only",
    "primary_evidence_class",
    "secondary_proxy_evidence_class",
    "secondary_geometry_evidence_class",
    *[f"true__{key}" for key in FEATURE_KEYS],
    *[f"label_geometry__{key}" for key in GEOMETRY_COLUMNS],
    *[f"forward_label_prediction__{key}" for key in FEATURE_KEYS],
    *[f"forward_label_signed_error__{key}" for key in FEATURE_KEYS],
    *[f"forward_label_absolute_error__{key}" for key in FEATURE_KEYS],
    *[f"inverse_geometry__{key}" for key in GEOMETRY_COLUMNS],
    *[f"geometry_label_absolute_error__{key}" for key in GEOMETRY_COLUMNS],
    *[f"own_forward_proxy__{key}" for key in FEATURE_KEYS],
    *[f"own_forward_signed_error__{key}" for key in FEATURE_KEYS],
    *[f"own_forward_absolute_error__{key}" for key in FEATURE_KEYS],
    "forward_q_target_met",
    "forward_q_shortfall",
    "own_forward_q_target_met",
    "own_forward_q_shortfall",
    "forward_declared_range_joint",
    "forward_fixed_span_engineering_joint",
    "own_forward_declared_range_joint",
    "own_forward_fixed_span_engineering_joint",
]

FIXED_CSV_FIELDS = [
    "model_key",
    "arm",
    "seed",
    "model_summary_sha256",
    "model_weights_sha256",
    "row_index",
    "target_id",
    "panel",
    "physical_cell_4d",
    "K_abs_capped_to_0p8_for_clustering_only",
    "evidence_class",
    *[f"target__{key}" for key in FEATURE_KEYS],
    *[f"inverse_geometry__{key}" for key in GEOMETRY_COLUMNS],
    *[f"own_forward_proxy__{key}" for key in FEATURE_KEYS],
    *[f"signed_error__{key}" for key in FEATURE_KEYS],
    *[f"absolute_error__{key}" for key in FEATURE_KEYS],
    "q_target_met",
    "q_shortfall",
    "declared_range_joint",
    "fixed_span_engineering_joint",
]

if len(COMMON_CSV_FIELDS) != len(set(COMMON_CSV_FIELDS)) or len(
    FIXED_CSV_FIELDS
) != len(set(FIXED_CSV_FIELDS)):
    raise RuntimeError("evaluator CSV field contracts must contain exact unique names")


def _joint_rows(
    target: np.ndarray, predicted: np.ndarray, declared_spans: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    residual = predicted - target
    declared = np.sqrt(np.mean((residual / declared_spans[None, :]) ** 2, axis=1))
    engineering = residual / FIXED_RESPONSE_SPANS[None, :]
    engineering[:, 2] = np.maximum(target[:, 2] - predicted[:, 2], 0.0) / FIXED_RESPONSE_SPANS[2]
    return declared, np.sqrt(np.mean(engineering**2, axis=1))


def _evaluate_one_model(
    *,
    model_record: dict[str, Any],
    model_bytes: bytes,
    trainer: ModuleType,
    common_rows: list[dict[str, str]],
    common_x: np.ndarray,
    common_y: np.ndarray,
    common_ids: list[str],
    common_cell_ids: list[str],
    common_cell_capped: np.ndarray,
    fixed_ids: list[str],
    fixed_x: np.ndarray,
    fixed_legacy: np.ndarray,
    fixed_extension: np.ndarray,
    fixed_cell_ids: list[str],
    fixed_cell_capped: np.ndarray,
    declared_spans: np.ndarray,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, np.ndarray],
]:
    model = _load_model(model_bytes, model_record["weights"]["sha256"])
    common_y_norm = (common_y - model["y_mean"][None, :]) / model["y_scale"][None, :]
    common_x_norm = (common_x - model["x_mean"][None, :]) / model["x_scale"][None, :]
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        forward_common_norm = trainer._predict(
            common_y_norm, model["forward_weights"], model["forward_biases"]
        )
        inverse_common_norm = trainer._predict_inverse(
            common_x_norm,
            model["inverse_weights"],
            model["inverse_biases"],
            model["geometry_lower"],
            model["geometry_upper"],
        )
        proxy_common_norm = trainer._predict(
            inverse_common_norm, model["forward_weights"], model["forward_biases"]
        )
        fixed_x_norm = (fixed_x - model["x_mean"][None, :]) / model["x_scale"][None, :]
        inverse_fixed_norm = trainer._predict_inverse(
            fixed_x_norm,
            model["inverse_weights"],
            model["inverse_biases"],
            model["geometry_lower"],
            model["geometry_upper"],
        )
        proxy_fixed_norm = trainer._predict(
            inverse_fixed_norm, model["forward_weights"], model["forward_biases"]
        )
    forward_common = forward_common_norm * model["x_scale"][None, :] + model["x_mean"][None, :]
    inverse_common = inverse_common_norm * model["y_scale"][None, :] + model["y_mean"][None, :]
    proxy_common = proxy_common_norm * model["x_scale"][None, :] + model["x_mean"][None, :]
    inverse_fixed = inverse_fixed_norm * model["y_scale"][None, :] + model["y_mean"][None, :]
    proxy_fixed = proxy_fixed_norm * model["x_scale"][None, :] + model["x_mean"][None, :]
    numerical = {
        "forward_common": forward_common,
        "inverse_common": inverse_common,
        "proxy_common": proxy_common,
        "inverse_fixed": inverse_fixed,
        "proxy_fixed": proxy_fixed,
    }
    for label, values in numerical.items():
        if np.any(~np.isfinite(values)):
            raise EvaluationError(f"{model_record['seed']}/{model_record['arm']} {label} is non-finite")
    if np.any(inverse_common_norm < model["geometry_lower"][None, :] - 1e-12) or np.any(
        inverse_common_norm > model["geometry_upper"][None, :] + 1e-12
    ):
        raise EvaluationError("common inverse geometry escaped decoder envelope")
    if np.any(inverse_fixed_norm < model["geometry_lower"][None, :] - 1e-12) or np.any(
        inverse_fixed_norm > model["geometry_upper"][None, :] + 1e-12
    ):
        raise EvaluationError("fixed inverse geometry escaped decoder envelope")

    key = _model_key(model_record["seed"], model_record["arm"])
    forward_declared, forward_engineering = _joint_rows(common_x, forward_common, declared_spans)
    proxy_declared, proxy_engineering = _joint_rows(common_x, proxy_common, declared_spans)
    common_output_rows: list[dict[str, Any]] = []
    for index, (source, identity) in enumerate(zip(common_rows, common_ids)):
        row: dict[str, Any] = {
            "model_key": key,
            "arm": model_record["arm"],
            "seed": model_record["seed"],
            "model_summary_sha256": model_record["summary"]["sha256"],
            "model_weights_sha256": model_record["weights"]["sha256"],
            "row_index": index,
            "geometry_identity_sha256": identity,
            "touchstone_sha256": source["touchstone_sha256"],
            "physical_cell_4d": common_cell_ids[index],
            "K_abs_capped_to_0p8_for_clustering_only": bool(
                common_cell_capped[index]
            ),
            "primary_evidence_class": "COMMON_HISTORICAL_REAL_EMX_HOLDOUT_FORWARD_LABEL_ERROR",
            "secondary_proxy_evidence_class": "OWN_FORWARD_TANDEM_PROXY_SECONDARY_NONUNIQUE_INVERSE",
            "secondary_geometry_evidence_class": "GEOMETRY_LABEL_DISTANCE_SECONDARY_NONUNIQUE_INVERSE",
        }
        for feature_index, feature in enumerate(FEATURE_KEYS):
            row[f"true__{feature}"] = float(common_x[index, feature_index])
            row[f"forward_label_prediction__{feature}"] = float(forward_common[index, feature_index])
            row[f"forward_label_signed_error__{feature}"] = float(forward_common[index, feature_index] - common_x[index, feature_index])
            row[f"forward_label_absolute_error__{feature}"] = abs(row[f"forward_label_signed_error__{feature}"])
            row[f"own_forward_proxy__{feature}"] = float(proxy_common[index, feature_index])
            row[f"own_forward_signed_error__{feature}"] = float(proxy_common[index, feature_index] - common_x[index, feature_index])
            row[f"own_forward_absolute_error__{feature}"] = abs(row[f"own_forward_signed_error__{feature}"])
        for geometry_index, geometry in enumerate(GEOMETRY_COLUMNS):
            row[f"label_geometry__{geometry}"] = float(common_y[index, geometry_index])
            row[f"inverse_geometry__{geometry}"] = float(inverse_common[index, geometry_index])
            row[f"geometry_label_absolute_error__{geometry}"] = float(abs(inverse_common[index, geometry_index] - common_y[index, geometry_index]))
        row.update(
            {
                "forward_q_target_met": bool(forward_common[index, 2] >= common_x[index, 2]),
                "forward_q_shortfall": float(max(common_x[index, 2] - forward_common[index, 2], 0.0)),
                "own_forward_q_target_met": bool(proxy_common[index, 2] >= common_x[index, 2]),
                "own_forward_q_shortfall": float(max(common_x[index, 2] - proxy_common[index, 2], 0.0)),
                "forward_declared_range_joint": float(forward_declared[index]),
                "forward_fixed_span_engineering_joint": float(forward_engineering[index]),
                "own_forward_declared_range_joint": float(proxy_declared[index]),
                "own_forward_fixed_span_engineering_joint": float(proxy_engineering[index]),
            }
        )
        common_output_rows.append(row)

    fixed_declared, fixed_engineering = _joint_rows(fixed_x, proxy_fixed, declared_spans)
    fixed_output_rows: list[dict[str, Any]] = []
    for index, target_id in enumerate(fixed_ids):
        panel = "legacy_K_abs_le_0p8" if fixed_legacy[index] else "highK_K_abs_gt_0p8"
        row = {
            "model_key": key,
            "arm": model_record["arm"],
            "seed": model_record["seed"],
            "model_summary_sha256": model_record["summary"]["sha256"],
            "model_weights_sha256": model_record["weights"]["sha256"],
            "row_index": index,
            "target_id": target_id,
            "panel": panel,
            "physical_cell_4d": fixed_cell_ids[index],
            "K_abs_capped_to_0p8_for_clustering_only": bool(
                fixed_cell_capped[index]
            ),
            "evidence_class": "OWN_FORWARD_TANDEM_PROXY_FIXED_FINITE_FRAME_NOT_FRESH_EMX",
        }
        for feature_index, feature in enumerate(FEATURE_KEYS):
            row[f"target__{feature}"] = float(fixed_x[index, feature_index])
            row[f"own_forward_proxy__{feature}"] = float(proxy_fixed[index, feature_index])
            row[f"signed_error__{feature}"] = float(proxy_fixed[index, feature_index] - fixed_x[index, feature_index])
            row[f"absolute_error__{feature}"] = abs(row[f"signed_error__{feature}"])
        for geometry_index, geometry in enumerate(GEOMETRY_COLUMNS):
            row[f"inverse_geometry__{geometry}"] = float(inverse_fixed[index, geometry_index])
        row.update(
            {
                "q_target_met": bool(proxy_fixed[index, 2] >= fixed_x[index, 2]),
                "q_shortfall": float(max(fixed_x[index, 2] - proxy_fixed[index, 2], 0.0)),
                "declared_range_joint": float(fixed_declared[index]),
                "fixed_span_engineering_joint": float(fixed_engineering[index]),
            }
        )
        fixed_output_rows.append(row)

    full = np.ones(fixed_x.shape[0], dtype=bool)
    fixed_metrics = {
        "full10k": _response_metrics(
            fixed_x,
            proxy_fixed,
            declared_spans=declared_spans,
            evidence_class="OWN_FORWARD_TANDEM_PROXY_FIXED_FINITE_FRAME_NOT_FRESH_EMX",
            requested_rows=int(fixed_x.shape[0]),
        ),
        "legacy8000": _response_metrics(
            fixed_x[fixed_legacy],
            proxy_fixed[fixed_legacy],
            declared_spans=declared_spans,
            evidence_class="OWN_FORWARD_TANDEM_PROXY_FIXED_FINITE_FRAME_NOT_FRESH_EMX",
            requested_rows=int(np.sum(fixed_legacy)),
        ),
        "highK2000": _response_metrics(
            fixed_x[fixed_extension],
            proxy_fixed[fixed_extension],
            declared_spans=declared_spans,
            evidence_class="OWN_FORWARD_TANDEM_PROXY_OUT_OF_SUPPORT_STRESS_NOT_FRESH_EMX",
            requested_rows=int(np.sum(fixed_extension)),
        ),
    }
    assert np.all(full)  # documents deliberate full finite-frame denominator
    metrics = {
        "status": "PASS",
        "model_key": key,
        "arm": model_record["arm"],
        "seed": model_record["seed"],
        "common_forward_primary": _response_metrics(
            common_x,
            forward_common,
            declared_spans=declared_spans,
            evidence_class="COMMON_HISTORICAL_REAL_EMX_HOLDOUT_FORWARD_LABEL_ERROR",
            requested_rows=int(common_x.shape[0]),
        ),
        "common_inverse_own_forward_secondary": _response_metrics(
            common_x,
            proxy_common,
            declared_spans=declared_spans,
            evidence_class="OWN_FORWARD_TANDEM_PROXY_SECONDARY_NONUNIQUE_INVERSE",
            requested_rows=int(common_x.shape[0]),
        ),
        "common_inverse_geometry_label_secondary": _geometry_metrics(
            common_y, inverse_common, requested_rows=int(common_y.shape[0])
        ),
        "fixed10k_own_forward_proxy": fixed_metrics,
    }
    bootstrap_predictions = {
        "common_forward_primary": forward_common,
        "common_inverse_own_forward_secondary": proxy_common,
        "common_inverse_geometry_label_secondary": inverse_common,
        "fixed10k_own_forward_proxy": proxy_fixed,
    }
    return metrics, common_output_rows, fixed_output_rows, bootstrap_predictions


def _assert_execute_arguments_match_contract(
    args: argparse.Namespace, contract: Mapping[str, Any]
) -> None:
    if contract.get("schema") != "controlled_real10k_20k_common_release_contract_v1":
        raise EvaluationError("prepared release-contract schema differs")
    bindings = contract.get("release_bindings")
    if not isinstance(bindings, dict) or contract.get(
        "release_contract_sha256"
    ) != _canonical_sha(bindings):
        raise EvaluationError("prepared release-contract canonical SHA differs")
    paths = contract.get("paths")
    if not isinstance(paths, dict):
        raise EvaluationError("prepared release contract lacks exact paths")
    path_arguments = {
        "preregistration_addendum": "preregistration_addendum",
        "materialization_summary": "materialization_summary",
        "common_holdout": "common_holdout",
        "fixed_normalization": "fixed_normalization",
        "six_arm_terminal_manifest": "six_arm_terminal_manifest",
        "fixed_targets_json": "fixed_targets",
        "trainer_source": "trainer_source",
    }
    for argument, path_key in path_arguments.items():
        if str(_absolute_lexical(getattr(args, argument))) != paths.get(path_key):
            raise EvaluationError(f"execute argument {argument} differs from prepared path")
    if str(_absolute_lexical(args.out_dir)) != bindings.get("evaluation_output_root"):
        raise EvaluationError("execute output root differs from prepared release contract")
    expected_shas = {
        "expected_preregistration_addendum_sha256": "preregistration_addendum_v1_2_sha256",
        "expected_materialization_summary_sha256": "materialization_summary_sha256",
        "expected_common_holdout_sha256": "common_holdout_sha256",
        "expected_fixed_normalization_sha256": "fixed_normalization_sha256",
        "expected_six_arm_terminal_manifest_sha256": "six_arm_terminal_manifest_sha256",
        "expected_fixed_targets_sha256": "fixed10k_sha256",
        "expected_trainer_sha256": "trainer_source_sha256",
    }
    for argument, binding_key in expected_shas.items():
        if getattr(args, argument) != bindings.get(binding_key):
            raise EvaluationError(f"execute expected SHA {argument} differs from prepared")
    denominators = contract.get("denominators") or {}
    exact_counts = {
        "expected_common_test_rows": "common_real_emx_holdout_rows",
        "expected_fixed_rows": "fixed10k_full_rows",
        "expected_legacy_rows": "fixed10k_legacy_rows",
        "expected_extension_rows": "fixed10k_extension_rows",
    }
    if any(
        int(getattr(args, argument)) != denominators.get(key)
        for argument, key in exact_counts.items()
    ):
        raise EvaluationError("execute denominators differ from prepared release contract")
    spatial = (contract.get("scientific_contract") or {}).get("spatial_sensitivity") or {}
    if (
        bool(args.fixture_mode) is not bool(contract.get("fixture_only"))
        or int(args.bootstrap_replicates) != spatial.get("replicates")
    ):
        raise EvaluationError("execute fixture/bootstrap contract differs from prepared")


def _assert_prepared_snapshots_unchanged(
    root_descriptor: int, snapshots: Mapping[str, bytes]
) -> None:
    if set(os.listdir(root_descriptor)) != PREPARE_FILE_NAMES:
        raise EvaluationError("prepared filesystem changed before release claim")
    for name, expected in snapshots.items():
        observed = _read_bytes_at(
            root_descriptor, name, f"prepared file {name} before claim"
        )[0]
        if observed != expected:
            raise EvaluationError(f"prepared file {name} changed before release claim")


def _open_prepared_release_lease(
    expected_raw: Any,
    *,
    challenge_nonce: str,
    root_identity: Mapping[str, Any],
    parent_descriptor: int,
    out_dir: Path,
) -> tuple[int, dict[str, Any]]:
    expected = _validate_lease_identity(
        expected_raw, "prepared one-time release lease"
    )
    path = Path(expected["path"])
    if path.parent != out_dir.parent or path == out_dir:
        raise EvaluationError("one-time release lease is not external in output parent")
    flags = (
        os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise EvaluationError(f"cannot open prepared one-time release lease: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        payload = b"".join(blocks)
        after = os.fstat(descriptor)
        if any(
            getattr(before, field) != getattr(after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mode",
                "st_nlink",
                "st_ctime_ns",
            )
        ):
            raise EvaluationError("one-time release lease changed during held read")
        observed = _lease_identity_from_descriptor(
            descriptor, path, "PREPARED", payload
        )
        _require_exact_json_equal(
            observed, expected, "prepared one-time release lease identity"
        )
        wanted_payload = _lease_state_bytes(
            "PREPARED", challenge_nonce, root_identity
        )
        if payload != wanted_payload:
            raise EvaluationError("one-time release lease prepared bytes differ")
        return descriptor, expected
    except Exception:
        os.close(descriptor)
        raise


def _consume_release_lease(
    descriptor: int,
    expected: Mapping[str, Any],
    *,
    challenge_nonce: str,
    root_identity: Mapping[str, Any],
    parent_descriptor: int,
) -> dict[str, Any]:
    consumed = _lease_state_bytes("CONSUMED", challenge_nonce, root_identity)
    if len(consumed) != expected["size_bytes"]:
        raise EvaluationError("one-time release lease state width differs")
    os.lseek(descriptor, 0, os.SEEK_SET)
    view = memoryview(consumed)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise EvaluationError("short write consuming one-time release lease")
        view = view[written:]
    os.ftruncate(descriptor, len(consumed))
    os.fchmod(descriptor, FINAL_FILE_MODE)
    os.fsync(descriptor)
    os.fsync(parent_descriptor)
    observed = _lease_identity_from_descriptor(
        descriptor, Path(str(expected["path"])), "CONSUMED", consumed
    )
    if (
        observed["device"] != expected["device"]
        or observed["inode"] != expected["inode"]
        or observed["nlink"] != 1
        or observed["mode"] != f"{FINAL_FILE_MODE:04o}"
        or observed["sha256"] == expected["sha256"]
    ):
        raise EvaluationError("one-time release lease did not consume irreversibly")
    return observed


def _assert_prepared_release_lease_unchanged(
    descriptor: int, expected: Mapping[str, Any]
) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    blocks: list[bytes] = []
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        blocks.append(block)
    observed = _lease_identity_from_descriptor(
        descriptor,
        Path(str(expected["path"])),
        "PREPARED",
        b"".join(blocks),
    )
    _require_exact_json_equal(
        observed, expected, "prepared one-time release lease changed before claim"
    )


def _release_input_records(contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    bindings = contract.get("release_bindings") or {}
    records = bindings.get("consumed_inputs")
    if not isinstance(records, dict):
        raise EvaluationError("prepared release contract lacks consumed-input identities")
    runtime = bindings.get("numerical_runtime") or {}
    runtime_files = runtime.get("files") if isinstance(runtime, dict) else None
    if not isinstance(runtime_files, dict):
        raise EvaluationError("prepared release runtime file bindings are missing")
    expected_roles = {
        "evaluator_source",
        "shared_scientific_contract",
        "trainer_source",
        "preregistration_addendum_v1_2",
        "materialization_summary",
        "common_source_csv",
        "common_holdout",
        "fixed_normalization",
        "fixed_targets",
        "six_arm_terminal_manifest",
        "paired_run_contract",
        "paired_runner",
        "paired_final_artifact_manifest",
        "paired_final_sha_index",
        *(f"runtime__{role}" for role in runtime_files),
    } | {
        f"model_{kind}__{key}"
        for key in _expected_model_keys()
        for kind in ("summary", "weights")
    } | {
        f"pair_receipt__seed_{seed}" for seed in EXACT_PAIRED_SEEDS
    }
    if "controlled_singleton" in bindings:
        expected_roles.add("controlled_singleton_lock")
    if set(records) != expected_roles:
        raise EvaluationError(
            "prepared release consumed-input role closure is not exact"
        )
    audited = {
        role: (
            _validate_controlled_singleton_identity(
                record, f"prepared release input {role}"
            )
            if role == "controlled_singleton_lock"
            else _validate_pinned_identity(
                record, f"prepared release input {role}"
            )
        )
        for role, record in records.items()
    }
    if any(
        audited[f"runtime__{role}"]
        != _validate_pinned_identity(record, f"prepared runtime {role}")
        for role, record in runtime_files.items()
    ):
        raise EvaluationError("prepared runtime/consumed-input bindings disagree")
    if audited["shared_scientific_contract"] != _validate_pinned_identity(
        bindings.get("shared_scientific_contract"),
        "prepared shared scientific contract",
    ):
        raise EvaluationError("prepared shared-contract duplicate bindings disagree")
    if "controlled_singleton" in bindings and audited[
        "controlled_singleton_lock"
    ] != _validate_controlled_singleton_identity(
        bindings.get("controlled_singleton"), "prepared controlled singleton"
    ):
        raise EvaluationError("prepared controlled-singleton duplicate bindings disagree")
    models = contract.get("models") or {}
    if not isinstance(models, dict) or set(models) != set(_expected_model_keys()):
        raise EvaluationError("prepared release model set/order differs")
    for key, model in models.items():
        if (
            audited[f"model_summary__{key}"]["path"]
            != model.get("summary", {}).get("path")
            or audited[f"model_summary__{key}"]["sha256"]
            != model.get("summary", {}).get("sha256")
            or audited[f"model_weights__{key}"]["path"]
            != model.get("weights", {}).get("path")
            or audited[f"model_weights__{key}"]["sha256"]
            != model.get("weights", {}).get("sha256")
        ):
            raise EvaluationError(f"prepared model binding disagrees for {key}")
    return audited


def _verify_paired_shared_contract_snapshot(
    run_contract_bytes: bytes,
    shared_identity: Mapping[str, Any],
    *,
    evaluator_runtime: Mapping[str, Any],
    paired_runtime: Mapping[str, Any] | None,
    paired_shared_contract: Mapping[str, Any],
    controlled_singleton: Mapping[str, Any] | None,
    fixture_mode: bool,
) -> None:
    payload = _json_from_bytes(run_contract_bytes, "paired run contract")
    expected_schema = (
        LEGACY_FIXTURE_PAIRED_RUN_CONTRACT_SCHEMA
        if fixture_mode
        else PAIRED_RUN_CONTRACT_SCHEMA
    )
    if payload.get("schema") != expected_schema:
        raise EvaluationError("held paired run-contract schema differs")
    shared = payload.get("shared_contract")
    expected = _validate_pinned_identity(
        shared_identity, "release shared scientific contract"
    )
    if fixture_mode:
        if not isinstance(shared, dict) or set(shared) - {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise EvaluationError("held fixture paired shared binding is malformed")
        if (
            shared.get("path") != expected["path"]
            or shared.get("sha256") != expected["sha256"]
        ):
            raise EvaluationError(
                "held fixture paired contract does not bind imported shared bytes"
            )
    else:
        live_shared = _audit_paired_shared_member(
            shared, evaluator_runtime, expected["sha256"]
        )
        _require_exact_json_equal(
            live_shared,
            paired_shared_contract,
            "held/prepared paired shared member binding",
        )
        live_runtime = _audit_paired_runtime_identity(
            payload.get("runtime"), evaluator_runtime
        )
        _require_exact_json_equal(
            live_runtime,
            paired_runtime,
            "held/prepared paired runtime binding",
        )
        if controlled_singleton is None:
            raise EvaluationError("prepared production singleton binding is missing")
        _audit_paired_controlled_singleton(
            payload.get("controlled_singleton"), controlled_singleton
        )
    _audit_trainer_launch_contract(
        (payload.get("process_contract") or {}).get("trainer_launch"),
        "held paired run-contract trainer launch",
    )


def _freeze_exact_held_closure(
    root_descriptor: int,
    parent_descriptor: int,
    *,
    expected_names: set[str],
    index_name: str,
    label: str,
) -> None:
    names = set(os.listdir(root_descriptor))
    if names != expected_names:
        raise EvaluationError(f"{label} filesystem closure differs: {sorted(names)}")
    _verify_index_at(
        root_descriptor, index_name, expected_names - {index_name}
    )
    for name in sorted(names):
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(name, flags, dir_fd=root_descriptor)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise EvaluationError(f"{label} output {name} nlink/type differs")
            os.fchmod(descriptor, FINAL_FILE_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    os.fchmod(root_descriptor, FINAL_DIRECTORY_MODE)
    _durable_held_directories(root_descriptor, parent_descriptor)
    if set(os.listdir(root_descriptor)) != expected_names:
        raise EvaluationError(f"{label} filesystem changed during freeze")
    for name in sorted(expected_names):
        metadata = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != FINAL_FILE_MODE
            or metadata.st_nlink != 1
        ):
            raise EvaluationError(f"{label} output {name} freeze verification failed")
    root_metadata = os.fstat(root_descriptor)
    if stat.S_IMODE(root_metadata.st_mode) != FINAL_DIRECTORY_MODE:
        raise EvaluationError(f"{label} directory freeze verification failed")
    _verify_index_at(
        root_descriptor, index_name, expected_names - {index_name}
    )


def _freeze_and_verify_final_output(
    out_dir: Path,
    root_descriptor: int,
    parent_descriptor: int,
    root_identity: Mapping[str, Any],
) -> dict[str, Any]:
    _assert_held_root_matches(root_descriptor, out_dir, root_identity)
    _freeze_exact_held_closure(
        root_descriptor,
        parent_descriptor,
        expected_names=FINAL_FILE_NAMES,
        index_name=RESULT_INDEX_NAME,
        label="final evaluation",
    )
    _assert_held_root_matches(
        root_descriptor,
        out_dir,
        {**dict(root_identity), "mode": f"{FINAL_DIRECTORY_MODE:04o}"},
    )
    return {
        "root": str(out_dir),
        "regular_files_exact": sorted(FINAL_FILE_NAMES),
        "regular_file_count": len(FINAL_FILE_NAMES),
        "files_mode": f"{FINAL_FILE_MODE:04o}",
        "directory_mode": f"{FINAL_DIRECTORY_MODE:04o}",
        "nlink_each": 1,
        "sha256_index": {
            "path": str(out_dir / RESULT_INDEX_NAME),
            "sha256": _sha256_at(root_descriptor, RESULT_INDEX_NAME),
            "self_hash_included": False,
        },
        "file_fsync": True,
        "directory_fsync": True,
        "parent_directory_fsync": True,
    }


def _terminalize_post_claim_failure(
    execution: dict[str, Any], exc: BaseException
) -> None:
    root_descriptor = execution["root_descriptor"]
    parent_descriptor = execution["parent_descriptor"]
    out_dir = execution["out_dir"]
    os.fchmod(root_descriptor, 0o755)
    os.fsync(root_descriptor)
    initial_names = set(os.listdir(root_descriptor))
    if FATAL_FAIL_NAME in initial_names or FAILURE_INDEX_NAME in initial_names:
        raise EvaluationError("post-claim failure closure already exists")
    planned_names = initial_names | {FATAL_FAIL_NAME, FAILURE_INDEX_NAME}
    available: dict[str, dict[str, Any]] = {}
    for name in sorted(initial_names):
        payload, identity = _read_bytes_at(
            root_descriptor, name, f"post-claim available artifact {name}"
        )
        available[name] = {
            "sha256": identity["sha256"],
            "size_bytes": len(payload),
        }
    lexical_matches = True
    try:
        _directory_identity_from_descriptor(
            root_descriptor, out_dir, "post-claim held output root"
        )
    except EvaluationError:
        lexical_matches = False
    _write_json_at_x(
        root_descriptor,
        parent_descriptor,
        FATAL_FAIL_NAME,
        {
            "schema": "controlled_real10k_20k_common_evaluation_fatal_fail_v1",
            "generated_utc": _utc_now(),
            "status": "FAIL_IRREVERSIBLE_TEST_RELEASE_CONSUMED",
            "reason": f"{type(exc).__name__}: {exc}",
            "one_time_release_consumed": True,
            "retry_authorized": False,
            "failed_model_arm_denominator_retained": 6,
            "complete_pair_claim_authorized": False,
            "held_output_root_identity": execution["root_identity"],
            "lexical_output_root_still_matches_held_inode": lexical_matches,
            "consumed_one_time_release_lease": execution.get("consumed_lease"),
            "available_artifacts_before_fatal": available,
            "failure_filesystem_contract": {
                "root": str(out_dir),
                "regular_files_exact": sorted(planned_names),
                "regular_file_count": len(planned_names),
                "files_mode": f"{FINAL_FILE_MODE:04o}",
                "directory_mode": f"{FINAL_DIRECTORY_MODE:04o}",
                "nlink_each": 1,
                "sha256_index_name": FAILURE_INDEX_NAME,
                "sha256_index_self_hash_included": False,
                "file_directory_and_parent_fsync_required": True,
            },
        },
    )
    _write_index_at_x(
        root_descriptor,
        parent_descriptor,
        FAILURE_INDEX_NAME,
        sorted(planned_names - {FAILURE_INDEX_NAME}),
    )
    _freeze_exact_held_closure(
        root_descriptor,
        parent_descriptor,
        expected_names=planned_names,
        index_name=FAILURE_INDEX_NAME,
        label="post-claim fatal evaluation",
    )


def _execute_held(args: argparse.Namespace, execution: dict[str, Any]) -> int:
    out_dir = execution["out_dir"]
    root_descriptor = execution["root_descriptor"]
    parent_descriptor = execution["parent_descriptor"]
    lock_descriptor = os.open(
        LOCK_NAME,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_descriptor,
    )
    with os.fdopen(lock_descriptor, "rb", closefd=True) as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EvaluationError("another evaluator process holds the singleton lock") from exc
        prepared, manifest, prepared_snapshots = _load_prepared(
            out_dir, root_descriptor
        )
        rebuilt = manifest["release_contract"]
        bindings = rebuilt.get("release_bindings") or {}
        root_identity = _assert_held_root_matches(
            root_descriptor,
            out_dir,
            bindings.get("evaluation_output_root_identity"),
        )
        execution["root_identity"] = root_identity
        _assert_execute_arguments_match_contract(args, rebuilt)
        lease_descriptor, lease_identity = _open_prepared_release_lease(
            bindings.get("one_time_release_lease"),
            challenge_nonce=manifest["qa_challenge_nonce"],
            root_identity=root_identity,
            parent_descriptor=parent_descriptor,
            out_dir=out_dir,
        )
        execution["lease_descriptor"] = lease_descriptor
        release_records = _release_input_records(rebuilt)
        protected_roles = {"common_source_csv", "common_holdout", "fixed_targets"}
        with ExitStack() as input_stack:
            go, go_handle = _audit_go(
                _absolute_lexical(args.independent_qa_go_receipt),
                args.expected_independent_qa_go_receipt_sha256,
                out_dir=out_dir,
                prepared=prepared,
                prepared_snapshots=prepared_snapshots,
                manifest=manifest,
                now=datetime.now(timezone.utc),
                stack=input_stack,
            )
            held = _open_held_inputs(release_records, input_stack)
            snapshots = {
                role: _snapshot_held_input(handle, release_records[role], role)
                for role, handle in held.items()
                if role not in protected_roles
            }
            runtime = rebuilt["release_bindings"]["numerical_runtime"]
            _verify_live_runtime_from_snapshots(runtime, snapshots)
            _verify_live_evaluator_from_snapshot(
                release_records["evaluator_source"], snapshots["evaluator_source"]
            )
            shared_identity = rebuilt["release_bindings"][
                "shared_scientific_contract"
            ]
            _verify_live_shared_contract_from_snapshot(
                shared_identity, snapshots["shared_scientific_contract"]
            )
            _verify_paired_shared_contract_snapshot(
                snapshots["paired_run_contract"],
                shared_identity,
                evaluator_runtime=runtime,
                paired_runtime=bindings.get("paired_runtime"),
                paired_shared_contract=bindings.get("paired_shared_contract"),
                controlled_singleton=bindings.get("controlled_singleton"),
                fixture_mode=bool(rebuilt.get("fixture_only")),
            )
            trainer_path = Path(rebuilt["paths"]["trainer_source"])
            trainer = _load_trainer(
                snapshots["trainer_source"],
                trainer_path,
                release_records["trainer_source"]["sha256"],
            )
            if any(held[role].tell() != 0 for role in protected_roles):
                raise EvaluationError("a protected test input was read before release claim")
            _assert_prepared_snapshots_unchanged(
                root_descriptor, prepared_snapshots
            )
            _assert_held_root_matches(
                root_descriptor, out_dir, root_identity
            )
            _assert_held_external_file_unchanged(
                go_handle, go["held_file_identity"]
            )
            _assert_prepared_release_lease_unchanged(
                lease_descriptor, lease_identity
            )
            try:
                os.stat(CLAIM_NAME, dir_fd=root_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise EvaluationError(
                    "one-time test release was already claimed; retry is forbidden"
                )
            # Crossing this line is the irreversible release boundary.  Any
            # exception during state consumption is conservatively terminal.
            execution["release_consumed"] = True
            consumed_lease = _consume_release_lease(
                lease_descriptor,
                lease_identity,
                challenge_nonce=manifest["qa_challenge_nonce"],
                root_identity=root_identity,
                parent_descriptor=parent_descriptor,
            )
            execution["consumed_lease"] = consumed_lease
            claim_path = out_dir / CLAIM_NAME
            claim = {
                "schema": RELEASE_CLAIM_SCHEMA,
                "generated_utc": _utc_now(),
                "status": "CLAIMED_IRREVERSIBLE_NO_RETRY",
                "release_event_count": 1,
                "test_rows_read_before_claim": False,
                "protected_test_input_roles_opened_but_unread": sorted(
                    protected_roles
                ),
                "held_release_input_count": len(held),
                "independent_go_receipt": go,
                "held_output_root_identity": root_identity,
                "consumed_one_time_release_lease": consumed_lease,
                "prepared_receipt_sha256": _sha256_bytes(
                    prepared_snapshots[PREPARED_NAME]
                ),
                "evaluation_manifest_sha256": _sha256_bytes(
                    prepared_snapshots[MANIFEST_NAME]
                ),
                "independent_qa_required_sha256": _sha256_bytes(
                    prepared_snapshots[QA_REQUIRED_NAME]
                ),
                "release_contract_sha256": rebuilt["release_contract_sha256"],
                "durability": {
                    "claim_file_fsync": True,
                    "claim_directory_fsync": True,
                    "claim_parent_directory_fsync": True,
                },
                "recovery_boundary": {
                    "claim_absent": "NO_RELEASE_CONSUMPTION_PROVEN; A FRESH EXECUTE INVOCATION MAY BE REVIEWED",
                    "claim_present": "RELEASE_CONSUMED_IRREVERSIBLY; NEVER RETRY OR REOPEN TEST INPUTS",
                    "ambiguous_filesystem_state": "FAIL_CLOSED_AS_RELEASE_CONSUMED",
                },
            }
            _write_json_at_x(
                root_descriptor, parent_descriptor, CLAIM_NAME, claim
            )
            _read_bytes_at(
                root_descriptor, CLAIM_NAME, "durable one-time release claim"
            )
            for role in sorted(protected_roles):
                snapshots[role] = _snapshot_held_input(
                    held[role], release_records[role], role
                )

        common_rows, common_x, common_y, common_ids = _load_common_test(
            snapshots["common_source_csv"],
            snapshots["common_holdout"],
            expected_rows=args.expected_common_test_rows,
        )
        fixed_ids, fixed_x, fixed_legacy, fixed_extension = _load_fixed_matrix(
            snapshots["fixed_targets"], args.expected_fixed_rows
        )
        if int(np.sum(fixed_legacy)) != args.expected_legacy_rows or int(
            np.sum(fixed_extension)
        ) != args.expected_extension_rows:
            raise EvaluationError("fixed target panel counts changed after release")
        common_cell_ids, common_cell_capped = _physical_cell_ids(
            common_x, cap_high_k_for_clustering_only=False
        )
        if np.any(common_cell_capped):
            raise EvaluationError("common real-EMX cell encoder unexpectedly capped K")
        for index, (source, derived) in enumerate(zip(common_rows, common_cell_ids)):
            if source.get("controlled_physical_cell_4d") != derived:
                raise EvaluationError(
                    f"common test row {index} stored/derived physical cell mismatch"
                )
        fixed_cell_ids, fixed_cell_capped = _physical_cell_ids(
            fixed_x, cap_high_k_for_clustering_only=True
        )
        if not np.array_equal(fixed_cell_capped, fixed_extension):
            raise EvaluationError("fixed10k high-K clustering cap/panel identity mismatch")
        normalization = rebuilt["normalization"]
        declared_spans = np.asarray(normalization["x_upper"], dtype=np.float64) - np.asarray(
            normalization["x_lower"], dtype=np.float64
        )
        common_out = out_dir / COMMON_ROWS_NAME
        fixed_out = out_dir / FIXED_ROWS_NAME
        results: dict[str, dict[str, Any]] = {}
        predictions_by_model: dict[str, dict[str, np.ndarray]] = {}
        with ExitStack() as stack:
            common_handle = stack.enter_context(
                _open_text_at_x(root_descriptor, COMMON_ROWS_NAME)
            )
            fixed_handle = stack.enter_context(
                _open_text_at_x(root_descriptor, FIXED_ROWS_NAME)
            )
            common_writer = csv.DictWriter(common_handle, fieldnames=COMMON_CSV_FIELDS, extrasaction="raise")
            fixed_writer = csv.DictWriter(fixed_handle, fieldnames=FIXED_CSV_FIELDS, extrasaction="raise")
            common_writer.writeheader()
            fixed_writer.writeheader()
            for key in _expected_model_keys():
                model = rebuilt["models"][key]
                try:
                    (
                        result,
                        common_model_rows,
                        fixed_model_rows,
                        bootstrap_predictions,
                    ) = _evaluate_one_model(
                        model_record=model,
                        model_bytes=snapshots[f"model_weights__{key}"],
                        trainer=trainer,
                        common_rows=common_rows,
                        common_x=common_x,
                        common_y=common_y,
                        common_ids=common_ids,
                        common_cell_ids=common_cell_ids,
                        common_cell_capped=common_cell_capped,
                        fixed_ids=fixed_ids,
                        fixed_x=fixed_x,
                        fixed_legacy=fixed_legacy,
                        fixed_extension=fixed_extension,
                        fixed_cell_ids=fixed_cell_ids,
                        fixed_cell_capped=fixed_cell_capped,
                        declared_spans=declared_spans,
                    )
                except Exception as exc:  # retain exact failed-arm denominator
                    results[key] = {
                        "status": "FAIL",
                        "model_key": key,
                        "arm": model["arm"],
                        "seed": model["seed"],
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                else:
                    # Computation and all required metrics have passed before
                    # any rows for this arm are published.  I/O failure is a
                    # fatal one-time-release failure, not a model-arm result.
                    common_writer.writerows(common_model_rows)
                    fixed_writer.writerows(fixed_model_rows)
                    results[key] = result
                    predictions_by_model[key] = bootstrap_predictions
            common_handle.flush()
            fixed_handle.flush()
            os.fchmod(common_handle.fileno(), 0o644)
            os.fchmod(fixed_handle.fileno(), 0o644)
            os.fsync(common_handle.fileno())
            os.fsync(fixed_handle.fileno())
        _durable_held_directories(root_descriptor, parent_descriptor)

        def estimand_records(selector: Sequence[str]) -> dict[str, dict[str, Any]]:
            selected: dict[str, dict[str, Any]] = {}
            for key, result in results.items():
                if result.get("status") != "PASS":
                    selected[key] = {"status": "FAIL"}
                    continue
                value: Any = result
                for component in selector:
                    value = value[component]
                selected[key] = {"status": "PASS", "metrics": value}
            return selected

        paired = {
            "common_forward_primary": _paired_statistics(
                estimand_records(("common_forward_primary",)),
                "common_holdout_forward_label_error_large_minus_small",
            ),
            "common_inverse_own_forward_secondary": _paired_statistics(
                estimand_records(("common_inverse_own_forward_secondary",)),
                "common_holdout_inverse_own_forward_proxy_large_minus_small",
            ),
            "common_inverse_geometry_label_secondary": _paired_statistics(
                estimand_records(("common_inverse_geometry_label_secondary",)),
                "common_holdout_inverse_geometry_label_distance_large_minus_small",
            ),
        }
        for panel in ("full10k", "legacy8000", "highK2000"):
            paired[f"fixed10k_{panel}"] = _paired_statistics(
                estimand_records(("fixed10k_own_forward_proxy", panel)),
                f"fixed10k_{panel}_own_forward_proxy_large_minus_small",
            )
        spatial_sensitivity = _spatial_sensitivity_bootstrap(
            common_x=common_x,
            common_y=common_y,
            common_cell_ids=common_cell_ids,
            fixed_x=fixed_x,
            fixed_legacy=fixed_legacy,
            fixed_extension=fixed_extension,
            fixed_cell_ids=fixed_cell_ids,
            predictions_by_model=predictions_by_model,
            paired_effects=paired,
            declared_spans=declared_spans,
            replicates=args.bootstrap_replicates,
            fixture_mode=args.fixture_mode,
        )
        passed = sum(result.get("status") == "PASS" for result in results.values())
        complete_pairs = min(
            value["paired_seed_denominator_complete"] for value in paired.values()
        )
        bootstrap_complete = spatial_sensitivity["frames_complete"] == 4
        if passed == 6 and complete_pairs == 3 and bootstrap_complete:
            status = (
                "PASS_SYNTHETIC_FIXTURE_ONLY_NOT_RESEARCH"
                if args.fixture_mode
                else "PASS_COMPLETE_CONTROLLED_COMMON_EVALUATION"
            )
        else:
            status = "FAIL_INCOMPLETE_RETAINED_DENOMINATORS"
        summary = {
            "schema": SUMMARY_SCHEMA,
            "generated_utc": _utc_now(),
            "status": status,
            "evidence_boundary": {
                "common_forward_primary": "stored historical real-EMX label holdout; no new EMX",
                "inverse_proxy_secondary": "own-forward tandem self-consistency; not physical truth",
                "geometry_label_secondary": "one recorded geometry label; inverse is non-unique",
                "fixed10k": "frozen finite target frame own-forward one-shot proxy; not fresh EMX",
                "causal_scope": "data-size-only claim conditional on every frozen control and all three complete pairs",
            },
            "release": {
                "test_access_event_count_before_six_arm_terminal": 0,
                "test_access_event_count_this_evaluator": 1,
                "one_time_release_claim": {
                    "path": str(claim_path),
                    "sha256": _sha256_at(root_descriptor, CLAIM_NAME),
                },
                "independent_go_receipt": go,
                "fresh_emx_generated": False,
                "fixed10k_regenerated": False,
                "models_retrained": False,
            },
            "denominators": {
                **rebuilt["denominators"],
                "model_arms_evaluated_pass": passed,
                "model_arms_failed": 6 - passed,
                "paired_seeds_complete": complete_pairs,
                "paired_seeds_failed_or_missing": 3 - complete_pairs,
                "spatial_bootstrap_frames_requested": 4,
                "spatial_bootstrap_frames_complete": spatial_sensitivity[
                    "frames_complete"
                ],
                "spatial_bootstrap_frames_failed": spatial_sensitivity[
                    "frames_failed"
                ],
            },
            "bindings": rebuilt["release_bindings"],
            "release_contract_sha256": rebuilt["release_contract_sha256"],
            "per_arm": results,
            "paired_effects": paired,
            "spatial_sensitivity": spatial_sensitivity,
            "statistical_boundary": {
                "paired_delta": "large_minus_small_within_seed",
                "replicate_unit": "paired_training_seed",
                "replicate_count": 3,
                "sample_SD_ddof": 1,
                "t_interval": "two-sided 95 percent Student-t, df=2",
                "t_critical_df2": T95_DF2,
                "small_n_warning": True,
                "failed_pair_policy": "no complete paired-effect claim; failed denominator retained",
            },
            "metric_contract": {
                "required_distribution": ["MAE", "RMSE", "P50", "P90", "P95", "P99", "Max"],
                "joint_metrics": [
                    "declared_range_joint_NRMSE",
                    "fixed_span_symmetric_joint_NRMSE",
                    "fixed_span_engineering_joint_error",
                ],
                "Q_metrics": [
                    "target_met_rate",
                    "shortfall_MAE",
                    "shortfall_RMSE",
                    "shortfall_P50",
                    "shortfall_P90",
                    "shortfall_P95",
                    "shortfall_P99",
                    "shortfall_Max",
                ],
                "K_target_relative_APE_primary": False,
                "spatial_bootstrap_replicates": args.bootstrap_replicates,
                "spatial_bootstrap_master_seed": SPATIAL_BOOTSTRAP_MASTER_SEED,
                "spatial_bootstrap_addendum_sha256": PREREGISTRATION_ADDENDUM_SHA256,
            },
            "outputs": {
                "common_per_row_csv": {
                    "path": str(common_out),
                    "sha256": _sha256_at(root_descriptor, COMMON_ROWS_NAME),
                },
                "fixed10k_per_row_csv": {
                    "path": str(fixed_out),
                    "sha256": _sha256_at(root_descriptor, FIXED_ROWS_NAME),
                },
            },
            "fixture_only": bool(args.fixture_mode),
            "eligible_for_research_conclusion": bool(
                not args.fixture_mode
                and status == "PASS_COMPLETE_CONTROLLED_COMMON_EVALUATION"
            ),
        }
        summary_path = out_dir / SUMMARY_NAME
        _write_json_at_x(
            root_descriptor, parent_descriptor, SUMMARY_NAME, summary
        )
        terminal = {
            "schema": COMPLETE_SCHEMA,
            "generated_utc": _utc_now(),
            "status": "PASS" if status.startswith("PASS") else "FAIL",
            "verdict": status,
            "one_time_release_consumed": True,
            "retry_authorized": False,
            "test_access_event_count": 1,
            "model_arm_pass_count": passed,
            "model_arm_fail_count": 6 - passed,
            "complete_pair_count": complete_pairs,
            "spatial_bootstrap_frame_pass_count": spatial_sensitivity[
                "frames_complete"
            ],
            "spatial_bootstrap_frame_fail_count": spatial_sensitivity["frames_failed"],
            "spatial_bootstrap_replicates": args.bootstrap_replicates,
            "final_filesystem_contract": {
                "root": str(out_dir),
                "regular_files_exact": sorted(FINAL_FILE_NAMES),
                "regular_file_count": len(FINAL_FILE_NAMES),
                "files_mode": f"{FINAL_FILE_MODE:04o}",
                "directory_mode": f"{FINAL_DIRECTORY_MODE:04o}",
                "nlink_each": 1,
                "sha256_index_name": RESULT_INDEX_NAME,
                "sha256_index_self_hash_included": False,
                "file_directory_and_parent_fsync_required": True,
            },
            "artifacts": {
                COMMON_ROWS_NAME: {
                    "sha256": _sha256_at(root_descriptor, COMMON_ROWS_NAME),
                    "size_bytes": _size_at(root_descriptor, COMMON_ROWS_NAME),
                },
                FIXED_ROWS_NAME: {
                    "sha256": _sha256_at(root_descriptor, FIXED_ROWS_NAME),
                    "size_bytes": _size_at(root_descriptor, FIXED_ROWS_NAME),
                },
                SUMMARY_NAME: {
                    "sha256": _sha256_at(root_descriptor, SUMMARY_NAME),
                    "size_bytes": _size_at(root_descriptor, SUMMARY_NAME),
                },
                CLAIM_NAME: {
                    "sha256": _sha256_at(root_descriptor, CLAIM_NAME),
                    "size_bytes": _size_at(root_descriptor, CLAIM_NAME),
                },
            },
        }
        terminal_path = out_dir / TERMINAL_NAME
        _write_json_at_x(
            root_descriptor, parent_descriptor, TERMINAL_NAME, terminal
        )
        _write_index_at_x(
            root_descriptor,
            parent_descriptor,
            RESULT_INDEX_NAME,
            sorted(FINAL_FILE_NAMES - {RESULT_INDEX_NAME}),
        )
        _freeze_and_verify_final_output(
            out_dir,
            root_descriptor,
            parent_descriptor,
            root_identity,
        )
        print(f"status={status}")
        print(f"summary={summary_path}")
        return 0 if status.startswith("PASS") else 2


def _execute(args: argparse.Namespace) -> int:
    out_dir = _absolute_lexical(args.out_dir)
    _reject_symlink_chain(out_dir.parent, "prepared evaluation output parent")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(os.fspath(out_dir.parent), directory_flags)
    except OSError as exc:
        raise EvaluationError(f"cannot open evaluation output parent: {exc}") from exc
    try:
        root_descriptor = os.open(
            out_dir.name, directory_flags, dir_fd=parent_descriptor
        )
    except OSError as exc:
        os.close(parent_descriptor)
        raise EvaluationError(f"cannot open prepared evaluation output root: {exc}") from exc
    observed_root = _directory_identity_from_descriptor(
        root_descriptor, out_dir, "prepared evaluation output root"
    )
    execution: dict[str, Any] = {
        "out_dir": out_dir,
        "parent_descriptor": parent_descriptor,
        "root_descriptor": root_descriptor,
        "root_identity": observed_root,
        "lease_descriptor": None,
        "release_consumed": False,
        "consumed_lease": None,
    }
    try:
        return _execute_held(args, execution)
    except BaseException as exc:
        if execution["release_consumed"]:
            try:
                _terminalize_post_claim_failure(execution, exc)
            except BaseException as closure_exc:
                raise EvaluationError(
                    f"{type(exc).__name__}: {exc}; post-claim exact failure "
                    f"closure failed: {type(closure_exc).__name__}: {closure_exc}"
                ) from closure_exc
        raise
    finally:
        lease_descriptor = execution.get("lease_descriptor")
        if isinstance(lease_descriptor, int):
            os.close(lease_descriptor)
        os.close(root_descriptor)
        os.close(parent_descriptor)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "execute"), default="prepare")
    parser.add_argument("--preregistration-addendum", required=True)
    parser.add_argument(
        "--expected-preregistration-addendum-sha256", required=True
    )
    parser.add_argument("--materialization-summary", required=True)
    parser.add_argument("--expected-materialization-summary-sha256", required=True)
    parser.add_argument("--common-holdout", required=True)
    parser.add_argument("--expected-common-holdout-sha256", required=True)
    parser.add_argument("--fixed-normalization", required=True)
    parser.add_argument("--expected-fixed-normalization-sha256", required=True)
    parser.add_argument("--six-arm-terminal-manifest", required=True)
    parser.add_argument("--expected-six-arm-terminal-manifest-sha256", required=True)
    parser.add_argument("--fixed-targets-json", required=True)
    parser.add_argument("--expected-fixed-targets-sha256", required=True)
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--expected-trainer-sha256", required=True)
    parser.add_argument("--runtime-bootstrap")
    parser.add_argument("--expected-runtime-bootstrap-sha256")
    parser.add_argument("--runtime-closure-json")
    parser.add_argument("--expected-runtime-closure-json-sha256")
    parser.add_argument("--runtime-closure-tree")
    parser.add_argument("--controlled-singleton-lock")
    parser.add_argument("--expected-controlled-singleton-lock-sha256")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--independent-qa-go-receipt")
    parser.add_argument("--expected-independent-qa-go-receipt-sha256")
    parser.add_argument("--fixture-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--expected-common-test-rows", type=int, default=EXPECTED_COMMON_TEST_ROWS)
    parser.add_argument("--expected-fixed-rows", type=int, default=EXPECTED_FIXED_ROWS)
    parser.add_argument("--expected-legacy-rows", type=int, default=EXPECTED_LEGACY_ROWS)
    parser.add_argument("--expected-extension-rows", type=int, default=EXPECTED_EXTENSION_ROWS)
    parser.add_argument(
        "--bootstrap-replicates",
        type=int,
        default=SPATIAL_BOOTSTRAP_REPLICATES,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    sha_fields = (
        "expected_preregistration_addendum_sha256",
        "expected_materialization_summary_sha256",
        "expected_common_holdout_sha256",
        "expected_fixed_normalization_sha256",
        "expected_six_arm_terminal_manifest_sha256",
        "expected_fixed_targets_sha256",
        "expected_trainer_sha256",
    )
    for field in sha_fields:
        try:
            setattr(args, field, _require_sha_token(getattr(args, field), field))
        except EvaluationError as exc:
            parser.error(str(exc))
    go_args = (
        args.independent_qa_go_receipt,
        args.expected_independent_qa_go_receipt_sha256,
    )
    if args.phase == "prepare" and any(go_args):
        parser.error("prepare does not accept a GO receipt")
    if args.phase == "execute" and not all(go_args):
        parser.error("execute requires an independent exact-GO receipt and expected SHA")
    if args.phase == "execute":
        try:
            args.expected_independent_qa_go_receipt_sha256 = _require_sha_token(
                args.expected_independent_qa_go_receipt_sha256,
                "expected independent GO SHA",
            )
        except EvaluationError as exc:
            parser.error(str(exc))
    runtime_values = (
        args.runtime_bootstrap,
        args.expected_runtime_bootstrap_sha256,
        args.runtime_closure_json,
        args.expected_runtime_closure_json_sha256,
        args.runtime_closure_tree,
        args.controlled_singleton_lock,
        args.expected_controlled_singleton_lock_sha256,
    )
    if args.fixture_mode:
        if any(runtime_values):
            parser.error("fixture mode does not accept production descriptor-runtime arguments")
    elif not all(runtime_values):
        parser.error(
            "production evaluation requires runtime bootstrap/closure/tree and singleton bindings"
        )
    else:
        for field in (
            "expected_runtime_bootstrap_sha256",
            "expected_runtime_closure_json_sha256",
            "expected_controlled_singleton_lock_sha256",
        ):
            try:
                setattr(args, field, _require_sha_token(getattr(args, field), field))
            except EvaluationError as exc:
                parser.error(str(exc))
    counts = (
        args.expected_common_test_rows,
        args.expected_fixed_rows,
        args.expected_legacy_rows,
        args.expected_extension_rows,
    )
    if any(value <= 0 for value in counts) or args.expected_legacy_rows + args.expected_extension_rows != args.expected_fixed_rows:
        parser.error("evaluation denominators must be positive and panel counts must sum to full")
    if args.bootstrap_replicates <= 0:
        parser.error("bootstrap replicate count must be positive")
    if not args.fixture_mode and counts != (
        EXPECTED_COMMON_TEST_ROWS,
        EXPECTED_FIXED_ROWS,
        EXPECTED_LEGACY_ROWS,
        EXPECTED_EXTENSION_ROWS,
    ):
        parser.error("production denominators are immutable; tiny counts require --fixture-mode")
    if (
        not args.fixture_mode
        and args.bootstrap_replicates != SPATIAL_BOOTSTRAP_REPLICATES
    ):
        parser.error("production spatial bootstrap replicate count is immutable")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.fixture_mode:
        if not _SEALED_RUNTIME_IMPORT:
            raise EvaluationError("production evaluator was not imported by descriptor runtime")
        try:
            runtime_bootstrap.require_active_runtime(
                "evaluator", args.expected_runtime_closure_json_sha256
            )
        except runtime_bootstrap.RuntimeClosureError as exc:
            raise EvaluationError("production evaluator descriptor runtime is inactive") from exc
    with _held_controlled_singleton(args) as singleton_identity:
        args.controlled_singleton_identity = singleton_identity
        if args.phase == "prepare":
            return _prepare(args)
        return _execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
