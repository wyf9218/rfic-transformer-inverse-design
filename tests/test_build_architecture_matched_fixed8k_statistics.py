from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "build_architecture_matched_fixed8k_statistics.py"
SPEC = importlib.util.spec_from_file_location(
    "build_architecture_matched_fixed8k_statistics_test_module", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SYNTHETIC_TARGET_ROWS = 10
SYNTHETIC_LEGACY_ROWS = 8
SYNTHETIC_BOOTSTRAP_REPLICATES = 64
SYNTHETIC_CANDIDATE_ID = "synthetic_architecture_matched_200k"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fieldnames(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    result: List[str] = []
    for row in rows:
        for key in row:
            if key not in result:
                result.append(key)
    return result


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_fieldnames(rows))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _artifact_record(path: Path, relative_path: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }
    if relative_path:
        result["relative_path"] = relative_path
    return result


def _topology_contract() -> Dict[str, Any]:
    semantics = [name.removeprefix("geom__") for name in MODULE.GEOMETRY_COLUMNS]
    return {
        "available": True,
        "index_by_semantic": {name: index for index, name in enumerate(semantics)},
        "power_line_port_ground_overlap": {
            "enabled": True,
            "bar_offset_um": 12.0,
            "shield_opening_clearance_um": 10.0,
            "expected_overlap_um": 10.0,
            "training_safety_margin_um": 0.0,
        },
    }


def _write_exact_architecture_weights(path: Path) -> None:
    arrays: Dict[str, np.ndarray] = {}
    forward_widths = MODULE.EXPECTED_FORWARD_ARCHITECTURE
    inverse_widths = MODULE.EXPECTED_INVERSE_ARCHITECTURE
    for prefix, widths in (("forward", forward_widths), ("inverse", inverse_widths)):
        for index, (width_in, width_out) in enumerate(zip(widths[:-1], widths[1:])):
            arrays[f"{prefix}_weight_{index}"] = np.zeros(
                (width_in, width_out), dtype=np.float32
            )
            arrays[f"{prefix}_bias_{index}"] = np.zeros(width_out, dtype=np.float32)
    arrays.update(
        {
            "normalization__x_mean": np.zeros(4, dtype=np.float64),
            "normalization__x_scale": np.ones(4, dtype=np.float64),
            "normalization__y_mean": np.full(10, 50.0, dtype=np.float64),
            "normalization__y_scale": np.full(10, 50.0, dtype=np.float64),
            "normalization__geometry_lower": np.full(10, -1.0, dtype=np.float64),
            "normalization__geometry_upper": np.full(10, 1.0, dtype=np.float64),
            "inverse_geometry_projection__mode": np.asarray(
                [MODULE.PROJECTION_MODE]
            ),
            "inverse_geometry_projection__topology_contract_json": np.asarray(
                [json.dumps(_topology_contract(), sort_keys=True)]
            ),
        }
    )
    np.savez_compressed(path, **arrays)


def _write_history(path: Path, scale: float) -> None:
    _write_csv(
        path,
        [
            {
                "stage": "forward_proxy",
                "optimizer_updates": 10,
                "train_response_objective_rmse": 0.20 * scale,
                "validation_response_objective_rmse": 0.25 * scale,
            },
            {
                "stage": "tandem_inverse",
                "optimizer_updates": 20,
                "train_response_objective_rmse": "",
                "validation_response_objective_rmse": 0.15 * scale,
            },
        ],
    )


def _model_summary(
    role: str,
    model_id: str,
    weights_path: Path,
    history_path: Path,
) -> Dict[str, Any]:
    training_count = 100_000 if role == "100k" else 200_000
    split = (
        {"train": 80_000, "validation": 10_000, "test": 10_000}
        if role == "100k"
        else {"train": 160_000, "validation": 20_000, "test": 20_000}
    )
    return {
        "execution_status": "PASS",
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "quality_status": "REVIEW_REQUIRED",
        "model_id": model_id,
        "training_count": training_count,
        "weights_npz_sha256": _sha256(weights_path),
        "history_csv": str(history_path.resolve()),
        "history_csv_sha256": _sha256(history_path),
        "arguments": {
            "forward_hidden_widths": "256,256,128",
            "inverse_hidden_widths": "512,512,256",
            "inverse_geometry_projection": MODULE.PROJECTION_MODE,
            "q_target_semantics": "minimum",
        },
        "method": {
            "geometry_output_constraint": MODULE.PROJECTION_MODE,
            "geometry_output_constraint_is_single_pass": True,
            "geometry_output_constraint_is_posthoc_repair": False,
        },
        "split_audit": {"row_counts": split},
    }


def _targets_payload() -> Dict[str, Any]:
    targets: List[Dict[str, Any]] = []
    for index in range(SYNTHETIC_TARGET_ROWS):
        k_abs = 0.1 * index if index < SYNTHETIC_LEGACY_ROWS else 0.85 + 0.05 * (
            index - SYNTHETIC_LEGACY_ROWS
        )
        targets.append(
            {
                "target_id": f"target_{index:04d}",
                "Lp_nH": 1.0 + 0.1 * index,
                "Ls_nH": 1.5 + 0.05 * index,
                "Q_min": 10.0 + index,
                "K_abs": k_abs,
            }
        )
    return {
        "schema": "direct_mlp_one_shot_targets_v1",
        "target_role": "nonadvisor_fixed_proxy_frame",
        "q_target_semantics": "minimum",
        "row_count": SYNTHETIC_TARGET_ROWS,
        "targets": targets,
    }


def _prediction_rows(
    targets: Mapping[str, Any],
    role: str,
    model_id: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    q_residuals = (
        (-2.0, 1.0, 0.0, 4.0) if role == "100k" else (-1.0, 2.0, 0.0, 5.0)
    )
    residual_template = (
        (0.5, -0.5, 0.0, 0.08)
        if role == "100k"
        else (0.25, -0.25, 0.0, 0.04)
    )
    for legacy_index, target_row in enumerate(targets["targets"][:SYNTHETIC_LEGACY_ROWS]):
        target = np.asarray(
            [target_row[key] for key in MODULE.TARGET_JSON_KEYS], dtype=float
        )
        residual = np.asarray(residual_template, dtype=float)
        residual[2] = q_residuals[legacy_index % len(q_residuals)]
        prediction = target + residual
        geometry = np.asarray(
            [
                80.0,
                80.0,
                80.0,
                80.0,
                2.0,
                20.0,
                20.0,
                0.0,
                40.0 + 0.01 * legacy_index,
                40.0 + 0.01 * legacy_index,
            ],
            dtype=float,
        )
        row: Dict[str, Any] = {
            "legacy_row_index": legacy_index,
            "fixed10k_original_row_index": legacy_index,
            "target_id": target_row["target_id"],
            "panel": MODULE.PANEL,
            "model_role": role,
            "model_id": model_id,
            "inference_mode": "one_shot_hard_feasible_topology_v1",
            "q_one_sided_shortfall": max(float(target[2] - prediction[2]), 0.0),
            "q_target_met": bool(prediction[2] >= target[2]),
            "geometry_sha256_12decimal_float64": MODULE._geometry_sha256(geometry),
        }
        for feature_index, suffix in enumerate(MODULE.FEATURE_SUFFIXES):
            row[f"target__{suffix}"] = float(target[feature_index])
            row[f"proxy_prediction__{suffix}"] = float(prediction[feature_index])
            row[f"signed_error__{suffix}"] = float(residual[feature_index])
            row[f"absolute_error__{suffix}"] = abs(float(residual[feature_index]))
        for column, value in zip(MODULE.GEOMETRY_COLUMNS, geometry):
            row[column] = float(value)
        rows.append(row)
    if role == "200k":
        rows.reverse()
    return rows


def _realized_argv(
    evaluator_path: Path,
    trainer_path: Path,
    targets_path: Path,
    reference_contract_path: Path,
    evaluation_dir: Path,
    summary_paths: Mapping[str, Path],
    weights_paths: Mapping[str, Path],
) -> List[str]:
    argv = [sys.executable, str(evaluator_path.resolve())]
    model_ids = {
        "100k": MODULE.EXPECTED_REFERENCE_MODEL_ID,
        "200k": SYNTHETIC_CANDIDATE_ID,
    }
    for role in ("100k", "200k"):
        argv.extend(
            [
                f"--model-{role}-id",
                model_ids[role],
                f"--model-{role}-summary",
                str(summary_paths[role].resolve()),
                f"--model-{role}-weights",
                str(weights_paths[role].resolve()),
                f"--model-{role}-trainer-source",
                str(trainer_path.resolve()),
                f"--expected-model-{role}-summary-sha256",
                _sha256(summary_paths[role]),
                f"--expected-model-{role}-weights-sha256",
                _sha256(weights_paths[role]),
                f"--expected-model-{role}-trainer-sha256",
                _sha256(trainer_path),
            ]
        )
    argv.extend(
        [
            "--targets-json",
            str(targets_path.resolve()),
            "--expected-targets-sha256",
            _sha256(targets_path),
            "--reference-contract",
            str(reference_contract_path.resolve()),
            "--expected-reference-contract-sha256",
            _sha256(reference_contract_path),
            "--out-dir",
            str(evaluation_dir.resolve()),
        ]
    )
    return argv


def _build_controller_fixture(tmp_path: Path) -> Dict[str, Any]:
    run_dir = tmp_path / MODULE.EXPECTED_RUN_ID
    evaluation_dir = run_dir / "evaluation"
    model_dir = run_dir / "models"
    evaluation_dir.mkdir(parents=True)
    model_dir.mkdir()

    trainer_path = run_dir / "synthetic_exact_trainer.py"
    trainer_helper_path = run_dir / "synthetic_model_splitting.py"
    evaluator_path = run_dir / "evaluate_architecture_matched_fixed8k.py"
    trainer_path.write_text("# synthetic hash-bound trainer source\n", encoding="utf-8")
    trainer_helper_path.write_text("# synthetic hash-bound trainer helper\n", encoding="utf-8")
    evaluator_path.write_text("# synthetic hash-bound evaluator source\n", encoding="utf-8")

    weights_paths = {
        "100k": model_dir / "reference_weights.npz",
        "200k": model_dir / "candidate_weights.npz",
    }
    for path in weights_paths.values():
        _write_exact_architecture_weights(path)

    history_paths = {
        "100k": model_dir / "reference_history.csv",
        "200k": model_dir / "candidate_history.csv",
    }
    _write_history(history_paths["100k"], 1.0)
    _write_history(history_paths["200k"], 0.8)

    summary_paths = {
        "100k": model_dir / "reference_summary.json",
        "200k": model_dir / "candidate_summary.json",
    }
    _write_json(
        summary_paths["100k"],
        _model_summary(
            "100k",
            MODULE.EXPECTED_REFERENCE_MODEL_ID,
            weights_paths["100k"],
            history_paths["100k"],
        ),
    )
    _write_json(
        summary_paths["200k"],
        _model_summary(
            "200k",
            SYNTHETIC_CANDIDATE_ID,
            weights_paths["200k"],
            history_paths["200k"],
        ),
    )

    targets_path = run_dir / "fixed_targets.json"
    targets = _targets_payload()
    _write_json(targets_path, targets)
    reference_contract_path = run_dir / "reference_contract.json"
    _write_json(
        reference_contract_path,
        {"schema": "synthetic_reference_contract_v1", "fixture_only": True},
    )

    prediction_paths = {
        "100k": evaluation_dir / "per_target_100k_predictions.csv",
        "200k": evaluation_dir / "per_target_200k_predictions.csv",
    }
    _write_csv(
        prediction_paths["100k"],
        _prediction_rows(targets, "100k", MODULE.EXPECTED_REFERENCE_MODEL_ID),
    )
    _write_csv(
        prediction_paths["200k"],
        _prediction_rows(targets, "200k", SYNTHETIC_CANDIDATE_ID),
    )
    comparison_path = evaluation_dir / "architecture_matched_comparison.csv"
    _write_csv(
        comparison_path,
        [{"comparison": "synthetic paired proxy fixture", "row_count": 8}],
    )

    evaluation_summary_path = evaluation_dir / "evaluation_summary.json"
    evaluation_summary = {
        "schema": "architecture_matched_fixed_legacy8k_proxy_evaluation_v2",
        "evaluation_execution_status": "PASS",
        "advisor_comparison_eligible": False,
        "contract_checks": {
            "terminal_training_pass": True,
            "legacy_panel_exact": True,
            "hash_bindings_complete": True,
        },
        "outputs": {
            path.name: {"sha256": _sha256(path)}
            for path in (
                prediction_paths["100k"],
                prediction_paths["200k"],
                comparison_path,
            )
        },
        "sources": {
            "100k_summary": {"sha256": _sha256(summary_paths["100k"])},
            "100k_weights": {"sha256": _sha256(weights_paths["100k"])},
            "200k_summary": {"sha256": _sha256(summary_paths["200k"])},
            "200k_weights": {"sha256": _sha256(weights_paths["200k"])},
        },
        "models": {
            role: {
                "weights_sha256": _sha256(weights_paths[role]),
                "forward_architecture": list(MODULE.EXPECTED_FORWARD_ARCHITECTURE),
                "inverse_architecture": list(MODULE.EXPECTED_INVERSE_ARCHITECTURE),
                "projection_mode": MODULE.PROJECTION_MODE,
                "parameter_count": MODULE.EXPECTED_PARAMETER_COUNT,
            }
            for role in ("100k", "200k")
        },
    }
    _write_json(evaluation_summary_path, evaluation_summary)

    finite_observer_path = run_dir / "FINITE_OBSERVER_RECEIPT.json"
    _write_json(
        finite_observer_path,
        {
            "schema": "exact_trainer_finite_update_observer_v1",
            "status": "PASS",
            "fixture_only": True,
        },
    )

    realized_argv_path = run_dir / "REALIZED_EVALUATION_ARGV.json"
    template_evaluator_argv_sha = hashlib.sha256(
        b"synthetic exact evaluator argv template"
    ).hexdigest()
    _write_json(
        realized_argv_path,
        {
            "schema": "deployed100k_exact_contract_on_200k_realized_evaluation_argv_v1",
            "template_argv_sha256": template_evaluator_argv_sha,
            "argv": _realized_argv(
                evaluator_path,
                trainer_path,
                targets_path,
                reference_contract_path,
                evaluation_dir,
                summary_paths,
                weights_paths,
            ),
        },
    )
    realized_command_path = run_dir / "REALIZED_EVALUATION_COMMAND.txt"
    realized_command_path.write_text(
        "synthetic realized evaluator command\n", encoding="utf-8"
    )

    candidate_artifacts = {
        "summary": _artifact_record(summary_paths["200k"]),
        "weights": _artifact_record(weights_paths["200k"]),
    }
    evaluation_artifacts = [
        _artifact_record(path, path.name)
        for path in (
            prediction_paths["100k"],
            prediction_paths["200k"],
            comparison_path,
            evaluation_summary_path,
        )
    ]
    evaluation_sums_path = evaluation_dir / "SHA256SUMS.txt"
    evaluation_sums_path.write_text(
        "".join(
            f"{record['sha256']}  {record['relative_path']}\n"
            for record in evaluation_artifacts
        ),
        encoding="ascii",
    )
    fixed_sha = _sha256(targets_path)
    reference_summary_sha = _sha256(summary_paths["100k"])
    reference_weights_sha = _sha256(weights_paths["100k"])
    trainer_sha = _sha256(trainer_path)
    trainer_helper_sha = _sha256(trainer_helper_path)
    realized_argv_sha = _sha256(realized_argv_path)
    realized_command_sha = _sha256(realized_command_path)
    evaluator_pid = 424242
    synthetic_hashes = {
        name: hashlib.sha256(f"synthetic identity: {name}".encode("ascii")).hexdigest()
        for name in (
            "dataset_binding",
            "python",
            "numpy_core",
            "blas",
            "dataset",
            "trainer_argv",
        )
    }
    preflight_identities = {
        "reference_contract": _sha256(reference_contract_path),
        "dataset_binding": synthetic_hashes["dataset_binding"],
        "trainer": trainer_sha,
        "trainer_helper": trainer_helper_sha,
        "python": synthetic_hashes["python"],
        "numpy_core": synthetic_hashes["numpy_core"],
        "blas": synthetic_hashes["blas"],
        "trainer_entrypoint": trainer_sha,
        "dataset": synthetic_hashes["dataset"],
        "reference_summary": reference_summary_sha,
        "reference_weights": reference_weights_sha,
        "fixed_targets": fixed_sha,
        "evaluator": _sha256(evaluator_path),
        "trainer_argv": synthetic_hashes["trainer_argv"],
        "evaluator_argv": template_evaluator_argv_sha,
    }
    receipts = {
        "RUN_STATUS.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["RUN_STATUS.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["RUN_STATUS.json"][1],
            "state": "COMPLETE",
            "trainer_returncode": 0,
            "evaluator_returncode": 0,
        },
        "PREFLIGHT_RECEIPT.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["PREFLIGHT_RECEIPT.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["PREFLIGHT_RECEIPT.json"][1],
            "completed_utc": "2026-08-24T00:55:00Z",
            "identities": preflight_identities,
            "trainer": {"path": str(trainer_path.resolve()), "sha256": trainer_sha},
            "trainer_helper": {
                "path": str(trainer_helper_path.resolve()),
                "sha256": trainer_helper_sha,
                "exact_import_location": True,
            },
            "runtime_identity": {
                "python_sha256": synthetic_hashes["python"],
                "numpy_version": "2.0.2",
                "numpy_core_sha256": synthetic_hashes["numpy_core"],
                "blas_sha256": synthetic_hashes["blas"],
            },
            "trainer_entrypoint": {
                "path": str(trainer_path.resolve()),
                "sha256": trainer_sha,
                "is_observer_wrapper": False,
            },
        },
        "LAUNCH_RECEIPT.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["LAUNCH_RECEIPT.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["LAUNCH_RECEIPT.json"][1],
            "trainer_pid": MODULE.EXPECTED_TRAINER_PID,
            "launched_utc": "2026-08-24T00:56:53Z",
            "trainer_sha256": trainer_sha,
            "trainer_helper_path": str(trainer_helper_path.resolve()),
            "trainer_helper_sha256": trainer_helper_sha,
            "exact_train_argv_sha256": synthetic_hashes["trainer_argv"],
            "trainer_entrypoint_sha256": trainer_sha,
            "dataset_sha256": synthetic_hashes["dataset"],
            "python_sha256": synthetic_hashes["python"],
            "numpy_version": "2.0.2",
            "numpy_core_sha256": synthetic_hashes["numpy_core"],
            "blas_sha256": synthetic_hashes["blas"],
        },
        "TRAINING_RECEIPT.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["TRAINING_RECEIPT.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["TRAINING_RECEIPT.json"][1],
            "trainer_pid": MODULE.EXPECTED_TRAINER_PID,
            "trainer_returncode": 0,
            "completed_utc": "2026-08-24T01:56:53Z",
            "finite_observer_receipt": {
                "status": "PASS",
                "runtime_checks_all_true": True,
                "loaded_blas_sha_set_exact": True,
                "observed_steps": {"forward_proxy": 3, "tandem_inverse": 3},
                **_artifact_record(finite_observer_path),
            },
            "candidate_artifacts": candidate_artifacts,
            "trainer_helper_sha256": trainer_helper_sha,
            "python_sha256": synthetic_hashes["python"],
            "numpy_version": "2.0.2",
            "numpy_core_sha256": synthetic_hashes["numpy_core"],
            "blas_sha256": synthetic_hashes["blas"],
        },
        "EVALUATION_LAUNCH_RECEIPT.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["EVALUATION_LAUNCH_RECEIPT.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["EVALUATION_LAUNCH_RECEIPT.json"][1],
            "evaluator_pid": evaluator_pid,
            "launched_utc": "2026-08-24T02:00:00Z",
            "evaluator_sha256": _sha256(evaluator_path),
            "fixed_targets_sha256": fixed_sha,
            "reference_summary_sha256": reference_summary_sha,
            "reference_weights_sha256": reference_weights_sha,
            "realized_evaluator_argv_sha256": realized_argv_sha,
            "realized_evaluator_command_sha256": realized_command_sha,
            "candidate_artifacts": candidate_artifacts,
            "trainer_helper_sha256": trainer_helper_sha,
            "template_evaluator_argv_sha256": template_evaluator_argv_sha,
        },
        "EVALUATION_RECEIPT.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["EVALUATION_RECEIPT.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["EVALUATION_RECEIPT.json"][1],
            "evaluator_pid": evaluator_pid,
            "evaluator_returncode": 0,
            "completed_utc": "2026-08-24T02:05:00Z",
            "candidate_artifacts": candidate_artifacts,
            "fixed_targets_sha256": fixed_sha,
            "reference_summary_sha256": reference_summary_sha,
            "reference_weights_sha256": reference_weights_sha,
            "realized_evaluator_argv_sha256": realized_argv_sha,
            "realized_evaluator_command_sha256": realized_command_sha,
            "template_evaluator_argv_sha256": template_evaluator_argv_sha,
            "evaluation_artifacts": evaluation_artifacts,
            "sha256s": _artifact_record(evaluation_sums_path),
        },
        "COMPLETE_RECEIPT.json": {
            "schema": MODULE.RECEIPT_CONTRACTS["COMPLETE_RECEIPT.json"][0],
            "overall_status": MODULE.RECEIPT_CONTRACTS["COMPLETE_RECEIPT.json"][1],
            "trainer_pid": MODULE.EXPECTED_TRAINER_PID,
            "trainer_returncode": 0,
            "evaluator_pid": evaluator_pid,
            "evaluator_returncode": 0,
            "finite_observer_receipt_sha256": _sha256(finite_observer_path),
            "candidate_summary_sha256": _sha256(summary_paths["200k"]),
            "candidate_weights_sha256": _sha256(weights_paths["200k"]),
            "fixed_targets_sha256": fixed_sha,
            "reference_summary_sha256": reference_summary_sha,
            "reference_weights_sha256": reference_weights_sha,
            "reference_contract_sha256": _sha256(reference_contract_path),
            "dataset_binding_sha256": synthetic_hashes["dataset_binding"],
            "trainer_sha256": trainer_sha,
            "trainer_entrypoint_sha256": trainer_sha,
            "trainer_helper_sha256": trainer_helper_sha,
            "python_sha256": synthetic_hashes["python"],
            "numpy_version": "2.0.2",
            "numpy_core_sha256": synthetic_hashes["numpy_core"],
            "blas_sha256": synthetic_hashes["blas"],
            "dataset_sha256": synthetic_hashes["dataset"],
            "template_evaluator_argv_sha256": template_evaluator_argv_sha,
            "realized_evaluator_argv_sha256": realized_argv_sha,
        },
    }
    receipt_paths: Dict[str, Path] = {}
    for filename, payload in receipts.items():
        path = run_dir / filename
        _write_json(path, payload)
        receipt_paths[filename] = path

    return {
        "run_dir": run_dir,
        "targets_path": targets_path,
        "targets_sha256": _sha256(targets_path),
        "summary_paths": summary_paths,
        "weights_paths": weights_paths,
        "prediction_paths": prediction_paths,
        "comparison_path": comparison_path,
        "evaluation_summary_path": evaluation_summary_path,
        "realized_argv_path": realized_argv_path,
        "realized_command_path": realized_command_path,
        "evaluation_sums_path": evaluation_sums_path,
        "receipt_paths": receipt_paths,
    }


