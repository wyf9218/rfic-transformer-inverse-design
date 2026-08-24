#!/usr/bin/env python3
"""Run one hash-gated 200k training job, then its frozen legacy-8k evaluation.

This is a site-neutral controller.  It never searches for, stops, resumes, or
signals another process.  A caller must first create a new, empty run
directory and provide frozen JSON argv records plus the expected SHA-256 of
every executable input.  Commands are executed directly as argv arrays; a
shell is never involved.

The controller is intended to be launched detached by the site operator.  It
remains alive while its one trainer child runs, records an immutable launch
receipt, and starts the evaluator only after the trainer exits successfully
and the requested checkpoint artifacts exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "deployed100k_exact_contract_on_200k_controller_v1"
THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)
DEFAULT_CHECKPOINT_ARTIFACTS = ("physical_feature_tandem_inverse_weights.npz",)
DEFAULT_EVALUATION_ARTIFACTS = (
    "per_target_100k_predictions.csv",
    "per_target_200k_predictions.csv",
    "architecture_matched_comparison.csv",
    "evaluation_summary.json",
)
FINITE_OBSERVER_REQUIRED_STEPS = 5
SUMMARY_SHA_PLACEHOLDER = "__MODEL_200K_SUMMARY_SHA256__"
WEIGHTS_SHA_PLACEHOLDER = "__MODEL_200K_WEIGHTS_SHA256__"
SUMMARY_SHA_FLAG = "--expected-model-200k-summary-sha256"
WEIGHTS_SHA_FLAG = "--expected-model-200k-weights-sha256"


class ControllerError(RuntimeError):
    """A fail-closed, auditable controller error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ControllerError(f"invalid SHA-256 value: {value!r}")
    return normalized


def _require_regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ControllerError(f"{label} is not a regular file: {resolved}")
    return resolved


def _require_sha(path: Path, expected: str, label: str) -> str:
    normalized = _valid_sha256(expected)
    actual = _sha256(path)
    if actual != normalized:
        raise ControllerError(f"{label} SHA-256 mismatch: {actual} != {normalized}")
    return actual


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ControllerError(f"cannot read {label} as JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ControllerError(f"{label} must be a JSON object")
    return value


def _read_argv(path: Path, label: str) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ControllerError(f"cannot read {label} argv JSON: {exc}") from exc
    if isinstance(value, dict):
        value = value.get("argv")
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item or "\x00" in item or "\n" in item for item in value)
    ):
        raise ControllerError(f"{label} argv must be a non-empty JSON string array")
    return list(value)


def _iter_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_objects(child)


def _path_value_matches(value: str, expected_path: Path) -> bool:
    if value == str(expected_path):
        return True
    try:
        return Path(value).expanduser().resolve() == expected_path
    except (OSError, RuntimeError, ValueError):
        return False


def _manifest_has_artifact_binding(
    manifest: dict[str, Any], artifact_path: Path, artifact_sha256: str
) -> bool:
    """Require a path and its SHA in the same nested artifact record.

    Field names are deliberately site-neutral.  A record may use ``path`` /
    ``sha256`` or descriptive variants such as ``trainer_path`` /
    ``trainer_sha256``; identity is determined from the values, not a private
    naming convention.
    """

    for record in _iter_objects(manifest):
        strings = [item for item in record.values() if isinstance(item, str)]
        has_path = any(_path_value_matches(item, artifact_path) for item in strings)
        has_sha = artifact_sha256 in (item.strip().lower() for item in strings)
        if has_path and has_sha:
            return True
    return False


def _all_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _all_string_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _all_string_values(child)]
    return []


def _manifest_has_runtime_binding(
    manifest: dict[str, Any],
    *,
    python_path: Path,
    python_sha256: str,
    numpy_version: str,
    numpy_core_path: Path,
    numpy_core_sha256: str,
    blas_path: Path,
    blas_sha256: str,
) -> bool:
    for record in _iter_objects(manifest):
        if record.get("numpy_version") != numpy_version:
            continue
        strings = _all_string_values(record)
        required_hashes = {python_sha256, numpy_core_sha256, blas_sha256}
        if not required_hashes.issubset({item.strip().lower() for item in strings}):
            continue
        if all(
            any(_path_value_matches(item, path) for item in strings)
            for path in (python_path, numpy_core_path, blas_path)
        ):
            return True
    return False


def _argv_has_path(argv: Sequence[str], expected_path: Path) -> bool:
    for token in argv:
        candidate = token.split("=", 1)[1] if "=" in token else token
        if _path_value_matches(candidate, expected_path):
            return True
    return False


def _single_flag_value(argv: Sequence[str], flag: str) -> tuple[int, str, bool]:
    """Return (index, value, uses_equals_form) for one exact flag occurrence."""

    occurrences: list[tuple[int, str, bool]] = []
    for index, token in enumerate(argv):
        if token == flag:
            if index + 1 >= len(argv):
                raise ControllerError(f"flag has no value: {flag}")
            occurrences.append((index + 1, argv[index + 1], False))
        elif token.startswith(flag + "="):
            occurrences.append((index, token.split("=", 1)[1], True))
    if len(occurrences) != 1:
        raise ControllerError(
            f"flag must occur exactly once with one value: {flag}; found {len(occurrences)}"
        )
    _, value, _ = occurrences[0]
    if not value:
        raise ControllerError(f"flag has an empty value: {flag}")
    return occurrences[0]


def _require_flag_path(argv: Sequence[str], flag: str, expected_path: Path) -> None:
    _, value, _ = _single_flag_value(argv, flag)
    if not _path_value_matches(value, expected_path):
        raise ControllerError(
            f"flag {flag} does not bind exact path {expected_path}: {value!r}"
        )


def _require_flag_literal(argv: Sequence[str], flag: str, expected: str) -> None:
    _, value, _ = _single_flag_value(argv, flag)
    if value != expected:
        raise ControllerError(
            f"flag {flag} must have literal value {expected!r}, got {value!r}"
        )


def _validate_evaluator_template(argv: Sequence[str]) -> None:
    expected = {
        SUMMARY_SHA_FLAG: SUMMARY_SHA_PLACEHOLDER,
        WEIGHTS_SHA_FLAG: WEIGHTS_SHA_PLACEHOLDER,
    }
    for flag, placeholder in expected.items():
        _require_flag_literal(argv, flag, placeholder)
        exact_count = sum(token == placeholder for token in argv)
        containing_count = sum(placeholder in token for token in argv)
        if exact_count != 1 or containing_count != 1:
            raise ControllerError(
                f"placeholder must appear once and only as the exact {flag} value: {placeholder}"
            )


