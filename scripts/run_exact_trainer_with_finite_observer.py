#!/usr/bin/env python3
"""Run a hash-bound trainer with a numerically inert finite-update observer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _all_finite(arrays: list[np.ndarray]) -> bool:
    return all(bool(np.all(np.isfinite(np.asarray(item)))) for item in arrays)


def _parameter_count(weights: list[np.ndarray], biases: list[np.ndarray]) -> int:
    return int(sum(item.size for item in [*weights, *biases]))


def _loaded_blas_libraries() -> list[dict[str, Any]]:
    paths: set[Path] = set()
    process_maps = Path("/proc/self/maps")
    if process_maps.is_file():
        for line in process_maps.read_text(encoding="utf-8", errors="replace").splitlines():
            candidate = line.split()[-1] if line.split() else ""
            if "openblas" in candidate.lower() and candidate.startswith("/"):
                path = Path(candidate.removesuffix(" (deleted)")).resolve()
                if path.is_file():
                    paths.add(path)
    return [
        {
            "file_name": path.name,
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(paths)
    ]


def _runtime_identity() -> dict[str, Any]:
    from numpy._core import _multiarray_umath

    python_executable = Path(sys.executable).resolve()
    numpy_core = Path(_multiarray_umath.__file__).resolve()
    return {
        "python_version": sys.version,
        "python_executable": str(python_executable),
        "python_executable_sha256": _sha256(python_executable),
        "numpy_version": np.__version__,
        "numpy_core": str(numpy_core),
        "numpy_core_sha256": _sha256(numpy_core),
        "loaded_blas_libraries": _loaded_blas_libraries(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "thread_environment": {
            key: os.environ.get(key, "") for key in THREAD_ENV_KEYS
        },
    }


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--expected-trainer-sha256", required=True)
    parser.add_argument("--expected-python-sha256", required=True)
    parser.add_argument("--expected-numpy-version", required=True)
    parser.add_argument("--expected-numpy-core-sha256", required=True)
    parser.add_argument("--expected-blas-sha256", required=True)
    parser.add_argument("--expected-thread-limit", type=int, required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--observe-steps", type=int, default=5)
    parsed, trainer_argv = parser.parse_known_args(argv)
    if trainer_argv and trainer_argv[0] == "--":
        trainer_argv = trainer_argv[1:]
    if parsed.observe_steps < 1:
        parser.error("--observe-steps must be positive")
    if not trainer_argv:
        parser.error("trainer arguments are required after --")
    return parsed, trainer_argv


def main(argv: list[str] | None = None) -> int:
    args, trainer_argv = _parse_args(argv)
    trainer_source = Path(args.trainer_source).expanduser().resolve()
    receipt_path = Path(args.receipt).expanduser().resolve()
    expected_sha = str(args.expected_trainer_sha256).strip().lower()
    actual_sha = _sha256(trainer_source)
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"trainer source SHA-256 mismatch: expected={expected_sha} actual={actual_sha}"
        )

    runtime_identity = _runtime_identity()
    expected_blas_sha256 = str(args.expected_blas_sha256).strip().lower()
    if (
        len(expected_blas_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_blas_sha256)
    ):
        raise RuntimeError("expected BLAS SHA-256 is malformed")
    loaded_blas_sha256 = {
        str(record["sha256"])
        for record in runtime_identity["loaded_blas_libraries"]
    }
    runtime_checks = {
        "python_executable_sha256": (
            runtime_identity["python_executable_sha256"]
            == str(args.expected_python_sha256).strip().lower()
        ),
        "numpy_version": (
            runtime_identity["numpy_version"] == str(args.expected_numpy_version)
        ),
        "numpy_core_sha256": (
            runtime_identity["numpy_core_sha256"]
            == str(args.expected_numpy_core_sha256).strip().lower()
        ),
        "blas_library_sha256_exact_set": loaded_blas_sha256
        == {expected_blas_sha256},
        "thread_environment": all(
            value == str(args.expected_thread_limit)
            for value in runtime_identity["thread_environment"].values()
        ),
    }
    if not all(runtime_checks.values()):
        raise RuntimeError(f"runtime identity mismatch: {runtime_checks}")

    sys.path.insert(0, str(trainer_source.parents[1]))
    spec = importlib.util.spec_from_file_location(
        "_hash_bound_exact_trainer_runtime",
        trainer_source,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load exact trainer source")
    trainer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trainer)
    original_adam_step: Callable[..., None] = trainer._adam_step

    observed_states: list[tuple[dict[str, Any], str]] = []
    stage_order = ("forward_proxy", "tandem_inverse")
    records: dict[str, dict[str, Any]] = {}
    payload: dict[str, Any] = {
        "schema": "exact_trainer_finite_update_observer_v1",
        "generated_utc": _utc_now(),
        "status": "WAITING_FOR_FIRST_OPTIMIZER_UPDATE",
        "scientific_boundary": (
            "The observer delegates every numeric update to the hash-bound original "
            "trainer function, then performs read-only finiteness checks. It does not "
            "alter gradients, parameters, optimizer state, data order, or checkpoints."
        ),
        "trainer_source": str(trainer_source),
        "trainer_source_sha256": actual_sha,
        "runtime_identity": runtime_identity,
        "runtime_checks": runtime_checks,
        "observe_steps_per_stage": int(args.observe_steps),
        "stages": records,
    }
    _write_json_atomic(receipt_path, payload)

    def observed_adam_step(
        weights: list[np.ndarray],
        biases: list[np.ndarray],
        grad_weights: list[np.ndarray],
        grad_biases: list[np.ndarray],
        state: dict[str, Any],
        learning_rate: float,
    ) -> None:
        stage_name = next(
            (name for observed_state, name in observed_states if observed_state is state),
            "",
        )
        if not stage_name:
            stage_index = len(observed_states)
            stage_name = (
                stage_order[stage_index]
                if stage_index < len(stage_order)
                else f"unexpected_stage_{stage_index + 1}"
            )
            observed_states.append((state, stage_name))
            records[stage_name] = {
                "status": "RUNNING",
                "observed_steps": 0,
                "parameter_count": _parameter_count(weights, biases),
                "weight_shapes": [list(item.shape) for item in weights],
                "bias_shapes": [list(item.shape) for item in biases],
            }
        record = records[stage_name]
        next_step = int(state.get("step", 0)) + 1
        within_window = next_step <= int(args.observe_steps)
        if within_window and not (
            _all_finite(weights)
            and _all_finite(biases)
            and _all_finite(grad_weights)
            and _all_finite(grad_biases)
        ):
            record.update({"status": "FAIL", "failed_step": next_step})
            payload.update({"generated_utc": _utc_now(), "status": "FAIL"})
            _write_json_atomic(receipt_path, payload)
            raise FloatingPointError(
                f"non-finite parameter or gradient before {stage_name} Adam step {next_step}"
            )

        original_adam_step(
            weights,
            biases,
            grad_weights,
            grad_biases,
            state,
            learning_rate,
        )

        if within_window:
            optimizer_arrays = [
                *state["mw"],
                *state["vw"],
                *state["mb"],
                *state["vb"],
            ]
            finite = (
                int(state.get("step", 0)) == next_step
                and _all_finite(weights)
                and _all_finite(biases)
                and _all_finite(optimizer_arrays)
            )
            record.update(
                {
                    "observed_steps": next_step,
                    "last_observed_utc": _utc_now(),
                    "parameters_finite": finite,
                    "gradients_finite": True,
                    "optimizer_state_finite": finite,
                }
            )
            if not finite:
                record.update({"status": "FAIL", "failed_step": next_step})
                payload.update({"generated_utc": _utc_now(), "status": "FAIL"})
                _write_json_atomic(receipt_path, payload)
                raise FloatingPointError(
                    f"non-finite state after {stage_name} Adam step {next_step}"
                )
            if next_step == int(args.observe_steps):
                record["status"] = "PASS"
            forward_pass = records.get("forward_proxy", {}).get("status") == "PASS"
            inverse_pass = records.get("tandem_inverse", {}).get("status") == "PASS"
            payload["status"] = (
                "PASS"
                if forward_pass and inverse_pass
                else "FORWARD_FIRST_UPDATES_PASS_INVERSE_NOT_STARTED"
                if forward_pass
                else "RUNNING"
            )
            payload["generated_utc"] = _utc_now()
            _write_json_atomic(receipt_path, payload)
            print(
                f"finite_update_observer stage={stage_name} step={next_step} "
                "all_finite=true",
                flush=True,
            )

    trainer._adam_step = observed_adam_step
    trainer_exit_code = int(trainer.main(trainer_argv))
    observer_exit_code = trainer_exit_code
    payload["generated_utc"] = _utc_now()
    payload["trainer_exit_code"] = trainer_exit_code
    forward_pass = records.get("forward_proxy", {}).get("status") == "PASS"
    inverse_pass = records.get("tandem_inverse", {}).get("status") == "PASS"
    if trainer_exit_code == 0 and not (forward_pass and inverse_pass):
        payload["status"] = "FAIL_INCOMPLETE_FINITE_UPDATE_OBSERVATION"
        observer_exit_code = 3
    elif trainer_exit_code != 0 and payload.get("status") != "FAIL":
        payload["status"] = "TRAINER_FAILED"
    payload["observer_exit_code"] = observer_exit_code
    _write_json_atomic(receipt_path, payload)
    return observer_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