def _build(fixture: Mapping[str, Any], out_dir: Path, target_sha: str = "") -> Dict[str, Any]:
    return MODULE.build_statistics(
        fixture["run_dir"],
        out_dir,
        bootstrap_replicates=SYNTHETIC_BOOTSTRAP_REPLICATES,
        synthetic_fixture=True,
        synthetic_expected_targets_sha256=target_sha or fixture["targets_sha256"],
        synthetic_expected_target_rows=SYNTHETIC_TARGET_ROWS,
        synthetic_expected_legacy_rows=SYNTHETIC_LEGACY_ROWS,
        synthetic_inference_seconds={"100k": 0.0125, "200k": 0.0100},
    )


def _refresh_evaluation_bindings(fixture: Mapping[str, Any]) -> None:
    summary_path = fixture["evaluation_summary_path"]
    summary = _read_json(summary_path)
    for path in (
        fixture["prediction_paths"]["100k"],
        fixture["prediction_paths"]["200k"],
        fixture["comparison_path"],
    ):
        summary["outputs"][path.name]["sha256"] = _sha256(path)
    _write_json(summary_path, summary)

    receipt_path = fixture["receipt_paths"]["EVALUATION_RECEIPT.json"]
    receipt = _read_json(receipt_path)
    evaluation_artifacts = [
        _artifact_record(path, path.name)
        for path in (
            fixture["prediction_paths"]["100k"],
            fixture["prediction_paths"]["200k"],
            fixture["comparison_path"],
            summary_path,
        )
    ]
    receipt["evaluation_artifacts"] = evaluation_artifacts
    fixture["evaluation_sums_path"].write_text(
        "".join(
            f"{record['sha256']}  {record['relative_path']}\n"
            for record in evaluation_artifacts
        ),
        encoding="ascii",
    )
    receipt["sha256s"] = _artifact_record(fixture["evaluation_sums_path"])
    _write_json(receipt_path, receipt)


