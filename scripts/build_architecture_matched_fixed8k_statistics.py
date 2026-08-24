#!/usr/bin/env python3
"""Build fail-closed paired legacy8k statistics from a completed controller run.

This module is intentionally post-training only.  It never starts, stops,
signals, or monitors training.  It accepts only a terminal controller run,
verifies all receipt and artifact bindings, and then recomputes the requested
statistics from the two immutable per-target prediction exports.

The production contract is locked to the current project-owner decision that
``|K|`` uses a normalization span of 0.8.  Synthetic-fixture mode exists only
for targeted tests and is restricted to the platform temporary directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMAL_REPORT_ROOT = (PROJECT_ROOT / "reports").resolve()
FORMAL_STAGING_PREFIX = ".architecture_matched_100k_vs_200k_fixed8k_v1_"

EXPECTED_RUN_ID = "deployed100k_exact_contract_on_200k_20260824T005653Z"
EXPECTED_TRAINER_PID = 199953
FROZEN_FIXED10K_SHA256 = (
    "c9d7d8bc7f65a488be0805969389a01ef049534eefdfdea71cbd640ee27d6407"
)
EXPECTED_TARGET_FRAME_ROWS = 10_000
EXPECTED_LEGACY_ROWS = 8_000
EXPECTED_REFERENCE_MODEL_ID = "current_foundry_qmin_response_only_seed20260713"
REFERENCE_DISPLAY_NAME = "Previous-presentation 100k reference"
CANDIDATE_DISPLAY_NAME = "architecture-matched 200k model"
EVIDENCE_LABEL = "proxy-only evidence"
PANEL = "legacy_k_le_0p8"
PROJECTION_MODE = "hard_feasible_topology_v1"
EXPECTED_FORWARD_ARCHITECTURE = (10, 256, 256, 128, 4)
EXPECTED_INVERSE_ARCHITECTURE = (4, 512, 512, 256, 10)
EXPECTED_PARAMETER_COUNT = 501_134
BOOTSTRAP_SEED = 20260824
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_LABEL = "paired finite-frame bootstrap sensitivity"
SWEEP_LABEL = "descriptive sensitivity curve"

# The project owner explicitly overrode the earlier request to use |K|=1.
# This value must be used consistently by every normalized statistic.
FEATURES = OrderedDict(
    (
        ("Lp", {"suffix": "lp", "unit": "nH", "span": 2.5}),
        ("Ls", {"suffix": "ls", "unit": "nH", "span": 2.5}),
        ("Qmin", {"suffix": "qmin", "unit": "dimensionless", "span": 20.0}),
        ("|K|", {"suffix": "k_abs", "unit": "dimensionless", "span": 0.8}),
    )
)
FEATURE_NAMES = tuple(FEATURES)
FEATURE_SUFFIXES = tuple(value["suffix"] for value in FEATURES.values())
NORMALIZATION_SPANS = np.asarray(
    [float(value["span"]) for value in FEATURES.values()], dtype=float
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
TARGET_JSON_KEYS = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")

RECEIPT_CONTRACTS = {
    "RUN_STATUS.json": (
        "deployed100k_exact_contract_on_200k_controller_v1",
        "PASS",
    ),
    "PREFLIGHT_RECEIPT.json": (
        "deployed100k_exact_contract_on_200k_preflight_v1",
        "PASS_HASH_AND_ARGV_BOUND",
    ),
    "LAUNCH_RECEIPT.json": (
        "deployed100k_exact_contract_on_200k_launch_v1",
        "LAUNCHED",
    ),
    "TRAINING_RECEIPT.json": (
        "deployed100k_exact_contract_on_200k_training_terminal_v1",
        "PASS_TRAINER_EXIT_ZERO_CHECKPOINTS_PRESENT",
    ),
    "EVALUATION_LAUNCH_RECEIPT.json": (
        "deployed100k_exact_contract_on_200k_evaluation_launch_v1",
        "LAUNCHED_AFTER_TRAINING_PASS",
    ),
    "EVALUATION_RECEIPT.json": (
        "deployed100k_exact_contract_on_200k_evaluation_terminal_v1",
        "PASS_LEGACY8K_EXPORTS_HASH_VERIFIED",
    ),
    "COMPLETE_RECEIPT.json": (
        "deployed100k_exact_contract_on_200k_complete_v1",
        "COMPLETE_TRAINING_AND_LEGACY8K_EVALUATION_PASS",
    ),
}


class ContractError(RuntimeError):
    """A release-blocking contract failure."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    _require(path.is_file() and path.stat().st_size > 0, f"missing {label}: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid {label}: {path.name}: {exc}") from exc
    _require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def _csv_fieldnames(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    fieldnames: List[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def _write_csv_exclusive(path: Path, rows: Sequence[Mapping[str, Any]]) -> int:
    _require(bool(rows), f"refusing to write empty CSV: {path.name}")
    fieldnames = _csv_fieldnames(rows)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _read_csv(path: Path, label: str) -> Tuple[List[Dict[str, str]], List[str]]:
    _require(path.is_file() and path.stat().st_size > 0, f"missing {label}: {path.name}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            _require(reader.fieldnames is not None, f"{label} has no header")
            rows = [dict(row) for row in reader]
            return rows, list(reader.fieldnames)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContractError(f"invalid {label}: {path.name}: {exc}") from exc


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} is not numeric") from exc
    _require(math.isfinite(result), f"{label} is NaN or Inf")
    return result


def _parse_bool(value: Any, label: str) -> bool:
    if type(value) is bool:
        return value
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ContractError(f"{label} is not boolean")


def _parse_utc(value: Any, label: str) -> datetime:
    text = str(value or "").strip()
    _require(bool(text), f"{label} timestamp is absent")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} timestamp is invalid") from exc
    _require(parsed.tzinfo is not None, f"{label} timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _duration_seconds(start: Any, end: Any, label: str) -> float:
    duration = (_parse_utc(end, f"{label} end") - _parse_utc(start, f"{label} start")).total_seconds()
    _require(math.isfinite(duration) and duration >= 0.0, f"{label} duration is invalid")
    return float(duration)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_source(path: Path) -> Dict[str, Any]:
    return {
        "file_name": path.name,
        "sha256": _sha256(path),
        "size_bytes": int(path.stat().st_size),
    }


def _require_sha(path: Path, expected: Any, label: str) -> str:
    normalized = str(expected or "").strip().lower()
    _require(
        len(normalized) == 64 and all(ch in "0123456789abcdef" for ch in normalized),
        f"{label} expected SHA256 is malformed",
    )
    _require(path.is_file() and path.stat().st_size > 0, f"missing {label}: {path.name}")
    actual = _sha256(path)
    _require(actual == normalized, f"{label} SHA256 mismatch")
    return actual


def _receipt_path_record(record: Any, label: str) -> Tuple[Path, str]:
    _require(isinstance(record, dict), f"{label} record is absent")
    path = Path(str(record.get("path") or "")).expanduser().resolve()
    digest = str(record.get("sha256") or "")
    if "size_bytes" in record:
        _require(int(record.get("size_bytes") or 0) > 0, f"{label} size is invalid")
    _require_sha(path, digest, label)
    return path, digest


def _argv_flag_map(argv: Any) -> Dict[str, str]:
    _require(isinstance(argv, list) and all(isinstance(item, str) for item in argv), "realized evaluator argv is invalid")
    result: Dict[str, str] = {}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item.startswith("--"):
            _require(index + 1 < len(argv), f"realized evaluator flag lacks value: {item}")
            _require(item not in result, f"realized evaluator flag is duplicated: {item}")
            result[item] = argv[index + 1]
            index += 2
        else:
            index += 1
    return result


def _required_evaluator_flags() -> Tuple[str, ...]:
    flags: List[str] = []
    for role in ("100k", "200k"):
        flags.extend(
            [
                f"--model-{role}-id",
                f"--model-{role}-summary",
                f"--model-{role}-weights",
                f"--model-{role}-trainer-source",
                f"--expected-model-{role}-summary-sha256",
                f"--expected-model-{role}-weights-sha256",
                f"--expected-model-{role}-trainer-sha256",
            ]
        )
    flags.extend(
        [
            "--targets-json",
            "--expected-targets-sha256",
            "--reference-contract",
            "--expected-reference-contract-sha256",
            "--out-dir",
        ]
    )
    return tuple(flags)


def discover_controller_bundle(
    controller_run_dir: Path,
    expected_run_id: str,
    expected_trainer_pid: int,
) -> Dict[str, Any]:
    """Validate terminal receipts before opening weights or predictions."""

    run_dir = controller_run_dir.expanduser().resolve()
    _require(run_dir.is_dir() and not run_dir.is_symlink(), "controller run directory is missing or unsafe")
    _require(run_dir.name == expected_run_id, "controller run_id does not match the authorized run")

    receipts: Dict[str, Dict[str, Any]] = {}
    receipt_sources: Dict[str, Dict[str, Any]] = {}
    for filename, (schema, status) in RECEIPT_CONTRACTS.items():
        path = run_dir / filename
        payload = _read_json(path, filename)
        _require(payload.get("schema") == schema, f"{filename} schema mismatch")
        _require(payload.get("overall_status") == status, f"{filename} terminal status is not PASS")
        receipts[filename] = payload
        receipt_sources[filename] = _safe_source(path)

    run_status = receipts["RUN_STATUS.json"]
    _require(run_status.get("state") == "COMPLETE", "RUN_STATUS is not terminal COMPLETE")
    _require(int(run_status.get("trainer_returncode")) == 0, "trainer return code is not zero")
    _require(int(run_status.get("evaluator_returncode")) == 0, "evaluator return code is not zero")

    preflight = receipts["PREFLIGHT_RECEIPT.json"]
    launch = receipts["LAUNCH_RECEIPT.json"]
    training = receipts["TRAINING_RECEIPT.json"]
    evaluation_launch = receipts["EVALUATION_LAUNCH_RECEIPT.json"]
    evaluation = receipts["EVALUATION_RECEIPT.json"]
    complete = receipts["COMPLETE_RECEIPT.json"]
    identities = preflight.get("identities")
    _require(isinstance(identities, dict), "preflight identity map is absent")
    identity_fields = (
        "reference_contract",
        "dataset_binding",
        "trainer",
        "trainer_helper",
        "python",
        "numpy_core",
        "blas",
        "trainer_entrypoint",
        "dataset",
        "reference_summary",
        "reference_weights",
        "fixed_targets",
        "evaluator",
        "trainer_argv",
        "evaluator_argv",
    )
    for field in identity_fields:
        digest = str(identities.get(field) or "")
        _require(
            len(digest) == 64 and all(character in "0123456789abcdef" for character in digest),
            f"preflight identity {field} is malformed",
        )
    _require((preflight.get("trainer") or {}).get("sha256") == identities["trainer"], "preflight trainer record does not close")
    _require((preflight.get("trainer_helper") or {}).get("sha256") == identities["trainer_helper"], "preflight trainer helper record does not close")
    _require((preflight.get("trainer_helper") or {}).get("exact_import_location") is True, "preflight trainer helper import location is not exact")
    runtime_identity = preflight.get("runtime_identity") or {}
    for receipt_field, identity_field in (
        ("python_sha256", "python"),
        ("numpy_core_sha256", "numpy_core"),
        ("blas_sha256", "blas"),
    ):
        _require(runtime_identity.get(receipt_field) == identities[identity_field], f"preflight runtime identity {receipt_field} does not close")
    _require((preflight.get("trainer_entrypoint") or {}).get("sha256") == identities["trainer_entrypoint"], "preflight trainer entrypoint does not close")

    for payload, receipt_field, identity_field, label in (
        (launch, "exact_train_argv_sha256", "trainer_argv", "launch trainer argv"),
        (launch, "trainer_sha256", "trainer", "launch trainer"),
        (launch, "trainer_helper_sha256", "trainer_helper", "launch trainer helper"),
        (launch, "trainer_entrypoint_sha256", "trainer_entrypoint", "launch trainer entrypoint"),
        (launch, "dataset_sha256", "dataset", "launch dataset"),
        (evaluation_launch, "evaluator_sha256", "evaluator", "evaluation launch evaluator"),
        (evaluation_launch, "template_evaluator_argv_sha256", "evaluator_argv", "evaluation template argv"),
        (complete, "reference_contract_sha256", "reference_contract", "complete reference contract"),
        (complete, "dataset_binding_sha256", "dataset_binding", "complete dataset binding"),
        (complete, "trainer_sha256", "trainer", "complete trainer"),
        (complete, "trainer_entrypoint_sha256", "trainer_entrypoint", "complete trainer entrypoint"),
        (complete, "trainer_helper_sha256", "trainer_helper", "complete trainer helper"),
        (complete, "dataset_sha256", "dataset", "complete dataset"),
        (complete, "fixed_targets_sha256", "fixed_targets", "complete fixed targets"),
        (complete, "reference_summary_sha256", "reference_summary", "complete reference summary"),
        (complete, "reference_weights_sha256", "reference_weights", "complete reference weights"),
        (complete, "template_evaluator_argv_sha256", "evaluator_argv", "complete evaluator argv"),
    ):
        _require(payload.get(receipt_field) == identities[identity_field], f"{label} identity does not close to preflight")
    for payload, label in ((launch, "launch"), (training, "training"), (complete, "complete")):
        for receipt_field, identity_field in (
            ("python_sha256", "python"),
            ("numpy_core_sha256", "numpy_core"),
            ("blas_sha256", "blas"),
        ):
            _require(payload.get(receipt_field) == identities[identity_field], f"{label} {receipt_field} does not close to preflight")
    numpy_version = str(runtime_identity.get("numpy_version") or "")
    _require(bool(numpy_version), "preflight NumPy version is absent")
    _require(all(str(payload.get("numpy_version") or "") == numpy_version for payload in (launch, training, complete)), "NumPy version receipts do not close")
    for payload, field, label in (
        (launch, "trainer_pid", "launch"),
        (training, "trainer_pid", "training"),
        (complete, "trainer_pid", "complete"),
    ):
        _require(int(payload.get(field)) == expected_trainer_pid, f"{label} trainer pid mismatch")
    _require(int(training.get("trainer_returncode")) == 0, "training receipt return code is not zero")
    _require(int(evaluation.get("evaluator_returncode")) == 0, "evaluation receipt return code is not zero")
    _require(int(complete.get("trainer_returncode")) == 0, "complete receipt trainer return code is not zero")
    _require(int(complete.get("evaluator_returncode")) == 0, "complete receipt evaluator return code is not zero")

    finite_observer = training.get("finite_observer_receipt")
    _require(isinstance(finite_observer, dict), "training receipt lacks finite observer record")
    _require(finite_observer.get("status") == "PASS", "finite observer status is not PASS")
    _require(finite_observer.get("runtime_checks_all_true") is True, "finite observer runtime checks are not PASS")
    _require(finite_observer.get("loaded_blas_sha_set_exact") is True, "finite observer BLAS identity is not exact")
    observed_steps = finite_observer.get("observed_steps") or {}
    _require(
        isinstance(observed_steps, dict)
        and all(int(observed_steps.get(stage) or 0) > 0 for stage in ("forward_proxy", "tandem_inverse")),
        "finite observer stage coverage is incomplete",
    )
    finite_path, finite_sha = _receipt_path_record(finite_observer, "finite observer receipt")
    _require(complete.get("finite_observer_receipt_sha256") == finite_sha, "complete receipt does not bind finite observer")
    finite_payload = _read_json(finite_path, "finite observer receipt")
    _require(finite_payload.get("schema") == "exact_trainer_finite_update_observer_v1", "finite observer receipt schema mismatch")
    _require(finite_payload.get("status") == "PASS", "finite observer receipt payload is not PASS")

    candidate_training = training.get("candidate_artifacts")
    candidate_evaluation = evaluation.get("candidate_artifacts")
    _require(isinstance(candidate_training, dict) and isinstance(candidate_evaluation, dict), "candidate artifact records are absent")
    candidate_paths: Dict[str, Path] = {}
    candidate_hashes: Dict[str, str] = {}
    for kind in ("summary", "weights"):
        path, digest = _receipt_path_record(candidate_training.get(kind), f"candidate {kind}")
        eval_path, eval_digest = _receipt_path_record(candidate_evaluation.get(kind), f"evaluation candidate {kind}")
        _require(path == eval_path and digest == eval_digest, f"candidate {kind} receipts do not close")
        launch_candidate = (evaluation_launch.get("candidate_artifacts") or {}).get(kind)
        launch_path, launch_digest = _receipt_path_record(launch_candidate, f"evaluation launch candidate {kind}")
        _require(path == launch_path and digest == launch_digest, f"evaluation launch does not bind candidate {kind}")
        _require(complete.get(f"candidate_{kind}_sha256") == digest, f"complete receipt does not bind candidate {kind}")
        candidate_paths[kind] = path
        candidate_hashes[kind] = digest

    for field in ("fixed_targets_sha256", "reference_summary_sha256", "reference_weights_sha256"):
        _require(
            str(evaluation.get(field) or "") == str(complete.get(field) or ""),
            f"evaluation and complete receipts disagree on {field}",
        )

    realized_path = run_dir / "REALIZED_EVALUATION_ARGV.json"
    realized = _read_json(realized_path, "REALIZED_EVALUATION_ARGV.json")
    _require(realized.get("schema") == "deployed100k_exact_contract_on_200k_realized_evaluation_argv_v1", "realized evaluator argv schema mismatch")
    _require(realized.get("template_argv_sha256") == identities["evaluator_argv"], "realized evaluator argv does not bind the preflight template")
    _require(evaluation.get("template_evaluator_argv_sha256") == identities["evaluator_argv"], "evaluation receipt does not bind the preflight evaluator template")
    realized_sha = _sha256(realized_path)
    for payload, label in (
        (evaluation_launch, "evaluation launch"),
        (evaluation, "evaluation terminal"),
        (complete, "complete"),
    ):
        _require(payload.get("realized_evaluator_argv_sha256") == realized_sha, f"{label} receipt does not bind realized evaluator argv")
    realized_command_path = run_dir / "REALIZED_EVALUATION_COMMAND.txt"
    realized_command_sha = _sha256(realized_command_path)
    _require(evaluation_launch.get("realized_evaluator_command_sha256") == realized_command_sha, "evaluation launch receipt does not bind realized evaluator command")
    _require(evaluation.get("realized_evaluator_command_sha256") == realized_command_sha, "evaluation terminal receipt does not bind realized evaluator command")
    argv = realized.get("argv")
    flags = _argv_flag_map(argv)
    missing = [flag for flag in _required_evaluator_flags() if flag not in flags]
    _require(not missing, f"realized evaluator argv is missing flags: {missing}")
    _require(flags["--model-100k-id"] == EXPECTED_REFERENCE_MODEL_ID, "reference model id is not the authorized previous-presentation model")
    _require(Path(flags["--model-200k-summary"]).expanduser().resolve() == candidate_paths["summary"], "realized argv candidate summary differs from receipts")
    _require(Path(flags["--model-200k-weights"]).expanduser().resolve() == candidate_paths["weights"], "realized argv candidate weights differs from receipts")
    _require(flags["--expected-model-200k-summary-sha256"] == candidate_hashes["summary"], "realized argv candidate summary SHA differs")
    _require(flags["--expected-model-200k-weights-sha256"] == candidate_hashes["weights"], "realized argv candidate weights SHA differs")

    evaluation_dir = Path(flags["--out-dir"]).expanduser().resolve()
    _require(evaluation_dir.is_dir(), "evaluation directory is absent")
    evaluation_records: Dict[str, Dict[str, Any]] = {}
    for record in evaluation.get("evaluation_artifacts") or []:
        _require(isinstance(record, dict), "evaluation artifact record is invalid")
        relative = str(record.get("relative_path") or "")
        _require(relative and relative not in evaluation_records, "evaluation artifact names are empty or duplicated")
        path, digest = _receipt_path_record(record, f"evaluation artifact {relative}")
        _require(path == (evaluation_dir / relative).resolve() and _is_within(path, evaluation_dir), f"evaluation artifact path is unsafe: {relative}")
        evaluation_records[relative] = {"path": path, "sha256": digest, "size_bytes": int(record.get("size_bytes") or 0)}
    required_evaluation = {
        "per_target_100k_predictions.csv",
        "per_target_200k_predictions.csv",
        "architecture_matched_comparison.csv",
        "evaluation_summary.json",
    }
    _require(required_evaluation.issubset(evaluation_records), "evaluation receipt lacks required artifacts")
    sums_path, sums_sha = _receipt_path_record(evaluation.get("sha256s"), "evaluation SHA256SUMS")
    _require(sums_path == (evaluation_dir / "SHA256SUMS.txt").resolve(), "evaluation SHA256SUMS path differs from evaluation directory")
    declared_sums: Dict[str, str] = {}
    for line_number, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split(maxsplit=1)
        _require(len(fields) == 2, f"malformed evaluation SHA256SUMS line {line_number}")
        relative = fields[1].strip().lstrip("*")
        _require(relative and relative not in declared_sums and not Path(relative).is_absolute() and ".." not in Path(relative).parts, "unsafe or duplicate evaluation SHA256SUMS entry")
        declared_sums[relative] = fields[0].strip().lower()
    for relative, record in evaluation_records.items():
        _require(declared_sums.get(relative) == record["sha256"], f"evaluation SHA256SUMS does not bind {relative}")

    target_path = Path(flags["--targets-json"]).expanduser().resolve()
    reference_summary_path = Path(flags["--model-100k-summary"]).expanduser().resolve()
    reference_weights_path = Path(flags["--model-100k-weights"]).expanduser().resolve()
    trainer_100k_path = Path(flags["--model-100k-trainer-source"]).expanduser().resolve()
    trainer_200k_path = Path(flags["--model-200k-trainer-source"]).expanduser().resolve()
    reference_contract_path = Path(flags["--reference-contract"]).expanduser().resolve()
    _require_sha(target_path, flags["--expected-targets-sha256"], "fixed targets")
    _require_sha(reference_summary_path, flags["--expected-model-100k-summary-sha256"], "reference summary")
    _require_sha(reference_weights_path, flags["--expected-model-100k-weights-sha256"], "reference weights")
    _require_sha(trainer_100k_path, flags["--expected-model-100k-trainer-sha256"], "reference trainer")
    _require_sha(trainer_200k_path, flags["--expected-model-200k-trainer-sha256"], "candidate trainer")
    _require(trainer_100k_path == trainer_200k_path, "model trainer source paths differ")
    preflight_trainer_path = Path(str((preflight.get("trainer") or {}).get("path") or "")).expanduser().resolve()
    _require(preflight_trainer_path == trainer_100k_path, "preflight and evaluator trainer source paths differ")
    _require(flags["--expected-model-100k-trainer-sha256"] == flags["--expected-model-200k-trainer-sha256"], "model trainer source hashes differ")
    _require_sha(reference_contract_path, flags["--expected-reference-contract-sha256"], "reference contract")
    _require(complete.get("reference_contract_sha256") == flags["--expected-reference-contract-sha256"], "complete receipt reference contract SHA differs")
    _require(complete.get("trainer_sha256") == flags["--expected-model-100k-trainer-sha256"], "complete receipt does not bind evaluator trainer source")
    helper_hashes = {
        str(payload.get("trainer_helper_sha256") or "")
        for payload in (launch, training, evaluation_launch, complete)
    }
    _require(len(helper_hashes) == 1 and "" not in helper_hashes, "trainer helper SHA receipts do not close")
    trainer_helper_path = Path(str(launch.get("trainer_helper_path") or "")).expanduser().resolve()
    _require_sha(trainer_helper_path, next(iter(helper_hashes)), "trainer model-splitting helper")
    preflight_helper_path = Path(str((preflight.get("trainer_helper") or {}).get("path") or "")).expanduser().resolve()
    _require(preflight_helper_path == trainer_helper_path, "preflight and launch trainer helper paths differ")
    _require(complete.get("fixed_targets_sha256") == flags["--expected-targets-sha256"], "complete receipt target SHA differs from realized argv")
    _require(complete.get("reference_summary_sha256") == flags["--expected-model-100k-summary-sha256"], "complete receipt reference summary SHA differs")
    _require(complete.get("reference_weights_sha256") == flags["--expected-model-100k-weights-sha256"], "complete receipt reference weights SHA differs")

    evaluator_path: Optional[Path] = None
    if isinstance(argv, list):
        for item in argv:
            if not str(item).startswith("--") and str(item).endswith(".py"):
                candidate = Path(str(item)).expanduser().resolve()
                if candidate.name == "evaluate_architecture_matched_fixed8k.py":
                    evaluator_path = candidate
                    break
    _require(evaluator_path is not None and evaluator_path.is_file(), "hash-bound evaluator source path cannot be recovered")
    _require(_sha256(evaluator_path) == str(evaluation_launch.get("evaluator_sha256") or ""), "evaluator source does not match evaluation launch receipt")
    evaluator_pid = int(evaluation_launch.get("evaluator_pid"))
    _require(int(evaluation.get("evaluator_pid")) == evaluator_pid, "evaluation receipt evaluator pid differs from launch")
    _require(int(complete.get("evaluator_pid")) == evaluator_pid, "complete receipt evaluator pid differs from launch")
    _require(evaluation_launch.get("fixed_targets_sha256") == flags["--expected-targets-sha256"], "evaluation launch target SHA differs")
    _require(evaluation_launch.get("reference_summary_sha256") == flags["--expected-model-100k-summary-sha256"], "evaluation launch reference summary SHA differs")
    _require(evaluation_launch.get("reference_weights_sha256") == flags["--expected-model-100k-weights-sha256"], "evaluation launch reference weights SHA differs")

    return {
        "run_dir": run_dir,
        "expected_run_id": expected_run_id,
        "expected_trainer_pid": expected_trainer_pid,
        "receipts": receipts,
        "receipt_sources": receipt_sources,
        "finite_observer_source": _safe_source(finite_path),
        "realized_argv_source": _safe_source(realized_path),
        "realized_command_source": _safe_source(realized_command_path),
        "flags": flags,
        "model_ids": {"100k": flags["--model-100k-id"], "200k": flags["--model-200k-id"]},
        "target_path": target_path,
        "reference_contract_path": reference_contract_path,
        "summary_paths": {"100k": reference_summary_path, "200k": candidate_paths["summary"]},
        "weights_paths": {"100k": reference_weights_path, "200k": candidate_paths["weights"]},
        "trainer_path": trainer_100k_path,
        "trainer_helper_path": trainer_helper_path,
        "evaluator_path": evaluator_path,
        "evaluation_dir": evaluation_dir,
        "evaluation_records": evaluation_records,
        "evaluation_sha256s_source": _safe_source(sums_path),
    }


def _archive_scalar_string(archive: Any, key: str) -> str:
    _require(key in archive.files, f"weights archive is missing {key}")
    values = np.asarray(archive[key]).reshape(-1)
    _require(values.size == 1, f"weights field {key} is not scalar")
    return str(values[0])


def _numbered_arrays(archive: Any, prefix: str) -> List[np.ndarray]:
    indexed: List[Tuple[int, np.ndarray]] = []
    for key in archive.files:
        if key.startswith(prefix):
            suffix = key[len(prefix) :]
            _require(suffix.isdigit(), f"malformed weights key: {key}")
            indexed.append((int(suffix), np.asarray(archive[key], dtype=float)))
    indexed.sort(key=lambda item: item[0])
    _require([index for index, _ in indexed] == list(range(len(indexed))), f"weights keys are not contiguous for {prefix}")
    return [value for _, value in indexed]


def _layer_widths(weights: Sequence[np.ndarray]) -> Tuple[int, ...]:
    _require(bool(weights) and all(value.ndim == 2 for value in weights), "model weights are malformed")
    widths = [int(weights[0].shape[0])]
    previous = widths[0]
    for value in weights:
        _require(value.shape[0] == previous, "model layer shapes do not connect")
        previous = int(value.shape[1])
        widths.append(previous)
    return tuple(widths)


def _parameter_count(weights: Sequence[np.ndarray], biases: Sequence[np.ndarray]) -> int:
    _require(len(weights) == len(biases), "weight and bias layer counts differ")
    total = 0
    for weight, bias in zip(weights, biases):
        _require(bias.ndim == 1 and bias.shape[0] == weight.shape[1], "bias shape does not match weight")
        total += int(weight.size + bias.size)
    return total


def _load_weight_contract(path: Path) -> Dict[str, Any]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            forward_weights = _numbered_arrays(archive, "forward_weight_")
            forward_biases = _numbered_arrays(archive, "forward_bias_")
            inverse_weights = _numbered_arrays(archive, "inverse_weight_")
            inverse_biases = _numbered_arrays(archive, "inverse_bias_")
            _require(
                all(
                    np.all(np.isfinite(array))
                    for array in (
                        *forward_weights,
                        *forward_biases,
                        *inverse_weights,
                        *inverse_biases,
                    )
                ),
                "terminal model parameters contain NaN or Inf",
            )
            model = {
                "forward_architecture": _layer_widths(forward_weights),
                "inverse_architecture": _layer_widths(inverse_weights),
                "forward_parameter_count": _parameter_count(forward_weights, forward_biases),
                "inverse_parameter_count": _parameter_count(inverse_weights, inverse_biases),
                "x_mean": np.asarray(archive["normalization__x_mean"], dtype=float),
                "x_scale": np.asarray(archive["normalization__x_scale"], dtype=float),
                "y_mean": np.asarray(archive["normalization__y_mean"], dtype=float),
                "y_scale": np.asarray(archive["normalization__y_scale"], dtype=float),
                "geometry_lower": np.asarray(archive["normalization__geometry_lower"], dtype=float),
                "geometry_upper": np.asarray(archive["normalization__geometry_upper"], dtype=float),
                "projection_mode": _archive_scalar_string(archive, "inverse_geometry_projection__mode"),
                "topology_contract": json.loads(_archive_scalar_string(archive, "inverse_geometry_projection__topology_contract_json")),
            }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid terminal weights archive {path.name}: {exc}") from exc
    model["parameter_count"] = int(model["forward_parameter_count"] + model["inverse_parameter_count"])
    _require(model["forward_architecture"] == EXPECTED_FORWARD_ARCHITECTURE, "forward architecture differs from contract")
    _require(model["inverse_architecture"] == EXPECTED_INVERSE_ARCHITECTURE, "inverse architecture differs from contract")
    _require(model["parameter_count"] == EXPECTED_PARAMETER_COUNT, "parameter count differs from contract")
    _require(model["projection_mode"] == PROJECTION_MODE, "decoder differs from contract")
    topology = model["topology_contract"]
    _require(isinstance(topology, dict) and topology.get("available") is True, "topology contract is unavailable")
    _require(bool((topology.get("power_line_port_ground_overlap") or {}).get("enabled")), "power-line topology contract is disabled")
    expected_shapes = {
        "x_mean": (4,),
        "x_scale": (4,),
        "y_mean": (10,),
        "y_scale": (10,),
        "geometry_lower": (10,),
        "geometry_upper": (10,),
    }
    for key, shape in expected_shapes.items():
        value = np.asarray(model[key], dtype=float)
        _require(value.shape == shape and np.all(np.isfinite(value)), f"weights {key} is malformed")
    _require(np.all(model["x_scale"] > 0.0) and np.all(model["y_scale"] > 0.0), "normalization scales are not positive")
    _require(np.all(model["geometry_upper"] > model["geometry_lower"]), "geometry bounds are not ordered")
    model["physical_lower"] = model["geometry_lower"] * model["y_scale"] + model["y_mean"]
    model["physical_upper"] = model["geometry_upper"] * model["y_scale"] + model["y_mean"]
    return model


def _parse_hidden_widths(value: Any) -> Tuple[int, ...]:
    if isinstance(value, str):
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(int(item) for item in value)
    return ()


def _validate_model_summary(summary: Dict[str, Any], role: str, model_id: str, weight_sha: str) -> Dict[str, Any]:
    expected_count = 100_000 if role == "100k" else 200_000
    _require(summary.get("execution_status") == "PASS", f"{role} model summary is not execution PASS")
    _require(int(summary.get("training_count") or 0) == expected_count, f"{role} source-table count differs")
    _require(str(summary.get("weights_npz_sha256") or "") == weight_sha, f"{role} summary does not bind terminal weights")
    recorded_id = str(summary.get("model_id") or "")
    _require(not recorded_id or recorded_id == model_id, f"{role} summary model id differs")
    arguments = summary.get("arguments") or {}
    method = summary.get("method") or {}
    _require(_parse_hidden_widths(arguments.get("forward_hidden_widths")) == EXPECTED_FORWARD_ARCHITECTURE[1:-1], f"{role} forward summary architecture differs")
    _require(_parse_hidden_widths(arguments.get("inverse_hidden_widths")) == EXPECTED_INVERSE_ARCHITECTURE[1:-1], f"{role} inverse summary architecture differs")
    _require(arguments.get("inverse_geometry_projection") == PROJECTION_MODE, f"{role} summary decoder differs")
    _require(arguments.get("q_target_semantics") == "minimum", f"{role} summary lacks Q-minimum semantics")
    _require(method.get("geometry_output_constraint") == PROJECTION_MODE, f"{role} method decoder differs")
    _require(method.get("geometry_output_constraint_is_single_pass") is True, f"{role} decoder is not one-shot")
    _require(method.get("geometry_output_constraint_is_posthoc_repair") is False, f"{role} decoder is post-hoc repair")
    counts = (summary.get("split_audit") or {}).get("row_counts") or {}
    split = {name: int(counts.get(name) or 0) for name in ("train", "validation", "test")}
    _require(all(value > 0 for value in split.values()), f"{role} split counts are incomplete")
    _require(sum(split.values()) == expected_count, f"{role} split counts do not close")
    history_path = Path(str(summary.get("history_csv") or "")).expanduser().resolve()
    history_sha = str(summary.get("history_csv_sha256") or "")
    _require_sha(history_path, history_sha, f"{role} training history")
    return {
        "source_table_rows": expected_count,
        "gradient_training_rows": split["train"],
        "validation_rows": split["validation"],
        "test_rows": split["test"],
        "history_path": history_path,
        "history_sha256": history_sha,
        "quality_status": summary.get("quality_status"),
        "overall_status": summary.get("overall_status"),
    }


def _load_targets(path: Path, expected_target_rows: int, expected_legacy_rows: int) -> Dict[str, Any]:
    payload = _read_json(path, "fixed targets")
    _require(payload.get("schema") == "direct_mlp_one_shot_targets_v1", "fixed targets schema mismatch")
    _require(payload.get("target_role") == "nonadvisor_fixed_proxy_frame", "fixed targets role mismatch")
    _require(payload.get("q_target_semantics") == "minimum", "fixed targets Q semantics mismatch")
    rows = payload.get("targets")
    _require(isinstance(rows, list) and len(rows) == expected_target_rows, "fixed target frame row count mismatch")
    _require(int(payload.get("row_count") or 0) == expected_target_rows, "fixed target row_count metadata mismatch")
    all_ids = set()
    legacy: Dict[str, Tuple[int, np.ndarray]] = {}
    for index, row in enumerate(rows):
        _require(isinstance(row, dict), f"fixed target row {index} is not an object")
        target_id = str(row.get("target_id") or "")
        _require(target_id and target_id not in all_ids, "fixed target_id is empty or duplicated")
        all_ids.add(target_id)
        vector = np.asarray([_finite_float(row[key], f"fixed target {target_id} {key}") for key in TARGET_JSON_KEYS], dtype=float)
        _require(0.0 <= vector[3] < 1.0, "fixed target |K| is outside the frame contract")
        if vector[3] <= 0.8:
            legacy[target_id] = (index, vector)
    _require(len(legacy) == expected_legacy_rows, "legacy panel does not contain the exact required row count")
    return {"payload": payload, "all_target_ids": all_ids, "legacy": legacy}


def _evaluation_artifact_by_name(bundle: Mapping[str, Any], filename: str) -> Path:
    record = bundle["evaluation_records"].get(filename)
    _require(isinstance(record, dict), f"evaluation receipt lacks {filename}")
    path = record["path"]
    _require_sha(path, record["sha256"], filename)
    return path


def _validate_evaluation_summary(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    path = _evaluation_artifact_by_name(bundle, "evaluation_summary.json")
    summary = _read_json(path, "evaluation summary")
    _require(summary.get("schema") == "architecture_matched_fixed_legacy8k_proxy_evaluation_v2", "evaluation summary schema mismatch")
    _require(summary.get("evaluation_execution_status") == "PASS", "automatic legacy8k evaluation is not PASS")
    checks = summary.get("contract_checks")
    _require(isinstance(checks, dict) and checks, "evaluation summary contract checks are absent")
    _require(all(value is True for value in checks.values()), "one or more evaluator contract checks failed")
    outputs = summary.get("outputs") or {}
    for filename in (
        "per_target_100k_predictions.csv",
        "per_target_200k_predictions.csv",
        "architecture_matched_comparison.csv",
    ):
        path = _evaluation_artifact_by_name(bundle, filename)
        record = outputs.get(filename)
        _require(isinstance(record, dict), f"evaluation summary does not bind {filename}")
        _require(record.get("sha256") == _sha256(path), f"evaluation summary hash mismatch for {filename}")
    return summary


def _required_prediction_columns() -> Tuple[str, ...]:
    columns: List[str] = [
        "legacy_row_index",
        "fixed10k_original_row_index",
        "target_id",
        "panel",
        "model_role",
        "model_id",
        "inference_mode",
        "q_one_sided_shortfall",
        "q_target_met",
        "geometry_sha256_12decimal_float64",
    ]
    for suffix in FEATURE_SUFFIXES:
        columns.extend(
            [
                f"target__{suffix}",
                f"proxy_prediction__{suffix}",
                f"signed_error__{suffix}",
                f"absolute_error__{suffix}",
            ]
        )
    columns.extend(GEOMETRY_COLUMNS)
    return tuple(columns)


def _geometry_sha256(values: np.ndarray) -> str:
    rounded = np.round(np.asarray(values, dtype=np.float64), decimals=12)
    return hashlib.sha256(rounded.tobytes()).hexdigest()


def _load_prediction_table(
    path: Path,
    role: str,
    model_id: str,
    legacy_targets: Mapping[str, Tuple[int, np.ndarray]],
    expected_rows: int,
) -> Dict[str, Any]:
    rows, fields = _read_csv(path, f"{role} prediction CSV")
    missing = [name for name in _required_prediction_columns() if name not in fields]
    _require(not missing, f"{role} prediction CSV lacks columns: {missing}")
    _require(len(rows) == expected_rows, f"{role} prediction CSV row count differs from legacy contract")
    by_id: Dict[str, Dict[str, Any]] = {}
    for row_number, raw in enumerate(rows, 1):
        target_id = str(raw.get("target_id") or "")
        _require(target_id and target_id not in by_id, f"{role} target_id is empty or duplicated")
        _require(target_id in legacy_targets, f"{role} prediction target is not in legacy panel")
        _require(raw.get("panel") == PANEL, f"{role} prediction mixes a non-legacy panel")
        _require(raw.get("model_role") == role, f"{role} prediction model role mismatch")
        _require(raw.get("model_id") == model_id, f"{role} prediction model id mismatch")
        _require(raw.get("inference_mode") == "one_shot_hard_feasible_topology_v1", f"{role} inference mode mismatch")
        expected_index, expected_target = legacy_targets[target_id]
        _require(int(raw["fixed10k_original_row_index"]) == expected_index, f"{role} fixed10k row identity mismatch")
        target = np.asarray([_finite_float(raw[f"target__{suffix}"], f"{role} target {target_id} {suffix}") for suffix in FEATURE_SUFFIXES], dtype=float)
        prediction = np.asarray([_finite_float(raw[f"proxy_prediction__{suffix}"], f"{role} prediction {target_id} {suffix}") for suffix in FEATURE_SUFFIXES], dtype=float)
        _require(np.array_equal(target, expected_target), f"{role} target values differ from fixed target frame")
        _require(target[3] <= 0.8, f"{role} high-K extension leaked into legacy panel")
        signed = prediction - target
        for index, suffix in enumerate(FEATURE_SUFFIXES):
            recorded_signed = _finite_float(raw[f"signed_error__{suffix}"], f"{role} signed error {target_id} {suffix}")
            recorded_absolute = _finite_float(raw[f"absolute_error__{suffix}"], f"{role} absolute error {target_id} {suffix}")
            _require(math.isclose(recorded_signed, float(signed[index]), rel_tol=0.0, abs_tol=1.0e-12), f"{role} signed error column is inconsistent")
            _require(math.isclose(recorded_absolute, abs(float(signed[index])), rel_tol=0.0, abs_tol=1.0e-12), f"{role} absolute error column is inconsistent")
        geometry = np.asarray([_finite_float(raw[column], f"{role} geometry {target_id} {column}") for column in GEOMETRY_COLUMNS], dtype=float)
        geometry_hash = _geometry_sha256(geometry)
        _require(raw["geometry_sha256_12decimal_float64"] == geometry_hash, f"{role} geometry digest mismatch")
        q_shortfall = max(float(target[2] - prediction[2]), 0.0)
        _require(math.isclose(_finite_float(raw["q_one_sided_shortfall"], f"{role} Q shortfall"), q_shortfall, rel_tol=0.0, abs_tol=1.0e-12), f"{role} Q shortfall column is inconsistent")
        _require(_parse_bool(raw["q_target_met"], f"{role} Q target-met") == bool(prediction[2] >= target[2]), f"{role} Q target-met column is inconsistent")
        by_id[target_id] = {
            "legacy_row_index": int(raw["legacy_row_index"]),
            "fixed10k_original_row_index": expected_index,
            "target": target,
            "prediction": prediction,
            "signed": signed,
            "absolute": np.abs(signed),
            "geometry": geometry,
            "geometry_sha256": geometry_hash,
        }
    _require(set(by_id) == set(legacy_targets), f"{role} prediction coverage is not exact")
    ordered_ids = sorted(by_id, key=lambda target_id: (by_id[target_id]["legacy_row_index"], target_id))
    legacy_indices = [by_id[target_id]["legacy_row_index"] for target_id in ordered_ids]
    _require(
        legacy_indices == list(range(expected_rows)),
        f"{role} legacy row indices are not the exact contiguous evaluation frame",
    )
    return {"by_id": by_id, "ordered_ids": ordered_ids, "source_sha256": _sha256(path), "path": path}


def _topology_violations(geometry: np.ndarray, contract: Mapping[str, Any], tolerance_um: float = 1.0e-9) -> Dict[str, Any]:
    _require(geometry.ndim == 2 and geometry.shape[1] == 10, "topology geometry matrix is malformed")
    index = {str(key): int(value) for key, value in (contract.get("index_by_semantic") or {}).items()}
    required_semantics = {
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "line_width_um",
        "primary_terminal_y_span_um",
        "secondary_terminal_y_span_um",
        "offset_um",
        "primary_feed_extension_um",
        "secondary_feed_extension_um",
    }
    _require(required_semantics.issubset(index), "topology contract lacks semantic indices")
    residuals: List[Tuple[str, np.ndarray]] = []
    chamfer_factor = math.sqrt(2.0) - 1.0
    for winding in ("primary", "secondary"):
        width = geometry[:, index[f"{winding}_outer_width_um"]]
        height = geometry[:, index[f"{winding}_outer_height_um"]]
        terminal = geometry[:, index[f"{winding}_terminal_y_span_um"]]
        residuals.extend(
            [
                (f"{winding}_terminal_within_outer_width", terminal - width),
                (f"{winding}_terminal_within_outer_height", terminal - height),
                (
                    f"{winding}_terminal_within_feed_side_straight_section",
                    terminal - (height - chamfer_factor * np.minimum(width, height)),
                ),
            ]
        )
    offset = geometry[:, index["offset_um"]]
    primary_feed = geometry[:, index["primary_feed_extension_um"]]
    secondary_feed = geometry[:, index["secondary_feed_extension_um"]]
    residuals.extend(
        [
            ("offset_within_primary_feed_support", -primary_feed - offset),
            ("offset_within_secondary_feed_support", offset - secondary_feed),
        ]
    )
    power = contract.get("power_line_port_ground_overlap") or {}
    _require(power.get("enabled") is True, "power-line topology contract is disabled")
    primary_width = geometry[:, index["primary_outer_width_um"]]
    secondary_width = geometry[:, index["secondary_outer_width_um"]]
    line_width = geometry[:, index["line_width_um"]]
    fixed_margin = (
        float(power["bar_offset_um"])
        + float(power["shield_opening_clearance_um"])
        + float(power.get("training_safety_margin_um") or 0.0)
    )
    primary_own_left = -0.5 * primary_width
    secondary_left = offset - 0.5 * secondary_width
    residuals.append(
        (
            "primary_signal_port_ground_overlap_reachable",
            primary_own_left - np.minimum(primary_own_left, secondary_left) + fixed_margin + line_width - primary_feed,
        )
    )
    secondary_own_right = offset + 0.5 * secondary_width
    primary_right = 0.5 * primary_width
    residuals.append(
        (
            "secondary_signal_port_ground_overlap_reachable",
            np.maximum(primary_right, secondary_own_right) - secondary_own_right + fixed_margin + line_width - secondary_feed,
        )
    )
    active = np.column_stack([residual > tolerance_um for _, residual in residuals])
    per_constraint = {
        name: {
            "violation_count": int(np.count_nonzero(residual > tolerance_um)),
            "maximum_positive_residual_um": float(np.max(np.maximum(residual, 0.0))),
        }
        for name, residual in residuals
    }
    return {
        "violating_rows": np.any(active, axis=1),
        "violating_row_count": int(np.count_nonzero(np.any(active, axis=1))),
        "constraint_violation_incidence_count": int(np.count_nonzero(active)),
        "constraint_count": len(residuals),
        "per_constraint": per_constraint,
    }


def _geometry_audit(
    table: Mapping[str, Any], model: Mapping[str, Any], expected_rows: int
) -> Dict[str, Any]:
    ids = table["ordered_ids"]
    geometry = np.vstack([table["by_id"][target_id]["geometry"] for target_id in ids])
    lower = np.asarray(model["physical_lower"], dtype=float)
    upper = np.asarray(model["physical_upper"], dtype=float)
    bound_rows = np.any((geometry < lower[None, :] - 1.0e-9) | (geometry > upper[None, :] + 1.0e-9), axis=1)
    topology = _topology_violations(geometry, model["topology_contract"])
    hashes = [table["by_id"][target_id]["geometry_sha256"] for target_id in ids]
    duplicate_count = len(hashes) - len(set(hashes))
    return {
        "bound_rows": bound_rows,
        "topology_rows": topology["violating_rows"],
        "geometry_bound_violation_count": int(np.count_nonzero(bound_rows)),
        "topology_violation_count": int(topology["violating_row_count"]),
        "topology_constraint_violation_incidence_count": int(topology["constraint_violation_incidence_count"]),
        "duplicate_predicted_geometry_count": int(duplicate_count),
        "unique_predicted_geometry_count": int(len(set(hashes))),
        "prediction_coverage": float(len(ids) / expected_rows) if expected_rows else 0.0,
        "topology_per_constraint": topology["per_constraint"],
    }


def _feature_metric_values(target: np.ndarray, prediction: np.ndarray) -> List[Dict[str, Any]]:
    signed = prediction - target
    absolute = np.abs(signed)
    rows: List[Dict[str, Any]] = []
    for index, feature in enumerate(FEATURE_NAMES):
        values = absolute[:, index]
        span = float(NORMALIZATION_SPANS[index])
        rows.append(
            {
                "feature": feature,
                "unit": FEATURES[feature]["unit"],
                "normalization_span": span,
                "count": int(values.size),
                "bias": float(np.mean(signed[:, index])),
                "mae": float(np.mean(values)),
                "rmse": float(np.sqrt(np.mean(signed[:, index] ** 2))),
                "median_absolute_error": float(np.percentile(values, 50.0)),
                "p90_absolute_error": float(np.percentile(values, 90.0)),
                "p95_absolute_error": float(np.percentile(values, 95.0)),
                "p99_absolute_error": float(np.percentile(values, 99.0)),
                "maximum_absolute_error": float(np.max(values)),
                "normalized_mae": float(np.mean(values / span)),
                "normalized_rmse": float(np.sqrt(np.mean((signed[:, index] / span) ** 2))),
            }
        )
    return rows


def _engineering_arrays(target: np.ndarray, prediction: np.ndarray) -> Dict[str, np.ndarray]:
    signed = prediction - target
    symmetric_absolute = np.abs(signed)
    q_shortfall = np.maximum(target[:, 2] - prediction[:, 2], 0.0)
    engineering_absolute = symmetric_absolute.copy()
    engineering_absolute[:, 2] = q_shortfall
    normalized = engineering_absolute / NORMALIZATION_SPANS[None, :]
    return {
        "signed": signed,
        "symmetric_absolute": symmetric_absolute,
        "q_shortfall": q_shortfall,
        "q_target_met": prediction[:, 2] >= target[:, 2],
        "engineering_absolute": engineering_absolute,
        "engineering_normalized": normalized,
        "row_normalized_mae": np.mean(normalized, axis=1),
        "row_normalized_rmse": np.sqrt(np.mean(normalized**2, axis=1)),
    }


def _point_metric_catalog(arrays: Mapping[str, Mapping[str, np.ndarray]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    catalog: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for role in ("100k", "200k"):
        current = arrays[role]
        signed = current["signed"]
        absolute = current["symmetric_absolute"]
        normalized_symmetric = absolute / NORMALIZATION_SPANS[None, :]
        for index, feature in enumerate(FEATURE_NAMES):
            physical = absolute[:, index]
            catalog[(role, "feature", f"{feature}:bias")] = {"value": float(np.mean(signed[:, index])), "unit": FEATURES[feature]["unit"], "direction": "zero_is_better"}
            for metric, value in (
                ("mae", np.mean(physical)),
                ("rmse", np.sqrt(np.mean(physical**2))),
                ("median_absolute_error", np.percentile(physical, 50.0)),
                ("p90_absolute_error", np.percentile(physical, 90.0)),
                ("p95_absolute_error", np.percentile(physical, 95.0)),
                ("p99_absolute_error", np.percentile(physical, 99.0)),
                ("maximum_absolute_error", np.max(physical)),
                ("normalized_mae", np.mean(normalized_symmetric[:, index])),
                ("normalized_rmse", np.sqrt(np.mean(normalized_symmetric[:, index] ** 2))),
            ):
                unit = "normalized" if metric.startswith("normalized_") else FEATURES[feature]["unit"]
                catalog[(role, "feature", f"{feature}:{metric}")] = {"value": float(value), "unit": unit, "direction": "lower_is_better"}
        q_shortfall = current["q_shortfall"]
        q_met = current["q_target_met"]
        for metric, value, direction in (
            ("target_met_fraction", np.mean(q_met), "higher_is_better"),
            ("shortfall_mae", np.mean(q_shortfall), "lower_is_better"),
            ("shortfall_rmse", np.sqrt(np.mean(q_shortfall**2)), "lower_is_better"),
            ("shortfall_p90", np.percentile(q_shortfall, 90.0), "lower_is_better"),
            ("shortfall_p95", np.percentile(q_shortfall, 95.0), "lower_is_better"),
        ):
            catalog[(role, "q", metric)] = {"value": float(value), "unit": "fraction" if metric == "target_met_fraction" else "dimensionless", "direction": direction}
        engineering = current["engineering_normalized"]
        catalog[(role, "joint", "joint_normalized_mae")] = {"value": float(np.mean(engineering)), "unit": "normalized", "direction": "lower_is_better"}
        catalog[(role, "joint", "joint_normalized_rmse")] = {"value": float(np.sqrt(np.mean(engineering**2))), "unit": "normalized", "direction": "lower_is_better"}
    return catalog


def _bootstrap_metric_batch(
    signed: np.ndarray,
    engineering: np.ndarray,
    q_shortfall: np.ndarray,
    q_met: np.ndarray,
    indices: np.ndarray,
) -> Dict[Tuple[str, str], np.ndarray]:
    sampled_signed = signed[indices]
    sampled_absolute = np.abs(sampled_signed)
    sampled_normalized_symmetric = sampled_absolute / NORMALIZATION_SPANS[None, None, :]
    result: Dict[Tuple[str, str], np.ndarray] = {}
    quantiles = np.percentile(sampled_absolute, [50.0, 90.0, 95.0, 99.0], axis=1)
    for index, feature in enumerate(FEATURE_NAMES):
        result[("feature", f"{feature}:bias")] = np.mean(sampled_signed[:, :, index], axis=1)
        result[("feature", f"{feature}:mae")] = np.mean(sampled_absolute[:, :, index], axis=1)
        result[("feature", f"{feature}:rmse")] = np.sqrt(np.mean(sampled_absolute[:, :, index] ** 2, axis=1))
        result[("feature", f"{feature}:median_absolute_error")] = quantiles[0, :, index]
        result[("feature", f"{feature}:p90_absolute_error")] = quantiles[1, :, index]
        result[("feature", f"{feature}:p95_absolute_error")] = quantiles[2, :, index]
        result[("feature", f"{feature}:p99_absolute_error")] = quantiles[3, :, index]
        result[("feature", f"{feature}:maximum_absolute_error")] = np.max(sampled_absolute[:, :, index], axis=1)
        result[("feature", f"{feature}:normalized_mae")] = np.mean(sampled_normalized_symmetric[:, :, index], axis=1)
        result[("feature", f"{feature}:normalized_rmse")] = np.sqrt(np.mean(sampled_normalized_symmetric[:, :, index] ** 2, axis=1))
    sampled_q = q_shortfall[indices]
    result[("q", "target_met_fraction")] = np.mean(q_met[indices], axis=1)
    result[("q", "shortfall_mae")] = np.mean(sampled_q, axis=1)
    result[("q", "shortfall_rmse")] = np.sqrt(np.mean(sampled_q**2, axis=1))
    q_quantiles = np.percentile(sampled_q, [90.0, 95.0], axis=1)
    result[("q", "shortfall_p90")] = q_quantiles[0]
    result[("q", "shortfall_p95")] = q_quantiles[1]
    sampled_engineering = engineering[indices]
    result[("joint", "joint_normalized_mae")] = np.mean(sampled_engineering, axis=(1, 2))
    result[("joint", "joint_normalized_rmse")] = np.sqrt(np.mean(sampled_engineering**2, axis=(1, 2)))
    return result


def paired_bootstrap_sensitivity(
    arrays: Mapping[str, Mapping[str, np.ndarray]],
    point_catalog: Mapping[Tuple[str, str, str], Mapping[str, Any]],
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
    batch_size: int = 32,
) -> List[Dict[str, Any]]:
    _require(replicates > 0, "bootstrap replicate count must be positive")
    n = int(arrays["100k"]["signed"].shape[0])
    _require(arrays["200k"]["signed"].shape[0] == n and n > 0, "bootstrap paired arrays do not align")
    rng = np.random.default_rng(seed)
    keys: Optional[List[Tuple[str, str]]] = None
    deltas: Dict[Tuple[str, str], np.ndarray] = {}
    offset = 0
    while offset < replicates:
        size = min(batch_size, replicates - offset)
        # One index matrix is deliberately shared by both model arms.  This is
        # the paired target_id resampling contract.
        indices = rng.integers(0, n, size=(size, n), endpoint=False)
        metrics_by_role: Dict[str, Dict[Tuple[str, str], np.ndarray]] = {}
        for role in ("100k", "200k"):
            current = arrays[role]
            metrics_by_role[role] = _bootstrap_metric_batch(
                current["signed"],
                current["engineering_normalized"],
                current["q_shortfall"],
                current["q_target_met"],
                indices,
            )
        if keys is None:
            keys = list(metrics_by_role["100k"])
            _require(set(keys) == set(metrics_by_role["200k"]), "bootstrap metric catalogs differ")
            deltas = {key: np.empty(replicates, dtype=float) for key in keys}
        for key in keys:
            deltas[key][offset : offset + size] = metrics_by_role["200k"][key] - metrics_by_role["100k"][key]
        offset += size

    rows: List[Dict[str, Any]] = []
    assert keys is not None
    for scope, metric_key in keys:
        reference = point_catalog[("100k", scope, metric_key)]
        candidate = point_catalog[("200k", scope, metric_key)]
        draws = deltas[(scope, metric_key)]
        direction = str(reference["direction"])
        probability_better: Optional[float]
        if direction == "higher_is_better":
            probability_better = float(np.mean(draws > 0.0))
        elif direction == "zero_is_better":
            # A delta draw alone cannot establish which arm is closer to zero;
            # do not fabricate a bootstrap probability for signed bias.
            probability_better = None
        else:
            probability_better = float(np.mean(draws < 0.0))
        if scope == "feature":
            feature, metric = metric_key.split(":", 1)
        elif scope == "q":
            feature, metric = "Qmin", metric_key
        else:
            feature, metric = "all_four", metric_key
        rows.append(
            {
                "sensitivity_label": BOOTSTRAP_LABEL,
                "scope": scope,
                "feature": feature,
                "metric": metric,
                "unit": reference["unit"],
                "direction": direction,
                "paired_target_count": n,
                "bootstrap_seed": seed,
                "bootstrap_replicates": replicates,
                "percentile_method": "numpy_linear",
                "reference_value": float(reference["value"]),
                "candidate_value": float(candidate["value"]),
                "point_delta_200k_minus_100k": float(candidate["value"] - reference["value"]),
                "bootstrap_delta_mean": float(np.mean(draws)),
                "bootstrap_delta_standard_error": float(np.std(draws, ddof=1)) if replicates > 1 else 0.0,
                "sensitivity_percentile_2_5": float(np.percentile(draws, 2.5)),
                "sensitivity_percentile_50": float(np.percentile(draws, 50.0)),
                "sensitivity_percentile_97_5": float(np.percentile(draws, 97.5)),
                "probability_candidate_better": probability_better,
                "scope_boundary": "finite fixed target-frame resampling sensitivity only",
            }
        )
    return rows


def _history_rows(role: str, display_name: str, record: Mapping[str, Any], n: int) -> List[Dict[str, Any]]:
    source_path = record["history_path"]
    rows, _fields = _read_csv(source_path, f"{role} training history")
    output: List[Dict[str, Any]] = []
    for raw in rows:
        stage = str(raw.get("stage") or "")
        if stage not in {"forward_proxy", "tandem_inverse"}:
            continue
        x_value = _finite_float(raw.get("optimizer_updates") or raw.get("epoch"), f"{role} history x")
        candidates: List[Tuple[str, str]]
        if stage == "forward_proxy":
            candidates = [
                ("train", "train_response_objective_rmse"),
                ("train", "train_feature_balanced_normalized_rmse"),
                ("train", "train_normalized_rmse"),
                ("validation", "validation_response_objective_rmse"),
                ("validation", "validation_feature_balanced_normalized_rmse"),
                ("validation", "validation_normalized_rmse"),
            ]
        else:
            candidates = [
                ("validation", "validation_response_objective_rmse"),
                ("validation", "validation_feature_balanced_response_normalized_rmse"),
                ("validation", "validation_response_normalized_rmse"),
            ]
        seen_series = set()
        for series, metric in candidates:
            if series in seen_series or raw.get(metric) in (None, ""):
                continue
            value = _finite_float(raw[metric], f"{role} history {metric}")
            output.append(
                {
                    "model_role": role,
                    "model_name": display_name,
                    "stage": stage,
                    "x_axis": "optimizer_updates" if raw.get("optimizer_updates") not in (None, "") else "epoch",
                    "x_value": x_value,
                    "series": series,
                    "metric": metric,
                    "value": value,
                    "unit": "normalized RMSE",
                    "n": n,
                    "source_history_csv_sha256": record["history_sha256"],
                }
            )
            seen_series.add(series)
    _require(any(row["stage"] == "forward_proxy" for row in output), f"{role} forward training curve is absent")
    _require(any(row["stage"] == "tandem_inverse" for row in output), f"{role} inverse validation curve is absent")
    return output


def _measure_bound_inference_runtime(
    bundle: Mapping[str, Any],
    prediction_tables: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Time one post-terminal inference pass and verify it reproduces signed CSVs."""

    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    loaded_module_names: List[str] = []
    try:
        evaluator_path = bundle["evaluator_path"]
        module_name = "_post_training_bound_evaluator_" + _sha256(evaluator_path)[:16]
        spec = importlib.util.spec_from_file_location(module_name, evaluator_path)
        _require(spec is not None and spec.loader is not None, "cannot import hash-bound evaluator")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        loaded_module_names.append(module_name)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise ContractError(f"cannot import hash-bound evaluator: {exc}") from exc
        required_symbols = ("_read_json", "_targets", "_load_model", "_load_trainer", "_infer")
        _require(all(callable(getattr(module, name, None)) for name in required_symbols), "hash-bound evaluator API is incomplete")
        target_ids, target_matrix, _indices = module._targets(module._read_json(bundle["target_path"]))
        runtime: Dict[str, float] = {}
        for role in ("100k", "200k"):
            model = module._load_model(bundle["weights_paths"][role])
            trainer_sha = _sha256(bundle["trainer_path"])
            before_modules = set(sys.modules)
            trainer = module._load_trainer(bundle["trainer_path"], trainer_sha, f"timing_{role}")
            loaded_module_names.extend(name for name in set(sys.modules) - before_modules if name.startswith("_architecture_matched_"))
            started = time.perf_counter()
            inference = module._infer(target_matrix, model, trainer, f"timing_{role}")
            elapsed = time.perf_counter() - started
            _require(math.isfinite(elapsed) and elapsed > 0.0, f"{role} inference timer is invalid")
            response = np.asarray(inference["response"], dtype=float)
            geometry = np.asarray(inference["geometry"], dtype=float)
            table = prediction_tables[role]
            expected_response = np.vstack([table["by_id"][target_id]["prediction"] for target_id in target_ids])
            expected_geometry = np.vstack([table["by_id"][target_id]["geometry"] for target_id in target_ids])
            _require(np.allclose(response, expected_response, rtol=0.0, atol=1.0e-10), f"{role} timed inference does not reproduce signed response CSV")
            _require(np.allclose(geometry, expected_geometry, rtol=0.0, atol=1.0e-10), f"{role} timed inference does not reproduce signed geometry CSV")
            runtime[role] = float(elapsed)
        return {
            "definition": "single-pass in-process model inference wall time with model already loaded; includes inverse decoder plus that model's own frozen forward proxy",
            "measurement_repetitions": 1,
            "per_model_seconds": runtime,
            "prediction_reproduction_check": True,
            "bytecode_write_disabled": True,
        }
    finally:
        for name in loaded_module_names:
            sys.modules.pop(name, None)
        sys.dont_write_bytecode = previous_dont_write_bytecode


def _sweep_rows(arrays: Mapping[str, Mapping[str, np.ndarray]], source_hashes: Mapping[str, str], n: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for tolerance in np.linspace(0.0, 0.25, 51):
        for role, display_name in (("100k", REFERENCE_DISPLAY_NAME), ("200k", CANDIDATE_DISPLAY_NAME)):
            normalized = arrays[role]["engineering_normalized"]
            success = np.all(normalized <= float(tolerance) + 1.0e-15, axis=1)
            rows.append(
                {
                    "section": "success_rate_sweep",
                    "model_role": role,
                    "model_name": display_name,
                    "metric": "joint_success_rate",
                    "value": float(np.mean(success)),
                    "unit": "fraction",
                    "definition": "all four engineering-normalized errors are at or below tolerance; Q uses one-sided shortfall",
                    "n": n,
                    "tolerance_normalized": float(tolerance),
                    "source_csv_sha256": source_hashes[role],
                    "curve_label": SWEEP_LABEL,
                }
            )
    return rows


def _metric_row(
    section: str,
    role: str,
    display_name: str,
    metric: str,
    value: float,
    unit: str,
    definition: str,
    n: int,
    source_hash: str,
    tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "section": section,
        "model_role": role,
        "model_name": display_name,
        "metric": metric,
        "value": float(value),
        "unit": unit,
        "definition": definition,
        "n": n,
        "tolerance_normalized": "" if tolerance is None else float(tolerance),
        "source_csv_sha256": source_hash,
        "curve_label": "",
    }


def _build_statistics_payload(
    bundle: Mapping[str, Any],
    expected_targets_sha256: str,
    expected_target_rows: int,
    expected_legacy_rows: int,
    bootstrap_replicates: int,
    synthetic_fixture: bool,
    synthetic_inference_seconds: Optional[Mapping[str, float]],
) -> Dict[str, Any]:
    actual_target_sha = _sha256(bundle["target_path"])
    _require(actual_target_sha == expected_targets_sha256, "fixed10k SHA does not match the active statistics contract")
    targets = _load_targets(bundle["target_path"], expected_target_rows, expected_legacy_rows)
    evaluation_summary = _validate_evaluation_summary(bundle)

    prediction_tables: Dict[str, Dict[str, Any]] = {}
    for role in ("100k", "200k"):
        path = _evaluation_artifact_by_name(bundle, f"per_target_{role}_predictions.csv")
        prediction_tables[role] = _load_prediction_table(
            path,
            role,
            bundle["model_ids"][role],
            targets["legacy"],
            expected_legacy_rows,
        )
    _require(set(prediction_tables["100k"]["by_id"]) == set(prediction_tables["200k"]["by_id"]), "model target_id sets are not one-to-one")
    paired_ids = prediction_tables["100k"]["ordered_ids"]
    _require(set(paired_ids) == set(prediction_tables["200k"]["ordered_ids"]), "paired prediction identity mismatch")
    for target_id in paired_ids:
        _require(
            prediction_tables["100k"]["by_id"][target_id]["legacy_row_index"]
            == prediction_tables["200k"]["by_id"][target_id]["legacy_row_index"],
            "paired target_id legacy row indices differ between models",
        )

    summary_payloads: Dict[str, Dict[str, Any]] = {}
    summary_records: Dict[str, Dict[str, Any]] = {}
    weight_contracts: Dict[str, Dict[str, Any]] = {}
    for role in ("100k", "200k"):
        summary_path = bundle["summary_paths"][role]
        weights_path = bundle["weights_paths"][role]
        summary_sha = _sha256(summary_path)
        weight_sha = _sha256(weights_path)
        source_records = evaluation_summary.get("sources") or {}
        _require((source_records.get(f"{role}_summary") or {}).get("sha256") == summary_sha, f"evaluation summary does not bind {role} summary")
        _require((source_records.get(f"{role}_weights") or {}).get("sha256") == weight_sha, f"evaluation summary does not bind {role} weights")
        model_record = (evaluation_summary.get("models") or {}).get(role) or {}
        _require(model_record.get("weights_sha256") == weight_sha, f"evaluation model record does not bind {role} weights")
        summary = _read_json(summary_path, f"{role} model summary")
        summary_payloads[role] = summary
        summary_records[role] = _validate_model_summary(summary, role, bundle["model_ids"][role], weight_sha)
        weight_contracts[role] = _load_weight_contract(weights_path)
        _require(tuple(model_record.get("forward_architecture") or ()) == EXPECTED_FORWARD_ARCHITECTURE, f"evaluation {role} forward architecture differs")
        _require(tuple(model_record.get("inverse_architecture") or ()) == EXPECTED_INVERSE_ARCHITECTURE, f"evaluation {role} inverse architecture differs")
        _require(model_record.get("projection_mode") == PROJECTION_MODE, f"evaluation {role} decoder differs")
        _require(int(model_record.get("parameter_count") or 0) == EXPECTED_PARAMETER_COUNT, f"evaluation {role} parameter count differs")

    target = np.vstack([prediction_tables["100k"]["by_id"][target_id]["target"] for target_id in paired_ids])
    predictions = {
        role: np.vstack([prediction_tables[role]["by_id"][target_id]["prediction"] for target_id in paired_ids])
        for role in ("100k", "200k")
    }
    for target_id in paired_ids:
        _require(
            np.array_equal(
                prediction_tables["100k"]["by_id"][target_id]["target"],
                prediction_tables["200k"]["by_id"][target_id]["target"],
            ),
            "model target values differ for a paired target_id",
        )
    _require(np.all(np.isfinite(target)) and all(np.all(np.isfinite(value)) for value in predictions.values()), "paired targets or predictions contain NaN/Inf")
    _require(np.all(target[:, 3] <= 0.8), "high-K extension leaked into paired targets")

    arrays = {role: _engineering_arrays(target, predictions[role]) for role in ("100k", "200k")}
    geometry_audits = {
        role: _geometry_audit(
            prediction_tables[role], weight_contracts[role], expected_legacy_rows
        )
        for role in ("100k", "200k")
    }

    if synthetic_fixture:
        _require(synthetic_inference_seconds is not None, "synthetic fixture inference timings are required")
        inference_runtime = {
            "definition": "synthetic fixture supplied inference wall time",
            "measurement_repetitions": 1,
            "per_model_seconds": {
                role: _finite_float(synthetic_inference_seconds[role], f"synthetic {role} inference runtime")
                for role in ("100k", "200k")
            },
            "prediction_reproduction_check": True,
        }
    else:
        inference_runtime = _measure_bound_inference_runtime(bundle, prediction_tables)

    training_runtime_seconds = _duration_seconds(
        bundle["receipts"]["LAUNCH_RECEIPT.json"].get("launched_utc"),
        bundle["receipts"]["TRAINING_RECEIPT.json"].get("completed_utc"),
        "candidate training wall runtime",
    )
    evaluation_pipeline_seconds = _duration_seconds(
        bundle["receipts"]["EVALUATION_LAUNCH_RECEIPT.json"].get("launched_utc"),
        bundle["receipts"]["EVALUATION_RECEIPT.json"].get("completed_utc"),
        "evaluation pipeline wall runtime",
    )

    feature_rows: List[Dict[str, Any]] = []
    source_hashes = {role: prediction_tables[role]["source_sha256"] for role in ("100k", "200k")}
    for role, display_name in (("100k", REFERENCE_DISPLAY_NAME), ("200k", CANDIDATE_DISPLAY_NAME)):
        for row in _feature_metric_values(target, predictions[role]):
            feature_rows.append(
                {
                    "model_role": role,
                    "model_name": display_name,
                    **row,
                    "source_prediction_csv_sha256": source_hashes[role],
                }
            )

    per_target_rows: List[Dict[str, Any]] = []
    for row_index, target_id in enumerate(paired_ids):
        base = prediction_tables["100k"]["by_id"][target_id]
        row: Dict[str, Any] = {
            "target_id": target_id,
            "legacy_row_index": row_index,
            "fixed10k_original_row_index": base["fixed10k_original_row_index"],
            "panel": PANEL,
            "reference_model_name": REFERENCE_DISPLAY_NAME,
            "candidate_model_name": CANDIDATE_DISPLAY_NAME,
        }
        for feature_index, suffix in enumerate(FEATURE_SUFFIXES):
            row[f"target__{suffix}"] = float(target[row_index, feature_index])
            for role, prefix in (("100k", "reference"), ("200k", "candidate")):
                row[f"{prefix}_prediction__{suffix}"] = float(predictions[role][row_index, feature_index])
                row[f"{prefix}_signed_error__{suffix}"] = float(arrays[role]["signed"][row_index, feature_index])
                row[f"{prefix}_absolute_error__{suffix}"] = float(arrays[role]["symmetric_absolute"][row_index, feature_index])
                row[f"{prefix}_normalized_absolute_error__{suffix}"] = float(arrays[role]["symmetric_absolute"][row_index, feature_index] / NORMALIZATION_SPANS[feature_index])
            row[f"delta_absolute_error_200k_minus_100k__{suffix}"] = float(arrays["200k"]["symmetric_absolute"][row_index, feature_index] - arrays["100k"]["symmetric_absolute"][row_index, feature_index])
            row[f"delta_normalized_absolute_error_200k_minus_100k__{suffix}"] = float(
                arrays["200k"]["symmetric_absolute"][row_index, feature_index] / NORMALIZATION_SPANS[feature_index]
                - arrays["100k"]["symmetric_absolute"][row_index, feature_index] / NORMALIZATION_SPANS[feature_index]
            )
        for role, prefix in (("100k", "reference"), ("200k", "candidate")):
            row[f"{prefix}_q_shortfall"] = float(arrays[role]["q_shortfall"][row_index])
            row[f"{prefix}_q_target_met"] = bool(arrays[role]["q_target_met"][row_index])
            row[f"{prefix}_joint_normalized_abs_error"] = float(arrays[role]["row_normalized_mae"][row_index])
            row[f"{prefix}_joint_normalized_rms_error"] = float(arrays[role]["row_normalized_rmse"][row_index])
            row[f"{prefix}_geometry_sha256"] = prediction_tables[role]["by_id"][target_id]["geometry_sha256"]
            row[f"{prefix}_geometry_bound_violation"] = bool(geometry_audits[role]["bound_rows"][row_index])
            row[f"{prefix}_topology_violation"] = bool(geometry_audits[role]["topology_rows"][row_index])
        row["delta_q_shortfall_200k_minus_100k"] = float(arrays["200k"]["q_shortfall"][row_index] - arrays["100k"]["q_shortfall"][row_index])
        row["delta_joint_normalized_abs_error_200k_minus_100k"] = float(arrays["200k"]["row_normalized_mae"][row_index] - arrays["100k"]["row_normalized_mae"][row_index])
        row["delta_joint_normalized_rms_error_200k_minus_100k"] = float(arrays["200k"]["row_normalized_rmse"][row_index] - arrays["100k"]["row_normalized_rmse"][row_index])
        per_target_rows.append(row)

    point_catalog = _point_metric_catalog(arrays)
    paired_delta_rows: List[Dict[str, Any]] = []
    for scope, metric_key in sorted({(scope, metric_key) for _role, scope, metric_key in point_catalog}):
        reference = point_catalog[("100k", scope, metric_key)]
        candidate = point_catalog[("200k", scope, metric_key)]
        if scope == "feature":
            feature, metric = metric_key.split(":", 1)
        elif scope == "q":
            feature, metric = "Qmin", metric_key
        else:
            feature, metric = "all_four", metric_key
        paired_delta_rows.append(
            {
                "metric_scope": scope,
                "feature": feature,
                "metric": metric,
                "unit": reference["unit"],
                "direction": reference["direction"],
                "reference_value": float(reference["value"]),
                "candidate_value": float(candidate["value"]),
                "delta_200k_minus_100k": float(candidate["value"] - reference["value"]),
                "paired_target_count": expected_legacy_rows,
            }
        )

    bootstrap_rows = paired_bootstrap_sensitivity(arrays, point_catalog, replicates=bootstrap_replicates)

    joint_rows: List[Dict[str, Any]] = []
    for role, display_name in (("100k", REFERENCE_DISPLAY_NAME), ("200k", CANDIDATE_DISPLAY_NAME)):
        current = arrays[role]
        source_hash = source_hashes[role]
        q_shortfall = current["q_shortfall"]
        for metric, value, unit, definition in (
            ("q_target_met_fraction", np.mean(current["q_target_met"]), "fraction", "fraction with predicted Qmin >= target Qmin"),
            ("q_shortfall_mae", np.mean(q_shortfall), "dimensionless", "mean max(target Qmin - predicted Qmin, 0)"),
            ("q_shortfall_rmse", np.sqrt(np.mean(q_shortfall**2)), "dimensionless", "root mean square of Q one-sided shortfall"),
            ("q_shortfall_p90", np.percentile(q_shortfall, 90.0), "dimensionless", "90th percentile of Q one-sided shortfall"),
            ("q_shortfall_p95", np.percentile(q_shortfall, 95.0), "dimensionless", "95th percentile of Q one-sided shortfall"),
        ):
            joint_rows.append(_metric_row("q", role, display_name, metric, float(value), unit, definition, expected_legacy_rows, source_hash))
        engineering = current["engineering_normalized"]
        joint_rows.append(_metric_row("joint", role, display_name, "joint_normalized_mae", float(np.mean(engineering)), "normalized", "mean over target-feature cells; Q uses one-sided shortfall", expected_legacy_rows, source_hash))
        joint_rows.append(_metric_row("joint", role, display_name, "joint_normalized_rmse", float(np.sqrt(np.mean(engineering**2))), "normalized", "root mean square over target-feature cells; Q uses one-sided shortfall", expected_legacy_rows, source_hash))
        audit = geometry_audits[role]
        for metric, value, unit, definition in (
            ("geometry_bound_violation_count", audit["geometry_bound_violation_count"], "rows", "predicted geometry rows outside saved model envelope"),
            ("topology_violation_count", audit["topology_violation_count"], "rows", "predicted geometry rows violating one or more saved topology constraints"),
            ("duplicate_predicted_geometry_count", audit["duplicate_predicted_geometry_count"], "rows", "predicted rows beyond the first occurrence of each 12-decimal geometry digest"),
            ("prediction_coverage", audit["prediction_coverage"], "fraction", "unique finite paired target predictions divided by the required legacy target count"),
        ):
            joint_rows.append(_metric_row("feasibility", role, display_name, metric, float(value), unit, definition, expected_legacy_rows, source_hash))
        contract = summary_records[role]
        for metric, value, definition in (
            ("source_table_rows", contract["source_table_rows"], "accepted source-table rows"),
            ("gradient_training_rows", contract["gradient_training_rows"], "rows used for gradient training"),
            ("validation_rows", contract["validation_rows"], "validation rows"),
            ("test_rows", contract["test_rows"], "sealed test rows"),
            ("parameter_count", EXPECTED_PARAMETER_COUNT, "total forward plus inverse trainable parameters"),
        ):
            joint_rows.append(_metric_row("model_contract", role, display_name, metric, float(value), "count", definition, expected_legacy_rows, source_hash))
        joint_rows.append(_metric_row("runtime", role, display_name, "inference_runtime_seconds", inference_runtime["per_model_seconds"][role], "seconds", inference_runtime["definition"], expected_legacy_rows, source_hash))
    joint_rows.append(_metric_row("runtime", "200k", CANDIDATE_DISPLAY_NAME, "training_runtime_seconds", training_runtime_seconds, "seconds", "controller launch to terminal training receipt wall time", expected_legacy_rows, source_hashes["200k"]))
    joint_rows.append(
        _metric_row(
            "runtime",
            "both",
            "two-model evaluation pipeline",
            "evaluation_pipeline_wall_runtime_seconds",
            evaluation_pipeline_seconds,
            "seconds",
            "evaluation launch receipt to terminal evaluation receipt; not pure inference runtime",
            expected_legacy_rows,
            bundle["evaluation_records"]["architecture_matched_comparison.csv"]["sha256"],
        )
    )
    joint_rows.extend(_sweep_rows(arrays, source_hashes, expected_legacy_rows))

    history_rows = _history_rows("100k", REFERENCE_DISPLAY_NAME, summary_records["100k"], expected_legacy_rows) + _history_rows("200k", CANDIDATE_DISPLAY_NAME, summary_records["200k"], expected_legacy_rows)

    geometry_summary = {
        "schema": "architecture_matched_fixed8k_geometry_feasibility_v1",
        "comparison_names": [REFERENCE_DISPLAY_NAME, CANDIDATE_DISPLAY_NAME],
        "panel": PANEL,
        "n": expected_legacy_rows,
        "evidence_label": EVIDENCE_LABEL,
        "definitions": {
            "geometry_bound_violation_count": "row count outside that model's saved physical geometry envelope at 1e-9 um tolerance",
            "topology_violation_count": "row count violating at least one saved topology residual at 1e-9 um tolerance",
            "duplicate_predicted_geometry_count": "row count beyond first occurrence of each 12-decimal float64 geometry digest",
            "prediction_coverage": "unique finite paired target predictions / required legacy target count",
        },
        "models": {
            role: {
                key: value
                for key, value in geometry_audits[role].items()
                if key not in {"bound_rows", "topology_rows"}
            }
            for role in ("100k", "200k")
        },
    }
    runtime_summary = {
        "schema": "architecture_matched_fixed8k_runtime_summary_v1",
        "panel": PANEL,
        "n": expected_legacy_rows,
        "training_runtime": {
            "model_role": "200k",
            "seconds": training_runtime_seconds,
            "definition": "controller launch receipt to terminal training receipt wall time",
        },
        "inference_runtime": inference_runtime,
        "evaluation_pipeline_wall_runtime": {
            "seconds": evaluation_pipeline_seconds,
            "definition": "evaluation launch receipt to terminal evaluation receipt; not substituted for pure inference runtime",
        },
    }

    tolerance_evidence_path = PROJECT_ROOT / "docs/research/REFERENCE_100K_SELECTION_UNPROVEN.json"
    tolerance_evidence: Dict[str, Any] = {
        "status": "UNRESOLVED_NOT_LOCALLY_BOUND",
        "numeric_tolerance_released": False,
        "reason": "the previous advisor deck and its evaluation-result contents are not present in this clone; current trainer checkpoint-selection defaults are not presentation tolerance evidence",
    }
    if tolerance_evidence_path.is_file():
        tolerance_evidence["negative_evidence_source"] = _safe_source(tolerance_evidence_path)

    evaluation_contract = {
        "schema": "architecture_matched_fixed8k_statistics_contract_v1",
        "comparison": {
            "reference_name": REFERENCE_DISPLAY_NAME,
            "candidate_name": CANDIDATE_DISPLAY_NAME,
            "reference_model_id": EXPECTED_REFERENCE_MODEL_ID,
            "candidate_model_id": bundle["model_ids"]["200k"],
            "evidence_label": EVIDENCE_LABEL,
            "comparison_claim_class": "descriptive paired proxy comparison",
        },
        "panel": {
            "name": PANEL,
            "selection": "K_abs <= 0.8",
            "n": expected_legacy_rows,
            "high_k_extension_included": False,
        },
        "normalization_spans": {
            feature: {"value": float(FEATURES[feature]["span"]), "unit": FEATURES[feature]["unit"]}
            for feature in FEATURE_NAMES
        },
        "normalization_decision": {
            "K_abs_span": 0.8,
            "authority": "project owner current-turn override",
            "scope": "all normalized feature, joint, paired-delta, bootstrap, and tolerance-sweep statistics",
        },
        "q_shortfall_definition": "max(target_Qmin - predicted_Qmin, 0)",
        "joint_metric_definition": "engineering-normalized target-feature cells; Q uses one-sided shortfall and other features use absolute residual",
        "percentile_method": "numpy linear",
        "bootstrap": {
            "label": BOOTSTRAP_LABEL,
            "seed": BOOTSTRAP_SEED,
            "replicates": bootstrap_replicates,
            "resampling_unit": "target_id",
            "paired_index_reused_for_both_models": True,
            "scope_boundary": "finite fixed target-frame resampling sensitivity only",
        },
        "previous_presentation_tolerance_contract": tolerance_evidence,
        "tolerance_sweep": {
            "label": SWEEP_LABEL,
            "grid": {"start": 0.0, "stop": 0.25, "point_count": 51},
            "success_definition": "all four engineering-normalized errors <= tolerance; Q uses one-sided shortfall",
            "privileged_threshold": None,
        },
        "synthetic_fixture": synthetic_fixture,
    }

    model_comparison = {
        "schema": "architecture_matched_fixed8k_model_contract_comparison_v1",
        "reference": {
            "display_name": REFERENCE_DISPLAY_NAME,
            "model_id": bundle["model_ids"]["100k"],
            "seed": 20260713,
            "role": "previous advisor presentation reference",
            "forward_architecture": list(EXPECTED_FORWARD_ARCHITECTURE),
            "inverse_architecture": list(EXPECTED_INVERSE_ARCHITECTURE),
            "decoder": PROJECTION_MODE,
            "total_parameters": EXPECTED_PARAMETER_COUNT,
            **{key: value for key, value in summary_records["100k"].items() if key not in {"history_path", "history_sha256"}},
            "summary_sha256": _sha256(bundle["summary_paths"]["100k"]),
            "weights_sha256": _sha256(bundle["weights_paths"]["100k"]),
            "history_sha256": summary_records["100k"]["history_sha256"],
        },
        "candidate": {
            "display_name": CANDIDATE_DISPLAY_NAME,
            "model_id": bundle["model_ids"]["200k"],
            "role": "architecture-matched candidate trained on 200k source-table rows",
            "forward_architecture": list(EXPECTED_FORWARD_ARCHITECTURE),
            "inverse_architecture": list(EXPECTED_INVERSE_ARCHITECTURE),
            "decoder": PROJECTION_MODE,
            "total_parameters": EXPECTED_PARAMETER_COUNT,
            **{key: value for key, value in summary_records["200k"].items() if key not in {"history_path", "history_sha256"}},
            "summary_sha256": _sha256(bundle["summary_paths"]["200k"]),
            "weights_sha256": _sha256(bundle["weights_paths"]["200k"]),
            "history_sha256": summary_records["200k"]["history_sha256"],
        },
        "contract_checks": {
            "forward_architecture_exact_and_equal": True,
            "inverse_architecture_exact_and_equal": True,
            "decoder_exact_and_equal": True,
            "parameter_count_exact_and_equal": True,
            "prediction_summary_weights_binding_complete": True,
        },
    }

    input_sources: Dict[str, Any] = {
        "fixed_targets": _safe_source(bundle["target_path"]),
        "reference_contract": _safe_source(bundle["reference_contract_path"]),
        "reference_summary": _safe_source(bundle["summary_paths"]["100k"]),
        "candidate_summary": _safe_source(bundle["summary_paths"]["200k"]),
        "reference_weights": _safe_source(bundle["weights_paths"]["100k"]),
        "candidate_weights": _safe_source(bundle["weights_paths"]["200k"]),
        "trainer_source": _safe_source(bundle["trainer_path"]),
        "trainer_helper_source": _safe_source(bundle["trainer_helper_path"]),
        "evaluator_source": _safe_source(bundle["evaluator_path"]),
        "evaluation_summary": _safe_source(_evaluation_artifact_by_name(bundle, "evaluation_summary.json")),
        "evaluation_sha256s": bundle["evaluation_sha256s_source"],
        "reference_predictions": _safe_source(prediction_tables["100k"]["path"]),
        "candidate_predictions": _safe_source(prediction_tables["200k"]["path"]),
        "realized_evaluation_argv": bundle["realized_argv_source"],
        "realized_evaluation_command": bundle["realized_command_source"],
        "finite_observer_receipt": bundle["finite_observer_source"],
        "controller_receipts": bundle["receipt_sources"],
    }
    gates = {
        "training_terminal_pass": True,
        "automatic_legacy8k_evaluation_pass": True,
        "fixed10k_sha_exact": True,
        "legacy_target_count_exact": True,
        "all_legacy_rows_k_abs_le_0p8": True,
        "target_id_unique": True,
        "model_target_id_one_to_one": True,
        "all_numeric_values_finite": True,
        "model_architecture_and_decoder_exact": True,
        "prediction_files_bound_to_weights_and_summaries": True,
        "training_and_evaluation_receipts_complete": True,
    }
    identity_audit = {
        "schema": "architecture_matched_fixed8k_input_identity_audit_v1",
        "release_gate_status": "PASS",
        "run_id": bundle["expected_run_id"],
        "trainer_pid_receipt_identity": bundle["expected_trainer_pid"],
        "panel": PANEL,
        "legacy_target_count": expected_legacy_rows,
        "sources": input_sources,
        "gates": gates,
        "evaluator_legacy_advisor_eligibility_field_preserved_in_source_only": evaluation_summary.get("advisor_comparison_eligible"),
        "display_name_override": {
            "reference": REFERENCE_DISPLAY_NAME,
            "candidate": CANDIDATE_DISPLAY_NAME,
        },
        "synthetic_fixture": synthetic_fixture,
    }

    advisor_notes = f"""# Architecture-matched fixed8k comparison notes

## Technical summary

- Comparison: **{REFERENCE_DISPLAY_NAME}** vs. **{CANDIDATE_DISPLAY_NAME}**.
- Scope: `{PANEL}`, n={expected_legacy_rows}, {EVIDENCE_LABEL}; high-K extension excluded.
- This is a descriptive paired proxy comparison. It is not fresh EMX or measured physical evidence.
- Q shortfall is `max(target_Qmin - predicted_Qmin, 0)`.
- Normalization spans are Lp=2.5 nH, Ls=2.5 nH, Qmin=20, and |K|=0.8, following the project owner's current override.

## Uncertainty and threshold handling

- `{BOOTSTRAP_LABEL}` uses seed {BOOTSTRAP_SEED} and {bootstrap_replicates} paired target-id resamples.
- The prior advisor-presentation tolerance contract is `UNRESOLVED_NOT_LOCALLY_BOUND`; no official point threshold is released.
- The 0.00–0.25 normalized success-rate sweep is a `{SWEEP_LABEL}` only.

## Release boundaries

- All terminal training/evaluation receipts and artifact hashes closed before statistics were built.
- All targets and predictions are finite, paired one-to-one, and restricted to |K|<=0.8.
- Geometry envelope, topology, duplicate-geometry, coverage, training-runtime, and post-terminal inference-runtime checks are included.
"""

    return {
        "evaluation_contract": evaluation_contract,
        "identity_audit": identity_audit,
        "model_comparison": model_comparison,
        "per_target_rows": per_target_rows,
        "feature_rows": feature_rows,
        "joint_rows": joint_rows,
        "paired_delta_rows": paired_delta_rows,
        "bootstrap_rows": bootstrap_rows,
        "history_rows": history_rows,
        "geometry_summary": geometry_summary,
        "runtime_summary": runtime_summary,
        "advisor_notes": advisor_notes,
        "n": expected_legacy_rows,
        "synthetic_fixture": synthetic_fixture,
    }


def build_statistics(
    controller_run_dir: Path,
    out_dir: Path,
    expected_run_id: str = EXPECTED_RUN_ID,
    expected_trainer_pid: int = EXPECTED_TRAINER_PID,
    bootstrap_replicates: int = BOOTSTRAP_REPLICATES,
    synthetic_fixture: bool = False,
    synthetic_expected_targets_sha256: Optional[str] = None,
    synthetic_expected_target_rows: int = EXPECTED_TARGET_FRAME_ROWS,
    synthetic_expected_legacy_rows: int = EXPECTED_LEGACY_ROWS,
    synthetic_inference_seconds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    run_source = controller_run_dir.expanduser().resolve()
    destination = out_dir.expanduser().resolve()
    _require(not destination.exists(), f"no-clobber statistics output already exists: {destination}")
    if synthetic_fixture:
        _require(not _is_within(destination, FORMAL_REPORT_ROOT), "synthetic fixture output is forbidden in the formal reports directory")
        _require(
            _is_within(destination, Path(tempfile.gettempdir()).resolve()),
            "synthetic fixture output must be inside the platform temporary directory",
        )
        _require(
            _is_within(run_source, Path(tempfile.gettempdir()).resolve()),
            "synthetic controller fixture must be inside the platform temporary directory",
        )
        _require(synthetic_expected_targets_sha256 is not None, "synthetic target SHA is required")
        expected_targets_sha = str(synthetic_expected_targets_sha256)
        expected_target_rows = int(synthetic_expected_target_rows)
        expected_legacy_rows = int(synthetic_expected_legacy_rows)
    else:
        _require(expected_run_id == EXPECTED_RUN_ID, "formal run_id is fixed to the authorized controller run")
        _require(expected_trainer_pid == EXPECTED_TRAINER_PID, "formal trainer pid receipt identity is fixed")
        _require(bootstrap_replicates == BOOTSTRAP_REPLICATES, "formal bootstrap replicate count is fixed at 10000")
        _require(
            destination.parent == FORMAL_REPORT_ROOT
            and destination.name.startswith(FORMAL_STAGING_PREFIX)
            and ".staging-" in destination.name,
            "formal statistics output is restricted to the runner-owned hidden reports staging directory",
        )
        expected_targets_sha = FROZEN_FIXED10K_SHA256
        expected_target_rows = EXPECTED_TARGET_FRAME_ROWS
        expected_legacy_rows = EXPECTED_LEGACY_ROWS

    bundle = discover_controller_bundle(run_source, expected_run_id, expected_trainer_pid)
    payload = _build_statistics_payload(
        bundle,
        expected_targets_sha,
        expected_target_rows,
        expected_legacy_rows,
        bootstrap_replicates,
        synthetic_fixture,
        synthetic_inference_seconds,
    )

    created = False
    try:
        destination.mkdir(parents=True, exist_ok=False)
        created = True
        files: Dict[str, Dict[str, Any]] = {}

        json_outputs = (
            ("EVALUATION_CONTRACT.json", payload["evaluation_contract"]),
            ("INPUT_IDENTITY_AUDIT.json", payload["identity_audit"]),
            ("MODEL_CONTRACT_COMPARISON.json", payload["model_comparison"]),
            ("geometry_feasibility_summary.json", payload["geometry_summary"]),
            ("training_runtime_summary.json", payload["runtime_summary"]),
        )
        for filename, value in json_outputs:
            path = destination / filename
            _write_json_exclusive(path, value)
            files[filename] = {"sha256": _sha256(path), "size_bytes": path.stat().st_size}

        csv_outputs = (
            ("per_target_paired_errors.csv", payload["per_target_rows"]),
            ("feature_metrics_long.csv", payload["feature_rows"]),
            ("joint_metrics.csv", payload["joint_rows"]),
            ("paired_delta_summary.csv", payload["paired_delta_rows"]),
            ("paired_bootstrap_sensitivity.csv", payload["bootstrap_rows"]),
            ("training_curves_long.csv", payload["history_rows"]),
        )
        for filename, rows in csv_outputs:
            path = destination / filename
            row_count = _write_csv_exclusive(path, rows)
            files[filename] = {"sha256": _sha256(path), "size_bytes": path.stat().st_size, "row_count": row_count}

        notes_path = destination / "ADVISOR_REPORT_NOTES.md"
        _write_text_exclusive(notes_path, payload["advisor_notes"])
        files[notes_path.name] = {"sha256": _sha256(notes_path), "size_bytes": notes_path.stat().st_size}

        report_summary = {
            "schema": "architecture_matched_fixed8k_report_summary_v1",
            "report_status": "STATISTICS_PASS_FIGURES_PENDING",
            "comparison": {
                "reference_name": REFERENCE_DISPLAY_NAME,
                "candidate_name": CANDIDATE_DISPLAY_NAME,
                "n": payload["n"],
                "panel": PANEL,
                "evidence_label": EVIDENCE_LABEL,
            },
            "normalization_spans": {feature: float(FEATURES[feature]["span"]) for feature in FEATURE_NAMES},
            "bootstrap_label": BOOTSTRAP_LABEL,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_replicates": bootstrap_replicates,
            "tolerance_sweep_label": SWEEP_LABEL,
            "prior_presentation_tolerance_status": "UNRESOLVED_NOT_LOCALLY_BOUND",
            "release_gates": payload["identity_audit"]["gates"],
            "outputs": files,
            "synthetic_fixture": payload["synthetic_fixture"],
        }
        report_summary["canonical_payload_sha256_without_self"] = _canonical_sha256(report_summary)
        report_summary_path = destination / "REPORT_SUMMARY.json"
        _write_json_exclusive(report_summary_path, report_summary)
        return {
            "out_dir": destination,
            "report_summary_path": report_summary_path,
            "report_summary_sha256": _sha256(report_summary_path),
            "statistics_outputs": {**files, "REPORT_SUMMARY.json": {"sha256": _sha256(report_summary_path), "size_bytes": report_summary_path.stat().st_size}},
            "n": payload["n"],
        }
    except Exception:
        if created and destination.exists():
            shutil.rmtree(destination)
        raise


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-run-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-run-id", default=EXPECTED_RUN_ID)
    parser.add_argument("--expected-trainer-pid", type=int, default=EXPECTED_TRAINER_PID)
    parser.add_argument("--bootstrap-replicates", type=int, default=BOOTSTRAP_REPLICATES)
    parser.add_argument("--synthetic-fixture", action="store_true")
    parser.add_argument("--synthetic-expected-targets-sha256", default="")
    parser.add_argument("--synthetic-expected-target-rows", type=int, default=EXPECTED_TARGET_FRAME_ROWS)
    parser.add_argument("--synthetic-expected-legacy-rows", type=int, default=EXPECTED_LEGACY_ROWS)
    parser.add_argument("--synthetic-inference-runtime-100k-seconds", type=float, default=0.0)
    parser.add_argument("--synthetic-inference-runtime-200k-seconds", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    synthetic_timings: Optional[Dict[str, float]] = None
    if args.synthetic_fixture:
        synthetic_timings = {
            "100k": args.synthetic_inference_runtime_100k_seconds,
            "200k": args.synthetic_inference_runtime_200k_seconds,
        }
    result = build_statistics(
        Path(args.controller_run_dir),
        Path(args.out_dir),
        expected_run_id=args.expected_run_id,
        expected_trainer_pid=args.expected_trainer_pid,
        bootstrap_replicates=args.bootstrap_replicates,
        synthetic_fixture=args.synthetic_fixture,
        synthetic_expected_targets_sha256=args.synthetic_expected_targets_sha256 or None,
        synthetic_expected_target_rows=args.synthetic_expected_target_rows,
        synthetic_expected_legacy_rows=args.synthetic_expected_legacy_rows,
        synthetic_inference_seconds=synthetic_timings,
    )
    print(str(result["out_dir"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