def _realize_evaluator_argv(
    template: Sequence[str], *, summary_sha256: str, weights_sha256: str
) -> list[str]:
    _validate_evaluator_template(template)
    realized = list(template)
    substitutions = {
        SUMMARY_SHA_FLAG: (SUMMARY_SHA_PLACEHOLDER, _valid_sha256(summary_sha256)),
        WEIGHTS_SHA_FLAG: (WEIGHTS_SHA_PLACEHOLDER, _valid_sha256(weights_sha256)),
    }
    changed_indices: list[int] = []
    for flag, (placeholder, replacement) in substitutions.items():
        index, value, uses_equals = _single_flag_value(realized, flag)
        if value != placeholder or uses_equals:
            # Templates are deliberately restricted to a separate literal
            # value token so the two permitted mutations are unambiguous.
            raise ControllerError(f"template {flag} must use a separate placeholder value token")
        realized[index] = replacement
        changed_indices.append(index)
    if len(set(changed_indices)) != 2:
        raise ControllerError("evaluator template substitutions are not unique")
    for index, (before, after) in enumerate(zip(template, realized)):
        if index not in changed_indices and before != after:
            raise ControllerError("unexpected evaluator template mutation")
    if any(
        placeholder in token
        for placeholder in (SUMMARY_SHA_PLACEHOLDER, WEIGHTS_SHA_PLACEHOLDER)
        for token in realized
    ):
        raise ControllerError("realized evaluator argv still contains a SHA placeholder")
    return realized


def _all_boolean_leaves_true(value: Any) -> tuple[bool, int]:
    if isinstance(value, bool):
        return value, 1
    if isinstance(value, dict) and value:
        results = [_all_boolean_leaves_true(child) for child in value.values()]
    elif isinstance(value, list) and value:
        results = [_all_boolean_leaves_true(child) for child in value]
    else:
        return False, 0
    return all(passed for passed, _ in results), sum(count for _, count in results)


def _validate_finite_observer_receipt(
    path: Path,
    *,
    required_steps: int,
    expected_blas_path: Path,
    expected_blas_sha256: str,
) -> dict[str, Any]:
    receipt_path = _require_regular_file(path, "finite observer receipt")
    receipt = _read_json_object(receipt_path, "finite observer receipt")
    if receipt.get("status") != "PASS":
        raise ControllerError("finite observer receipt status is not PASS")
    stages = receipt.get("stages")
    if not isinstance(stages, dict):
        raise ControllerError("finite observer receipt lacks stages object")
    observed_steps: dict[str, int] = {}
    for stage in ("forward_proxy", "tandem_inverse"):
        stage_record = stages.get(stage)
        if not isinstance(stage_record, dict):
            raise ControllerError(f"finite observer receipt lacks {stage} object")
        value = stage_record.get("observed_steps")
        if isinstance(value, bool) or not isinstance(value, int) or value < required_steps:
            raise ControllerError(
                f"finite observer {stage} observed_steps is below {required_steps}"
            )
        observed_steps[stage] = value
    runtime_check_records = [
        record["runtime_checks"]
        for record in _iter_objects(receipt)
        if "runtime_checks" in record
    ]
    if not runtime_check_records:
        raise ControllerError("finite observer receipt lacks runtime_checks")
    check_count = 0
    for checks in runtime_check_records:
        passed, count = _all_boolean_leaves_true(checks)
        if not passed or count < 1:
            raise ControllerError("finite observer runtime_checks are not all true booleans")
        check_count += count
    top_level_runtime_checks = receipt.get("runtime_checks")
    if (
        not isinstance(top_level_runtime_checks, dict)
        or top_level_runtime_checks.get("blas_library_sha256_exact_set") is not True
    ):
        raise ControllerError(
            "finite observer runtime_checks.blas_library_sha256_exact_set is not true"
        )
    runtime_identity = receipt.get("runtime_identity")
    loaded_blas = (
        runtime_identity.get("loaded_blas_libraries")
        if isinstance(runtime_identity, dict)
        else None
    )
    if not isinstance(loaded_blas, list) or len(loaded_blas) != 1:
        raise ControllerError(
            "finite observer runtime_identity.loaded_blas_libraries must contain exactly one item"
        )
    record = loaded_blas[0]
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("path"), str)
        or not isinstance(record.get("sha256"), str)
    ):
        raise ControllerError("finite observer loaded BLAS identity is malformed")
    loaded_blas_sha256 = {_valid_sha256(record["sha256"])}
    expected_blas = _valid_sha256(expected_blas_sha256)
    if (
        loaded_blas_sha256 != {expected_blas}
        or not _path_value_matches(record["path"], expected_blas_path)
    ):
        raise ControllerError(
            "finite observer loaded BLAS path/SHA set is not the unique expected identity"
        )
    return {
        "path": str(receipt_path),
        "sha256": _sha256(receipt_path),
        "status": "PASS",
        "required_steps": required_steps,
        "observed_steps": observed_steps,
        "runtime_check_count": check_count,
        "runtime_checks_all_true": True,
        "loaded_blas_sha256": sorted(loaded_blas_sha256),
        "loaded_blas_sha_set_exact": True,
    }