def _refresh_reference_summary_bindings(fixture: Mapping[str, Any]) -> None:
    summary_sha = _sha256(fixture["summary_paths"]["100k"])
    realized = _read_json(fixture["realized_argv_path"])
    argv = realized["argv"]
    flag_index = argv.index("--expected-model-100k-summary-sha256")
    argv[flag_index + 1] = summary_sha
    _write_json(fixture["realized_argv_path"], realized)
    realized_sha = _sha256(fixture["realized_argv_path"])

    preflight_path = fixture["receipt_paths"]["PREFLIGHT_RECEIPT.json"]
    preflight = _read_json(preflight_path)
    preflight["identities"]["reference_summary"] = summary_sha
    _write_json(preflight_path, preflight)

    for filename in (
        "EVALUATION_LAUNCH_RECEIPT.json",
        "EVALUATION_RECEIPT.json",
        "COMPLETE_RECEIPT.json",
    ):
        path = fixture["receipt_paths"][filename]
        receipt = _read_json(path)
        receipt["reference_summary_sha256"] = summary_sha
        receipt["realized_evaluator_argv_sha256"] = realized_sha
        _write_json(path, receipt)

    evaluation_summary = _read_json(fixture["evaluation_summary_path"])
    evaluation_summary["sources"]["100k_summary"]["sha256"] = summary_sha
    _write_json(fixture["evaluation_summary_path"], evaluation_summary)
    _refresh_evaluation_bindings(fixture)


def _mutate_prediction_csv(
    fixture: Mapping[str, Any],
    mutate: Callable[[List[Dict[str, str]]], None],
) -> None:
    path = fixture["prediction_paths"]["200k"]
    rows = _read_csv(path)
    mutate(rows)
    _write_csv(path, rows)
    _refresh_evaluation_bindings(fixture)


def test_metric_formulas_use_fixed_spans_q_shortfall_and_engineering_joint() -> None:
    target = np.asarray(
        [
            [1.0, 1.0, 10.0, 0.10],
            [2.0, 1.0, 12.0, 0.20],
            [3.0, 1.0, 14.0, 0.30],
            [4.0, 1.0, 16.0, 0.40],
        ],
        dtype=float,
    )
    prediction = target + np.asarray(
        [
            [-1.0, 0.0, -2.0, 0.08],
            [0.0, 0.0, 1.0, -0.08],
            [1.0, 0.0, 0.0, 0.16],
            [2.0, 0.0, 4.0, -0.16],
        ],
        dtype=float,
    )

    feature_rows = {
        row["feature"]: row for row in MODULE._feature_metric_values(target, prediction)
    }
    lp = feature_rows["Lp"]
    assert lp["count"] == 4
    assert lp["bias"] == pytest.approx(0.5)
    assert lp["mae"] == pytest.approx(1.0)
    assert lp["rmse"] == pytest.approx(math.sqrt(1.5))
    assert lp["median_absolute_error"] == pytest.approx(1.0)
    assert lp["p90_absolute_error"] == pytest.approx(1.7)
    assert lp["p95_absolute_error"] == pytest.approx(1.85)
    assert lp["p99_absolute_error"] == pytest.approx(1.97)
    assert lp["maximum_absolute_error"] == pytest.approx(2.0)
    assert lp["normalized_mae"] == pytest.approx(0.4)
    assert lp["normalized_rmse"] == pytest.approx(math.sqrt(1.5) / 2.5)

    k_abs = feature_rows["|K|"]
    assert k_abs["normalization_span"] == pytest.approx(0.8)
    assert k_abs["normalized_mae"] == pytest.approx(0.15)
    assert k_abs["normalized_rmse"] == pytest.approx(
        math.sqrt(np.mean(np.asarray([0.08, 0.08, 0.16, 0.16]) ** 2)) / 0.8
    )

    arrays = MODULE._engineering_arrays(target, prediction)
    np.testing.assert_allclose(arrays["q_shortfall"], [2.0, 0.0, 0.0, 0.0])
    np.testing.assert_array_equal(arrays["q_target_met"], [False, True, True, True])
    assert np.mean(arrays["q_target_met"]) == pytest.approx(0.75)
    assert np.mean(arrays["q_shortfall"]) == pytest.approx(0.5)
    assert np.sqrt(np.mean(arrays["q_shortfall"] ** 2)) == pytest.approx(1.0)
    assert np.percentile(arrays["q_shortfall"], 90.0) == pytest.approx(1.4)
    assert np.percentile(arrays["q_shortfall"], 95.0) == pytest.approx(1.7)
    assert np.mean(arrays["engineering_normalized"]) == pytest.approx(2.3 / 16.0)
    assert np.sqrt(np.mean(arrays["engineering_normalized"] ** 2)) == pytest.approx(
        math.sqrt(1.07 / 16.0)
    )