def _record_unmanaged_child_risk(
    run_dir: Path, *, child_kind: str, child_pid: int, exc: Exception
) -> None:
    payload = {
        "schema": "deployed100k_exact_contract_on_200k_unmanaged_child_risk_v1",
        "overall_status": "FAIL_CONTROLLER_RECEIPT_WRITE_CHILD_LEFT_UNMANAGED",
        "child_kind": child_kind,
        "child_pid": child_pid,
        "controller_pid": os.getpid(),
        "detected_utc": _utc_now(),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "child_was_signaled": False,
        "signals_sent": [],
        "operator_action": "DO_NOT_LAUNCH_DUPLICATE_INSPECT_CHILD_READ_ONLY",
    }
    try:
        _write_json_exclusive(run_dir / "UNMANAGED_CHILD_RISK.json", payload)
    except Exception as record_exc:  # best effort: the evidence filesystem may be failing
        print(
            "CRITICAL: child continues unmanaged and risk receipt could not be written: "
            f"pid={child_pid} receipt_error={record_exc}",
            file=sys.stderr,
        )


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _copy_exclusive(source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("xb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(block)


def _write_status(run_dir: Path, payload: dict[str, Any]) -> None:
    path = run_dir / "RUN_STATUS.json"
    temporary = run_dir / f".RUN_STATUS.{os.getpid()}.{time.time_ns()}.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _status(
    run_dir: Path,
    *,
    state: str,
    overall_status: str,
    started_utc: str,
    pid: int | None = None,
    trainer_returncode: int | None = None,
    evaluator_returncode: int | None = None,
    detail: str = "",
    checkpoint_dir: Path | None = None,
    train_stdout_path: Path | None = None,
) -> dict[str, Any]:
    stdout_record: dict[str, Any] | None = None
    if train_stdout_path is not None:
        if train_stdout_path.is_file():
            stat = train_stdout_path.stat()
            stdout_record = {
                "path": str(train_stdout_path),
                "size_bytes": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds"),
            }
        else:
            stdout_record = {
                "path": str(train_stdout_path),
                "size_bytes": 0,
                "mtime_utc": None,
            }
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "overall_status": overall_status,
        "state": state,
        "controller_pid": os.getpid(),
        "trainer_pid": pid,
        "started_utc": started_utc,
        "updated_utc": _utc_now(),
        "trainer_returncode": trainer_returncode,
        "evaluator_returncode": evaluator_returncode,
        "detail": detail,
        "checkpoint_dir": str(checkpoint_dir) if checkpoint_dir is not None else None,
        "train_stdout_log": stdout_record,
    }
    _write_status(run_dir, payload)
    return payload


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _required_files(root: Path, relative_names: Sequence[str], label: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative_name in relative_names:
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ControllerError(f"unsafe {label} relative path: {relative_name}")
        path = (root / relative).resolve()
        if not _is_within(path, root) or not path.is_file() or path.stat().st_size <= 0:
            raise ControllerError(f"missing or empty {label}: {path}")
        records.append(
            {
                "relative_path": relative.as_posix(),
                "path": str(path),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def _verify_sha256s_file(
    evaluation_dir: Path, required_records: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    sums_path = evaluation_dir / "SHA256SUMS.txt"
    if not sums_path.is_file() or sums_path.stat().st_size <= 0:
        raise ControllerError(f"missing or empty evaluation SHA256SUMS.txt: {sums_path}")
    declared: dict[str, str] = {}
    for line_number, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise ControllerError(f"malformed SHA256SUMS.txt line {line_number}")
        digest = _valid_sha256(fields[0])
        relative_name = fields[1].strip()
        if relative_name.startswith("*"):
            relative_name = relative_name[1:]
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ControllerError(f"unsafe SHA256SUMS.txt path: {relative_name}")
        declared[relative.as_posix()] = digest
    for record in required_records:
        relative_name = str(record["relative_path"])
        if declared.get(relative_name) != record["sha256"]:
            raise ControllerError(f"SHA256SUMS.txt does not bind {relative_name}")
    return {
        "path": str(sums_path),
        "sha256": _sha256(sums_path),
        "size_bytes": sums_path.stat().st_size,
        "required_entries_verified": len(required_records),
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--reference-contract-json", required=True)
    parser.add_argument("--reference-contract-sha256", required=True)
    parser.add_argument("--dataset-binding-json", required=True)
    parser.add_argument("--dataset-binding-sha256", required=True)
    parser.add_argument("--trainer-path", required=True)
    parser.add_argument("--trainer-sha256", required=True)
    parser.add_argument("--trainer-helper-path", required=True)
    parser.add_argument("--trainer-helper-sha256", required=True)
    parser.add_argument("--python-path", required=True)
    parser.add_argument("--python-sha256", required=True)
    parser.add_argument("--numpy-version", required=True)
    parser.add_argument("--numpy-core-path", required=True)
    parser.add_argument("--numpy-core-sha256", required=True)
    parser.add_argument("--blas-path", required=True)
    parser.add_argument("--blas-sha256", required=True)
    parser.add_argument(
        "--trainer-entrypoint-path",
        default="",
        help="Optional hash-bound observer wrapper used as the exact trainer command entrypoint.",
    )
    parser.add_argument("--trainer-entrypoint-sha256", default="")
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--dataset-sha256", required=True)
    parser.add_argument("--reference-summary-path", required=True)
    parser.add_argument("--reference-summary-sha256", required=True)
    parser.add_argument("--reference-weights-path", required=True)
    parser.add_argument("--reference-weights-sha256", required=True)
    parser.add_argument("--fixed-targets-path", required=True)
    parser.add_argument("--fixed-targets-sha256", required=True)
    parser.add_argument("--trainer-argv-json", required=True)
    parser.add_argument("--trainer-argv-sha256", required=True)
    parser.add_argument("--evaluator-path", required=True)
    parser.add_argument("--evaluator-sha256", required=True)
    parser.add_argument("--evaluator-argv-json", required=True)
    parser.add_argument("--evaluator-argv-sha256", required=True)
    parser.add_argument(
        "--candidate-summary-path",
        required=True,
        help="Future exact trainer summary path inside checkpoint-dir.",
    )
    parser.add_argument(
        "--candidate-weights-path",
        required=True,
        help="Future exact trainer weights path inside checkpoint-dir.",
    )
    parser.add_argument(
        "--finite-observer-receipt",
        required=True,
        help="Future finite-update observer receipt path inside run-dir.",
    )
    parser.add_argument(
        "--finite-observer-required-steps",
        type=int,
        default=FINITE_OBSERVER_REQUIRED_STEPS,
    )
    parser.add_argument("--thread-limit", type=int, default=4)
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=30.0,
        help="Maximum interval between controller UTC heartbeats while the trainer runs.",
    )
    parser.add_argument("--checkpoint-dir", default="")
    parser.add_argument("--evaluation-dir", default="")
    parser.add_argument(
        "--required-checkpoint-artifact",
        action="append",
        default=[],
        help="Relative to checkpoint-dir; repeat for multiple required artifacts.",
    )
    parser.add_argument(
        "--required-evaluation-artifact",
        action="append",
        default=[],
        help="Relative to evaluation-dir; defaults to the four frozen comparison exports.",
    )
    return parser.parse_args(argv)


def _record_failure(
    run_dir: Path,
    *,
    state: str,
    started_utc: str,
    exc: Exception,
    pid: int | None,
    trainer_returncode: int | None,
    evaluator_returncode: int | None,
    checkpoint_dir: Path | None = None,
    train_stdout_path: Path | None = None,
) -> None:
    detail = f"{type(exc).__name__}: {exc}"
    status = _status(
        run_dir,
        state=state,
        overall_status="FAIL",
        started_utc=started_utc,
        pid=pid,
        trainer_returncode=trainer_returncode,
        evaluator_returncode=evaluator_returncode,
        detail=detail,
        checkpoint_dir=checkpoint_dir,
        train_stdout_path=train_stdout_path,
    )
    failure_path = run_dir / "FAILURE_RECEIPT.json"
    if not failure_path.exists():
        _write_json_exclusive(
            failure_path,
            {
                "schema": "deployed100k_exact_contract_on_200k_failure_v1",
                "overall_status": "FAIL_STOPPED_NO_SILENT_RETRY",
                "failed_state": state,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "failed_utc": status["updated_utc"],
                "controller_pid": os.getpid(),
                "trainer_pid": pid,
                "trainer_returncode": trainer_returncode,
                "evaluator_returncode": evaluator_returncode,
            },
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    run_dir_input = Path(args.run_dir).expanduser()
    try:
        run_dir_lstat = os.lstat(run_dir_input)
    except OSError as exc:
        print(f"ERROR: cannot lstat pre-created run-dir {run_dir_input}: {exc}", file=sys.stderr)
        return 2
    if stat.S_ISLNK(run_dir_lstat.st_mode) or not stat.S_ISDIR(run_dir_lstat.st_mode):
        print(
            f"ERROR: run-dir must be a pre-created, non-symlink directory: {run_dir_input}",
            file=sys.stderr,
        )
        return 2
    run_dir = run_dir_input.resolve()
    try:
        if any(run_dir.iterdir()):
            raise ControllerError(f"run-dir is not empty; refusing duplicate or overwrite: {run_dir}")
        started_utc = _utc_now()
        _write_json_exclusive(
            run_dir / "RUN_CONTROLLER_LOCK.json",
            {
                "schema": "deployed100k_exact_contract_on_200k_lock_v1",
                "controller_pid": os.getpid(),
                "created_utc": started_utc,
                "no_clobber": True,
            },
        )
    except (ControllerError, FileExistsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    pid: int | None = None
    evaluator_pid: int | None = None
    trainer_returncode: int | None = None
    evaluator_returncode: int | None = None
    failed_state = "PREFLIGHT"
    _status(
        run_dir,
        state="PREFLIGHT",
        overall_status="RUNNING",
        started_utc=started_utc,
        detail="validating frozen inputs and exact argv",
    )

    try:
        if args.thread_limit < 1:
            raise ControllerError("thread-limit must be at least 1")
        if not (0.1 <= args.heartbeat_seconds <= 3600.0):
            raise ControllerError("heartbeat-seconds must be between 0.1 and 3600")
        if args.finite_observer_required_steps < 1:
            raise ControllerError("finite-observer-required-steps must be at least 1")

        reference_source = _require_regular_file(
            Path(args.reference_contract_json), "reference contract"
        )
        dataset_binding_source = _require_regular_file(
            Path(args.dataset_binding_json), "dataset binding"
        )
        trainer = _require_regular_file(Path(args.trainer_path), "trainer")
        trainer_helper = _require_regular_file(
            Path(args.trainer_helper_path), "trainer model-splitting helper"
        )
        expected_trainer_helper = (
            trainer.parents[1]
            / "rfic_transformer_inverse_design"
            / "model_splitting.py"
        ).resolve()
        if trainer_helper != expected_trainer_helper:
            raise ControllerError(
                "trainer helper is not the exact import-location model_splitting.py: "
                f"{trainer_helper} != {expected_trainer_helper}"
            )
        if bool(args.trainer_entrypoint_path) != bool(args.trainer_entrypoint_sha256):
            raise ControllerError(
                "trainer-entrypoint-path and trainer-entrypoint-sha256 must be provided together"
            )
        trainer_entrypoint = (
            _require_regular_file(
                Path(args.trainer_entrypoint_path), "trainer entrypoint"
            )
            if args.trainer_entrypoint_path
            else trainer
        )
        trainer_entrypoint_command = str(
            Path(args.trainer_entrypoint_path or args.trainer_path).expanduser().absolute()
        )
        dataset = _require_regular_file(Path(args.dataset_path), "dataset")
        reference_summary = _require_regular_file(
            Path(args.reference_summary_path), "reference 100k summary"
        )
        reference_weights = _require_regular_file(
            Path(args.reference_weights_path), "reference 100k weights"
        )
        fixed_targets = _require_regular_file(Path(args.fixed_targets_path), "fixed targets")
        evaluator = _require_regular_file(Path(args.evaluator_path), "evaluator")
        evaluator_command = str(Path(args.evaluator_path).expanduser().absolute())
        trainer_argv_source = _require_regular_file(
            Path(args.trainer_argv_json), "trainer argv JSON"
        )
        evaluator_argv_source = _require_regular_file(
            Path(args.evaluator_argv_json), "evaluator argv JSON"
        )
        trainer_argv = _read_argv(trainer_argv_source, "trainer")
        evaluator_argv = _read_argv(evaluator_argv_source, "evaluator")
        trainer_python_command = str(Path(args.python_path).expanduser().absolute())
        trainer_python = _require_regular_file(Path(args.python_path), "trainer Python executable")
        numpy_core = _require_regular_file(Path(args.numpy_core_path), "NumPy core binary")
        blas_library = _require_regular_file(Path(args.blas_path), "BLAS library")
        if not args.numpy_version.strip():
            raise ControllerError("numpy-version must not be empty")

        identities = {
            "reference_contract": _require_sha(
                reference_source, args.reference_contract_sha256, "reference contract"
            ),
            "dataset_binding": _require_sha(
                dataset_binding_source, args.dataset_binding_sha256, "dataset binding"
            ),
            "trainer": _require_sha(trainer, args.trainer_sha256, "trainer"),
            "trainer_helper": _require_sha(
                trainer_helper,
                args.trainer_helper_sha256,
                "trainer model-splitting helper",
            ),
            "python": _require_sha(
                trainer_python,
                args.python_sha256,
                "trainer Python executable",
            ),
            "numpy_core": _require_sha(
                numpy_core,
                args.numpy_core_sha256,
                "NumPy core binary",
            ),
            "blas": _require_sha(
                blas_library,
                args.blas_sha256,
                "BLAS library",
            ),
            "trainer_entrypoint": (
                _require_sha(
                    trainer_entrypoint,
                    args.trainer_entrypoint_sha256,
                    "trainer entrypoint",
                )
                if args.trainer_entrypoint_path
                else _require_sha(trainer, args.trainer_sha256, "trainer entrypoint")
            ),
            "dataset": _require_sha(dataset, args.dataset_sha256, "dataset"),
            "reference_summary": _require_sha(
                reference_summary,
                args.reference_summary_sha256,
                "reference 100k summary",
            ),
            "reference_weights": _require_sha(
                reference_weights,
                args.reference_weights_sha256,
                "reference 100k weights",
            ),
            "fixed_targets": _require_sha(
                fixed_targets, args.fixed_targets_sha256, "fixed targets"
            ),
            "evaluator": _require_sha(evaluator, args.evaluator_sha256, "evaluator"),
            "trainer_argv": _require_sha(
                trainer_argv_source, args.trainer_argv_sha256, "trainer argv JSON"
            ),
            "evaluator_argv": _require_sha(
                evaluator_argv_source, args.evaluator_argv_sha256, "evaluator argv JSON"
            ),
        }
        reference = _read_json_object(reference_source, "reference contract")
        dataset_binding = _read_json_object(dataset_binding_source, "dataset binding")

        if not _manifest_has_artifact_binding(reference, trainer, identities["trainer"]):
            raise ControllerError("reference contract does not bind the exact trainer path/SHA-256")
        if not _manifest_has_artifact_binding(
            reference, trainer_helper, identities["trainer_helper"]
        ):
            raise ControllerError(
                "reference contract does not bind exact trainer helper path/SHA-256"
            )
        if not _manifest_has_runtime_binding(
            reference,
            python_path=trainer_python,
            python_sha256=identities["python"],
            numpy_version=args.numpy_version.strip(),
            numpy_core_path=numpy_core,
            numpy_core_sha256=identities["numpy_core"],
            blas_path=blas_library,
            blas_sha256=identities["blas"],
        ):
            raise ControllerError(
                "reference contract does not bind exact Python/NumPy-core/BLAS paths, "
                "SHA-256 values, and literal numpy_version in one runtime record"
            )
        if args.trainer_entrypoint_path and not _manifest_has_artifact_binding(
            reference, trainer_entrypoint, identities["trainer_entrypoint"]
        ):
            raise ControllerError(
                "reference contract does not bind the exact trainer entrypoint path/SHA-256"
            )
        for label, path, identity_key in (
            ("reference 100k summary", reference_summary, "reference_summary"),
            ("reference 100k weights", reference_weights, "reference_weights"),
        ):
            if not _manifest_has_artifact_binding(reference, path, identities[identity_key]):
                raise ControllerError(
                    f"reference contract does not bind the exact {label} path/SHA-256"
                )
        if not _manifest_has_artifact_binding(dataset_binding, dataset, identities["dataset"]):
            raise ControllerError("dataset binding does not bind the exact dataset path/SHA-256")
        if not _manifest_has_artifact_binding(
            dataset_binding, fixed_targets, identities["fixed_targets"]
        ):
            raise ControllerError(
                "dataset binding does not bind fixed-target path/SHA-256"
            )
        if not _manifest_has_artifact_binding(
            dataset_binding, evaluator, identities["evaluator"]
        ):
            raise ControllerError("dataset binding does not bind evaluator path/SHA-256")
        if not _manifest_has_artifact_binding(
            reference, trainer_argv_source, identities["trainer_argv"]
        ):
            raise ControllerError(
                "reference contract does not bind exact trainer argv JSON path/SHA-256"
            )
        if not _manifest_has_artifact_binding(
            dataset_binding, evaluator_argv_source, identities["evaluator_argv"]
        ):
            raise ControllerError(
                "dataset binding does not bind exact evaluator argv template path/SHA-256"
            )

        checkpoint_dir = (
            Path(args.checkpoint_dir).expanduser().resolve()
            if args.checkpoint_dir
            else run_dir / "checkpoints"
        )
        evaluation_dir = (
            Path(args.evaluation_dir).expanduser().resolve()
            if args.evaluation_dir
            else run_dir / "evaluation"
        )
        if not _is_within(checkpoint_dir, run_dir) or checkpoint_dir == run_dir:
            raise ControllerError("checkpoint-dir must be a proper child of run-dir")
        if not _is_within(evaluation_dir, run_dir) or evaluation_dir == run_dir:
            raise ControllerError("evaluation-dir must be a proper child of run-dir")
        if checkpoint_dir.exists() or evaluation_dir.exists():
            raise ControllerError("checkpoint-dir and evaluation-dir must not exist before launch")
        candidate_summary = Path(args.candidate_summary_path).expanduser().resolve()
        candidate_weights = Path(args.candidate_weights_path).expanduser().resolve()
        finite_observer_receipt = Path(args.finite_observer_receipt).expanduser().resolve()
        for label, path, parent in (
            ("candidate summary", candidate_summary, checkpoint_dir),
            ("candidate weights", candidate_weights, checkpoint_dir),
            ("finite observer receipt", finite_observer_receipt, run_dir),
        ):
            if not _is_within(path, parent) or path == parent:
                raise ControllerError(f"{label} must be a proper child of {parent}")
            if path.exists() or path.is_symlink():
                raise ControllerError(f"future {label} already exists before launch: {path}")

        if len(trainer_argv) < 2 or trainer_argv[0] != trainer_python_command:
            raise ControllerError("trainer argv[0] is not the exact Python path")
        if trainer_argv[1] != trainer_entrypoint_command:
            raise ControllerError("trainer argv[1] is not the exact observer entrypoint")
        _require_flag_path(trainer_argv, "--trainer-source", trainer)
        for flag, value in (
            ("--expected-trainer-sha256", identities["trainer"]),
            ("--expected-python-sha256", identities["python"]),
            ("--expected-numpy-version", args.numpy_version.strip()),
            ("--expected-numpy-core-sha256", identities["numpy_core"]),
            ("--expected-blas-sha256", identities["blas"]),
            ("--expected-thread-limit", str(args.thread_limit)),
            ("--observe-steps", str(args.finite_observer_required_steps)),
        ):
            _require_flag_literal(trainer_argv, flag, value)
        _require_flag_path(trainer_argv, "--receipt", finite_observer_receipt)
        _require_flag_path(trainer_argv, "--training-csv", dataset)
        _require_flag_path(trainer_argv, "--out-dir", checkpoint_dir)

        if len(evaluator_argv) < 2 or evaluator_argv[0] != trainer_python_command:
            raise ControllerError("evaluator argv[0] is not the exact Python path")
        if evaluator_argv[1] != evaluator_command:
            raise ControllerError("evaluator argv[1] is not the exact evaluator path")
        for flag, path in (
            ("--reference-contract", reference_source),
            ("--model-100k-summary", reference_summary),
            ("--model-100k-weights", reference_weights),
            ("--model-100k-trainer-source", trainer),
            ("--model-200k-summary", candidate_summary),
            ("--model-200k-weights", candidate_weights),
            ("--model-200k-trainer-source", trainer),
            ("--targets-json", fixed_targets),
            ("--out-dir", evaluation_dir),
        ):
            _require_flag_path(evaluator_argv, flag, path)
        for flag, value in (
            ("--expected-reference-contract-sha256", identities["reference_contract"]),
            ("--expected-model-100k-summary-sha256", identities["reference_summary"]),
            ("--expected-model-100k-weights-sha256", identities["reference_weights"]),
            ("--expected-model-100k-trainer-sha256", identities["trainer"]),
            ("--expected-model-200k-trainer-sha256", identities["trainer"]),
        ):
            _require_flag_literal(evaluator_argv, flag, value)
        _validate_evaluator_template(evaluator_argv)

        _copy_exclusive(reference_source, run_dir / "REFERENCE_100K_CONTRACT.json")
        _copy_exclusive(dataset_binding_source, run_dir / "DATASET_200K_BINDING.json")
        _copy_exclusive(trainer_argv_source, run_dir / "EXACT_TRAIN_ARGV.json")
        _copy_exclusive(evaluator_argv_source, run_dir / "EXACT_EVALUATION_ARGV.json")
        _write_text_exclusive(run_dir / "EXACT_TRAIN_COMMAND.txt", shlex.join(trainer_argv) + "\n")
        _write_text_exclusive(
            run_dir / "EXACT_EVALUATION_COMMAND.txt", shlex.join(evaluator_argv) + "\n"
        )
        _write_json_exclusive(
            run_dir / "PREFLIGHT_RECEIPT.json",
            {
                "schema": "deployed100k_exact_contract_on_200k_preflight_v1",
                "overall_status": "PASS_HASH_AND_ARGV_BOUND",
                "completed_utc": _utc_now(),
                "identities": identities,
                "reference_schema": reference.get("schema"),
                "dataset_binding_schema": dataset_binding.get("schema"),
                "thread_limit": args.thread_limit,
                "trainer": {"path": str(trainer), "sha256": identities["trainer"]},
                "trainer_helper": {
                    "path": str(trainer_helper),
                    "sha256": identities["trainer_helper"],
                    "exact_import_location": True,
                },
                "runtime_identity": {
                    "python_path": str(trainer_python),
                    "python_sha256": identities["python"],
                    "numpy_version": args.numpy_version.strip(),
                    "numpy_core_path": str(numpy_core),
                    "numpy_core_sha256": identities["numpy_core"],
                    "blas_path": str(blas_library),
                    "blas_sha256": identities["blas"],
                },
                "trainer_entrypoint": {
                    "path": str(trainer_entrypoint),
                    "sha256": identities["trainer_entrypoint"],
                    "is_observer_wrapper": trainer_entrypoint != trainer,
                },
                "checkpoint_dir": str(checkpoint_dir),
                "candidate_summary_path": str(candidate_summary),
                "candidate_weights_path": str(candidate_weights),
                "finite_observer_receipt_path": str(finite_observer_receipt),
                "finite_observer_required_steps": args.finite_observer_required_steps,
                "evaluation_dir": str(evaluation_dir),
                "shell_used": False,
            },
        )

        failed_state = "TRAINING"
        _status(
            run_dir,
            state="TRAINING_LAUNCH_PENDING",
            overall_status="RUNNING",
            started_utc=started_utc,
            detail="all hashes and exact trainer argv passed",
            checkpoint_dir=checkpoint_dir,
        )
        child_environment = os.environ.copy()
        thread_environment = {key: str(args.thread_limit) for key in THREAD_ENV_KEYS}
        child_environment.update(thread_environment)
        stdout_path = run_dir / "train_stdout.log"
        stderr_path = run_dir / "train_stderr.log"
        with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
            process = subprocess.Popen(
                trainer_argv,
                cwd=str(run_dir),
                env=child_environment,
                stdout=stdout_handle,
                stderr=stderr_handle,
                shell=False,
            )
            pid = process.pid
            launch_receipt = {
                "schema": "deployed100k_exact_contract_on_200k_launch_v1",
                "overall_status": "LAUNCHED",
                "controller_pid": os.getpid(),
                "trainer_pid": pid,
                "launched_utc": _utc_now(),
                "exact_train_argv_sha256": identities["trainer_argv"],
                "exact_train_command_sha256": _sha256(run_dir / "EXACT_TRAIN_COMMAND.txt"),
                "trainer_sha256": identities["trainer"],
                "trainer_helper_path": str(trainer_helper),
                "trainer_helper_sha256": identities["trainer_helper"],
                "python_sha256": identities["python"],
                "numpy_version": args.numpy_version.strip(),
                "numpy_core_sha256": identities["numpy_core"],
                "blas_sha256": identities["blas"],
                "trainer_entrypoint_path": str(trainer_entrypoint),
                "trainer_entrypoint_sha256": identities["trainer_entrypoint"],
                "trainer_entrypoint_is_observer_wrapper": trainer_entrypoint != trainer,
                "dataset_sha256": identities["dataset"],
                "thread_environment": thread_environment,
                "inherited_environment": True,
                "shell_used": False,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "checkpoint_dir": str(checkpoint_dir),
            }
            try:
                _write_json_exclusive(run_dir / "LAUNCH_RECEIPT.json", launch_receipt)
                _status(
                    run_dir,
                    state="TRAINING",
                    overall_status="RUNNING",
                    started_utc=started_utc,
                    pid=pid,
                    detail="trainer child is running",
                    checkpoint_dir=checkpoint_dir,
                    train_stdout_path=stdout_path,
                )
            except Exception as exc:
                _record_unmanaged_child_risk(
                    run_dir, child_kind="trainer", child_pid=pid, exc=exc
                )
                print(
                    f"CRITICAL: trainer pid {pid} continues unmanaged after receipt/status failure: {exc}",
                    file=sys.stderr,
                )
                return 3
            while True:
                try:
                    heartbeat_utc = _utc_now()
                    stdout_handle.write(
                        (
                            f"[controller-heartbeat] utc={heartbeat_utc} "
                            f"controller_pid={os.getpid()} trainer_pid={pid} "
                            "state=TRAINING\n"
                        ).encode("utf-8")
                    )
                    stdout_handle.flush()
                    os.fsync(stdout_handle.fileno())
                    _status(
                        run_dir,
                        state="TRAINING",
                        overall_status="RUNNING",
                        started_utc=started_utc,
                        pid=pid,
                        detail=f"UTC heartbeat {heartbeat_utc}; trainer child is running",
                        checkpoint_dir=checkpoint_dir,
                        train_stdout_path=stdout_path,
                    )
                except Exception as exc:
                    _record_unmanaged_child_risk(
                        run_dir, child_kind="trainer", child_pid=pid, exc=exc
                    )
                    print(
                        f"CRITICAL: trainer pid {pid} continues unmanaged after heartbeat/status failure: {exc}",
                        file=sys.stderr,
                    )
                    return 3
                try:
                    trainer_returncode = process.wait(timeout=args.heartbeat_seconds)
                    break
                except subprocess.TimeoutExpired:
                    continue
        if trainer_returncode != 0:
            raise ControllerError(f"trainer exited with return code {trainer_returncode}")

        finite_observer_record = _validate_finite_observer_receipt(
            finite_observer_receipt,
            required_steps=args.finite_observer_required_steps,
            expected_blas_path=blas_library,
            expected_blas_sha256=identities["blas"],
        )
        trainer_helper_pre_evaluation_sha256 = _require_sha(
            trainer_helper,
            identities["trainer_helper"],
            "trainer model-splitting helper before evaluation",
        )
        _require_sha(
            trainer_python,
            identities["python"],
            "trainer Python executable before evaluation",
        )
        _require_sha(
            numpy_core,
            identities["numpy_core"],
            "NumPy core binary before evaluation",
        )
        _require_sha(
            blas_library,
            identities["blas"],
            "BLAS library before evaluation",
        )
        _require_sha(
            reference_source,
            identities["reference_contract"],
            "reference contract before evaluation",
        )
        _require_sha(
            reference_summary,
            identities["reference_summary"],
            "reference 100k summary before evaluation",
        )
        _require_sha(
            reference_weights,
            identities["reference_weights"],
            "reference 100k weights before evaluation",
        )
        candidate_summary = _require_regular_file(candidate_summary, "candidate 200k summary")
        candidate_weights = _require_regular_file(candidate_weights, "candidate 200k weights")
        candidate_artifacts = {
            "summary": {
                "path": str(candidate_summary),
                "sha256": _sha256(candidate_summary),
                "size_bytes": candidate_summary.stat().st_size,
            },
            "weights": {
                "path": str(candidate_weights),
                "sha256": _sha256(candidate_weights),
                "size_bytes": candidate_weights.stat().st_size,
            },
        }
        if any(record["size_bytes"] <= 0 for record in candidate_artifacts.values()):
            raise ControllerError("candidate summary or weights is empty")
        checkpoint_names = tuple(
            args.required_checkpoint_artifact or DEFAULT_CHECKPOINT_ARTIFACTS
        )
        checkpoint_records = _required_files(
            checkpoint_dir, checkpoint_names, "checkpoint artifact"
        )
        _write_json_exclusive(
            run_dir / "TRAINING_RECEIPT.json",
            {
                "schema": "deployed100k_exact_contract_on_200k_training_terminal_v1",
                "overall_status": "PASS_TRAINER_EXIT_ZERO_CHECKPOINTS_PRESENT",
                "completed_utc": _utc_now(),
                "trainer_pid": pid,
                "trainer_returncode": trainer_returncode,
                "checkpoint_artifacts": checkpoint_records,
                "candidate_artifacts": candidate_artifacts,
                "finite_observer_receipt": finite_observer_record,
                "trainer_helper_sha256": trainer_helper_pre_evaluation_sha256,
                "python_sha256": identities["python"],
                "numpy_version": args.numpy_version.strip(),
                "numpy_core_sha256": identities["numpy_core"],
                "blas_sha256": identities["blas"],
                "train_stdout_sha256": _sha256(stdout_path),
                "train_stderr_sha256": _sha256(stderr_path),
            },
        )

        realized_evaluator_argv = _realize_evaluator_argv(
            evaluator_argv,
            summary_sha256=candidate_artifacts["summary"]["sha256"],
            weights_sha256=candidate_artifacts["weights"]["sha256"],
        )
        _write_json_exclusive(
            run_dir / "REALIZED_EVALUATION_ARGV.json",
            {
                "schema": "deployed100k_exact_contract_on_200k_realized_evaluation_argv_v1",
                "template_argv_sha256": identities["evaluator_argv"],
                "allowed_substitutions": {
                    SUMMARY_SHA_FLAG: candidate_artifacts["summary"]["sha256"],
                    WEIGHTS_SHA_FLAG: candidate_artifacts["weights"]["sha256"],
                },
                "argv": realized_evaluator_argv,
            },
        )
        _write_text_exclusive(
            run_dir / "REALIZED_EVALUATION_COMMAND.txt",
            shlex.join(realized_evaluator_argv) + "\n",
        )
        realized_evaluator_argv_sha256 = _sha256(
            run_dir / "REALIZED_EVALUATION_ARGV.json"
        )
        realized_evaluator_command_sha256 = _sha256(
            run_dir / "REALIZED_EVALUATION_COMMAND.txt"
        )

        failed_state = "EVALUATION"
        _status(
            run_dir,
            state="EVALUATION_LAUNCH_PENDING",
            overall_status="RUNNING",
            started_utc=started_utc,
            pid=pid,
            trainer_returncode=trainer_returncode,
            detail="training passed; launching frozen legacy-8k evaluator",
            checkpoint_dir=checkpoint_dir,
            train_stdout_path=stdout_path,
        )
        evaluation_stdout = run_dir / "evaluation_stdout.log"
        evaluation_stderr = run_dir / "evaluation_stderr.log"
        with evaluation_stdout.open("xb") as stdout_handle, evaluation_stderr.open(
            "xb"
        ) as stderr_handle:
            evaluator_prelaunch_sha256 = _require_sha(
                evaluator,
                identities["evaluator"],
                "evaluator immediately before launch",
            )
            evaluator_process = subprocess.Popen(
                realized_evaluator_argv,
                cwd=str(run_dir),
                env=child_environment,
                stdout=stdout_handle,
                stderr=stderr_handle,
                shell=False,
            )
            evaluator_pid = evaluator_process.pid
            try:
                _write_json_exclusive(
                    run_dir / "EVALUATION_LAUNCH_RECEIPT.json",
                    {
                        "schema": "deployed100k_exact_contract_on_200k_evaluation_launch_v1",
                        "overall_status": "LAUNCHED_AFTER_TRAINING_PASS",
                        "evaluator_pid": evaluator_pid,
                        "launched_utc": _utc_now(),
                        "evaluator_sha256": evaluator_prelaunch_sha256,
                        "evaluator_sha256_reverified_immediately_before_launch": True,
                        "fixed_targets_sha256": identities["fixed_targets"],
                        "template_evaluator_argv_sha256": identities["evaluator_argv"],
                        "realized_evaluator_argv_sha256": realized_evaluator_argv_sha256,
                        "realized_evaluator_command_sha256": realized_evaluator_command_sha256,
                        "candidate_artifacts": candidate_artifacts,
                        "reference_summary_sha256": identities["reference_summary"],
                        "reference_weights_sha256": identities["reference_weights"],
                        "trainer_helper_sha256": trainer_helper_pre_evaluation_sha256,
                        "shell_used": False,
                        "evaluation_dir": str(evaluation_dir),
                    },
                )
                _status(
                    run_dir,
                    state="EVALUATING",
                    overall_status="RUNNING",
                    started_utc=started_utc,
                    pid=pid,
                    trainer_returncode=trainer_returncode,
                    detail=f"evaluator child {evaluator_pid} is running",
                    checkpoint_dir=checkpoint_dir,
                    train_stdout_path=stdout_path,
                )
            except Exception as exc:
                _record_unmanaged_child_risk(
                    run_dir, child_kind="evaluator", child_pid=evaluator_pid, exc=exc
                )
                print(
                    f"CRITICAL: evaluator pid {evaluator_pid} continues unmanaged after receipt/status failure: {exc}",
                    file=sys.stderr,
                )
                return 3
            evaluator_returncode = evaluator_process.wait()
        if evaluator_returncode != 0:
            raise ControllerError(f"evaluator exited with return code {evaluator_returncode}")

        evaluation_names = tuple(
            args.required_evaluation_artifact or DEFAULT_EVALUATION_ARTIFACTS
        )
        evaluation_records = _required_files(
            evaluation_dir, evaluation_names, "evaluation artifact"
        )
        sums_record = _verify_sha256s_file(evaluation_dir, evaluation_records)
        _write_json_exclusive(
            run_dir / "EVALUATION_RECEIPT.json",
            {
                "schema": "deployed100k_exact_contract_on_200k_evaluation_terminal_v1",
                "overall_status": "PASS_LEGACY8K_EXPORTS_HASH_VERIFIED",
                "completed_utc": _utc_now(),
                "evaluator_pid": evaluator_pid,
                "evaluator_returncode": evaluator_returncode,
                "fixed_targets_sha256": identities["fixed_targets"],
                "reference_summary_sha256": identities["reference_summary"],
                "reference_weights_sha256": identities["reference_weights"],
                "candidate_artifacts": candidate_artifacts,
                "template_evaluator_argv_sha256": identities["evaluator_argv"],
                "realized_evaluator_argv_sha256": realized_evaluator_argv_sha256,
                "realized_evaluator_command_sha256": realized_evaluator_command_sha256,
                "evaluation_artifacts": evaluation_records,
                "sha256s": sums_record,
                "evaluation_stdout_sha256": _sha256(evaluation_stdout),
                "evaluation_stderr_sha256": _sha256(evaluation_stderr),
            },
        )
        _write_json_exclusive(
            run_dir / "COMPLETE_RECEIPT.json",
            {
                "schema": "deployed100k_exact_contract_on_200k_complete_v1",
                "overall_status": "COMPLETE_TRAINING_AND_LEGACY8K_EVALUATION_PASS",
                "started_utc": started_utc,
                "completed_utc": _utc_now(),
                "trainer_pid": pid,
                "trainer_returncode": trainer_returncode,
                "evaluator_pid": evaluator_pid,
                "evaluator_returncode": evaluator_returncode,
                "reference_contract_sha256": identities["reference_contract"],
                "dataset_binding_sha256": identities["dataset_binding"],
                "trainer_sha256": identities["trainer"],
                "trainer_entrypoint_sha256": identities["trainer_entrypoint"],
                "trainer_helper_sha256": trainer_helper_pre_evaluation_sha256,
                "python_sha256": identities["python"],
                "numpy_version": args.numpy_version.strip(),
                "numpy_core_sha256": identities["numpy_core"],
                "blas_sha256": identities["blas"],
                "dataset_sha256": identities["dataset"],
                "fixed_targets_sha256": identities["fixed_targets"],
                "reference_summary_sha256": identities["reference_summary"],
                "reference_weights_sha256": identities["reference_weights"],
                "candidate_summary_sha256": candidate_artifacts["summary"]["sha256"],
                "candidate_weights_sha256": candidate_artifacts["weights"]["sha256"],
                "finite_observer_receipt_sha256": finite_observer_record["sha256"],
                "template_evaluator_argv_sha256": identities["evaluator_argv"],
                "realized_evaluator_argv_sha256": realized_evaluator_argv_sha256,
            },
        )
        _status(
            run_dir,
            state="COMPLETE",
            overall_status="PASS",
            started_utc=started_utc,
            pid=pid,
            trainer_returncode=trainer_returncode,
            evaluator_returncode=evaluator_returncode,
            detail="training and frozen legacy-8k evaluation completed",
            checkpoint_dir=checkpoint_dir,
            train_stdout_path=stdout_path,
        )
        return 0
    except Exception as exc:
        _record_failure(
            run_dir,
            state=failed_state,
            started_utc=started_utc,
            exc=exc,
            pid=pid,
            trainer_returncode=trainer_returncode,
            evaluator_returncode=evaluator_returncode,
            checkpoint_dir=locals().get("checkpoint_dir"),
            train_stdout_path=locals().get("stdout_path"),
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