def test_full_synthetic_controller_run_is_paired_deterministic_and_swept(
    tmp_path: Path,
) -> None:
    fixture = _build_controller_fixture(tmp_path)
    out_a = tmp_path / "statistics_a"
    out_b = tmp_path / "statistics_b"
    result = _build(fixture, out_a)
    _build(fixture, out_b)

    assert result["n"] == SYNTHETIC_LEGACY_ROWS
    assert {path.name for path in out_a.iterdir()} == {
        "EVALUATION_CONTRACT.json",
        "INPUT_IDENTITY_AUDIT.json",
        "MODEL_CONTRACT_COMPARISON.json",
        "per_target_paired_errors.csv",
        "feature_metrics_long.csv",
        "joint_metrics.csv",
        "paired_delta_summary.csv",
        "paired_bootstrap_sensitivity.csv",
        "training_curves_long.csv",
        "geometry_feasibility_summary.json",
        "training_runtime_summary.json",
        "ADVISOR_REPORT_NOTES.md",
        "REPORT_SUMMARY.json",
    }

    contract = _read_json(out_a / "EVALUATION_CONTRACT.json")
    assert contract["normalization_spans"]["|K|"]["value"] == pytest.approx(0.8)
    assert contract["q_shortfall_definition"] == "max(target_Qmin - predicted_Qmin, 0)"
    assert contract["bootstrap"] == {
        "label": "paired finite-frame bootstrap sensitivity",
        "paired_index_reused_for_both_models": True,
        "replicates": SYNTHETIC_BOOTSTRAP_REPLICATES,
        "resampling_unit": "target_id",
        "scope_boundary": "finite fixed target-frame resampling sensitivity only",
        "seed": 20260824,
    }

    feature_rows = _read_csv(out_a / "feature_metrics_long.csv")
    reference_k = next(
        row
        for row in feature_rows
        if row["model_role"] == "100k" and row["feature"] == "|K|"
    )
    candidate_k = next(
        row
        for row in feature_rows
        if row["model_role"] == "200k" and row["feature"] == "|K|"
    )
    assert float(reference_k["normalization_span"]) == pytest.approx(0.8)
    assert float(reference_k["normalized_mae"]) == pytest.approx(0.1)
    assert float(candidate_k["normalized_mae"]) == pytest.approx(0.05)

    joint_rows = _read_csv(out_a / "joint_metrics.csv")

    def joint_value(role: str, metric: str) -> float:
        row = next(
            current
            for current in joint_rows
            if current["model_role"] == role and current["metric"] == metric
        )
        return float(row["value"])

    assert joint_value("100k", "q_target_met_fraction") == pytest.approx(0.75)
    assert joint_value("100k", "q_shortfall_mae") == pytest.approx(0.5)
    assert joint_value("100k", "q_shortfall_rmse") == pytest.approx(1.0)
    assert joint_value("100k", "q_shortfall_p90") == pytest.approx(2.0)
    assert joint_value("100k", "q_shortfall_p95") == pytest.approx(2.0)
    assert joint_value("100k", "joint_normalized_mae") == pytest.approx(4.2 / 32.0)
    assert joint_value("100k", "joint_normalized_rmse") == pytest.approx(
        math.sqrt(0.74 / 32.0)
    )
    assert joint_value("200k", "joint_normalized_mae") == pytest.approx(2.1 / 32.0)
    assert joint_value("200k", "joint_normalized_rmse") == pytest.approx(
        math.sqrt(0.185 / 32.0)
    )

    paired_rows = _read_csv(out_a / "per_target_paired_errors.csv")
    assert [row["target_id"] for row in paired_rows] == [
        f"target_{index:04d}" for index in range(SYNTHETIC_LEGACY_ROWS)
    ]
    first = paired_rows[0]
    assert float(first["delta_absolute_error_200k_minus_100k__lp"]) == pytest.approx(
        -0.25
    )
    assert float(first["delta_absolute_error_200k_minus_100k__qmin"]) == pytest.approx(
        -1.0
    )
    assert float(
        first["delta_normalized_absolute_error_200k_minus_100k__k_abs"]
    ) == pytest.approx(-0.05)

    bootstrap_a = (out_a / "paired_bootstrap_sensitivity.csv").read_bytes()
    bootstrap_b = (out_b / "paired_bootstrap_sensitivity.csv").read_bytes()
    assert bootstrap_a == bootstrap_b
    assert b"paired finite-frame bootstrap sensitivity" in bootstrap_a
    assert b"deployment population confidence interval" not in bootstrap_a.lower()
    bootstrap_rows = _read_csv(out_a / "paired_bootstrap_sensitivity.csv")
    assert {int(row["bootstrap_seed"]) for row in bootstrap_rows} == {20260824}
    assert {int(row["bootstrap_replicates"]) for row in bootstrap_rows} == {
        SYNTHETIC_BOOTSTRAP_REPLICATES
    }
    k_bootstrap = next(
        row
        for row in bootstrap_rows
        if row["scope"] == "feature"
        and row["feature"] == "|K|"
        and row["metric"] == "normalized_mae"
    )
    assert float(k_bootstrap["point_delta_200k_minus_100k"]) == pytest.approx(-0.05)
    assert float(k_bootstrap["bootstrap_delta_mean"]) == pytest.approx(-0.05)

    sweep = [row for row in joint_rows if row["section"] == "success_rate_sweep"]
    assert len(sweep) == 102
    for role in ("100k", "200k"):
        role_rows = [row for row in sweep if row["model_role"] == role]
        tolerances = [float(row["tolerance_normalized"]) for row in role_rows]
        rates = [float(row["value"]) for row in role_rows]
        assert tolerances[0] == pytest.approx(0.0)
        assert tolerances[-1] == pytest.approx(0.25)
        assert all(left < right for left, right in zip(tolerances, tolerances[1:]))
        assert all(left <= right for left, right in zip(rates, rates[1:]))
        assert {row["curve_label"] for row in role_rows} == {
            MODULE.SWEEP_LABEL
        }


def _case_high_k_leakage(fixture: Mapping[str, Any]) -> None:
    def mutate(rows: List[Dict[str, str]]) -> None:
        rows[0]["target_id"] = "target_0008"
        rows[0]["fixed10k_original_row_index"] = "8"

    _mutate_prediction_csv(fixture, mutate)


def _case_duplicate_target(fixture: Mapping[str, Any]) -> None:
    def mutate(rows: List[Dict[str, str]]) -> None:
        rows[1]["target_id"] = rows[0]["target_id"]

    _mutate_prediction_csv(fixture, mutate)


def _case_missing_target(fixture: Mapping[str, Any]) -> None:
    def mutate(rows: List[Dict[str, str]]) -> None:
        rows.pop()

    _mutate_prediction_csv(fixture, mutate)


def _case_nan_prediction(fixture: Mapping[str, Any]) -> None:
    def mutate(rows: List[Dict[str, str]]) -> None:
        rows[0]["proxy_prediction__lp"] = "nan"

    _mutate_prediction_csv(fixture, mutate)


def _case_decoder_drift(fixture: Mapping[str, Any]) -> None:
    path = fixture["summary_paths"]["100k"]
    summary = _read_json(path)
    summary["arguments"]["inverse_geometry_projection"] = "independent_sigmoid"
    summary["method"]["geometry_output_constraint"] = "independent_sigmoid"
    _write_json(path, summary)
    _refresh_reference_summary_bindings(fixture)


def _case_receipt_status(fixture: Mapping[str, Any]) -> None:
    path = fixture["receipt_paths"]["TRAINING_RECEIPT.json"]
    receipt = _read_json(path)
    receipt["overall_status"] = "RUNNING"
    _write_json(path, receipt)


def _case_hash_binding(fixture: Mapping[str, Any]) -> None:
    path = fixture["summary_paths"]["200k"]
    summary = _read_json(path)
    summary["tampered_after_receipt"] = True
    _write_json(path, summary)


FAILURE_CASES: Tuple[Tuple[str, Callable[[Mapping[str, Any]], None], str], ...] = (
    ("high_k_leakage", _case_high_k_leakage, "not in legacy panel"),
    ("duplicate_target", _case_duplicate_target, "duplicated"),
    ("missing_target", _case_missing_target, "row count differs"),
    ("nan_prediction", _case_nan_prediction, "NaN or Inf"),
    ("decoder_drift", _case_decoder_drift, "summary decoder differs"),
    ("receipt_status", _case_receipt_status, "terminal status is not PASS"),
    ("hash_binding", _case_hash_binding, "candidate summary SHA256 mismatch"),
)


@pytest.mark.parametrize(
    ("case_name", "mutate", "match"),
    FAILURE_CASES,
    ids=[case[0] for case in FAILURE_CASES],
)
def test_fail_closed_before_output_for_identity_and_receipt_gates(
    tmp_path: Path,
    case_name: str,
    mutate: Callable[[Mapping[str, Any]], None],
    match: str,
) -> None:
    fixture = _build_controller_fixture(tmp_path)
    mutate(fixture)
    out_dir = tmp_path / f"blocked_{case_name}"
    with pytest.raises(MODULE.ContractError, match=match):
        _build(fixture, out_dir)
    assert not out_dir.exists()


def test_fixed_target_sha_mismatch_fails_before_output(tmp_path: Path) -> None:
    fixture = _build_controller_fixture(tmp_path)
    out_dir = tmp_path / "blocked_fixed_sha"
    with pytest.raises(MODULE.ContractError, match="fixed10k SHA"):
        _build(fixture, out_dir, target_sha="0" * 64)
    assert not out_dir.exists()


def test_no_clobber_preserves_completed_synthetic_output(tmp_path: Path) -> None:
    fixture = _build_controller_fixture(tmp_path)
    out_dir = tmp_path / "statistics"
    _build(fixture, out_dir)
    before = {
        path.name: _sha256(path)
        for path in out_dir.iterdir()
        if path.is_file()
    }

    with pytest.raises(MODULE.ContractError, match="no-clobber"):
        _build(fixture, out_dir)

    after = {
        path.name: _sha256(path)
        for path in out_dir.iterdir()
        if path.is_file()
    }
    assert after == before


def test_synthetic_output_is_rejected_outside_platform_temp_directory() -> None:
    destination = REPO / "docs" / "__forbidden_synthetic_fixed8k_output__"
    assert not destination.exists()
    with pytest.raises(MODULE.ContractError, match="platform temporary directory"):
        MODULE.build_statistics(
            REPO / "unused_synthetic_controller_fixture",
            destination,
            expected_run_id="synthetic",
            expected_trainer_pid=1,
            bootstrap_replicates=1,
            synthetic_fixture=True,
            synthetic_expected_targets_sha256="a" * 64,
            synthetic_expected_target_rows=1,
            synthetic_expected_legacy_rows=1,
            synthetic_inference_seconds={"100k": 0.01, "200k": 0.01},
        )
    assert not destination.exists()
