from __future__ import annotations

import csv
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_controlled_real10k_20k_common.py"
TRAINER = ROOT / "scripts" / "train_physical_feature_tandem_inverse.py"
RUNNER = ROOT / "scripts" / "run_controlled_real10k_20k_paired.py"
SHARED_CONTRACT = (
    ROOT / "rfic_transformer_inverse_design" / "controlled_real10k_20k_contract.py"
)
PREREGISTRATION_ADDENDUM = (
    ROOT
    / "reports"
    / "controlled_real10k_20k_nested_20260824"
    / "CONTROLLED_EXPERIMENT_PREREGISTRATION_ADDENDUM_V1_2.json"
)
SPEC = importlib.util.spec_from_file_location("controlled_common_evaluator_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EVALUATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVALUATOR
SPEC.loader.exec_module(EVALUATOR)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _binding(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha(path), "size_bytes": path.stat().st_size}


def _artifact_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "path": str(path),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _write_runner_final_index(controller_root: Path) -> Path:
    final_index = controller_root / EVALUATOR.PAIRED_FINAL_INDEX_NAME
    members = sorted(
        (
            path
            for path in controller_root.rglob("*")
            if path.is_file()
            and path.relative_to(controller_root).as_posix()
            not in {"controller.lock", "SHA256SUMS.txt", EVALUATOR.PAIRED_FINAL_INDEX_NAME}
        ),
        key=lambda path: path.relative_to(controller_root).as_posix(),
    )
    final_index.write_text(
        "".join(
            f"{_sha(path)}  {path.relative_to(controller_root).as_posix()}\n"
            for path in members
        ),
        encoding="ascii",
    )
    return final_index


def _identity(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _weights(path: Path, normalization_sha: str, gradient_train_rows: int) -> None:
    arrays: dict[str, np.ndarray] = {}
    forward_widths = [10, 256, 256, 256, 4]
    inverse_widths = [4, 256, 256, 256, 10]
    for index, (left, right) in enumerate(zip(forward_widths, forward_widths[1:])):
        arrays[f"forward_weight_{index}"] = np.zeros((left, right), dtype=np.float64)
        arrays[f"forward_bias_{index}"] = np.zeros(right, dtype=np.float64)
    for index, (left, right) in enumerate(zip(inverse_widths, inverse_widths[1:])):
        arrays[f"inverse_weight_{index}"] = np.zeros((left, right), dtype=np.float64)
        arrays[f"inverse_bias_{index}"] = np.zeros(right, dtype=np.float64)
    input_lower = np.asarray(EVALUATOR.INPUT_LOWER, dtype=np.float64)
    input_upper = np.asarray(EVALUATOR.INPUT_UPPER, dtype=np.float64)
    geometry_lower = np.asarray(EVALUATOR.GEOMETRY_LOWER, dtype=np.float64)
    geometry_upper = np.asarray(EVALUATOR.GEOMETRY_UPPER, dtype=np.float64)
    arrays.update(
        {
            "normalization__x_mean": 0.5 * (input_lower + input_upper),
            "normalization__x_scale": 0.5 * (input_upper - input_lower),
            "normalization__y_mean": 0.5 * (geometry_lower + geometry_upper),
            "normalization__y_scale": 0.5 * (geometry_upper - geometry_lower),
            "normalization__feature_lower": input_lower,
            "normalization__feature_upper": input_upper,
            "normalization__geometry_lower": -np.ones(10, dtype=np.float64),
            "normalization__geometry_upper": np.ones(10, dtype=np.float64),
            "normalization__response_loss_dimension_weights": np.ones(4, dtype=np.float64),
            "normalization__response_loss_physical_spans": input_upper - input_lower,
            "normalization_contract__mode": np.asarray(["external_declared_midpoint_half_range"]),
            "normalization_contract__sha256": np.asarray([normalization_sha]),
            "training_sampler__family": np.asarray(["row_uniform"]),
            "training_sampler__fingerprint_sha256": np.asarray(["1" * 64]),
            "training_sampler__draws_per_epoch": np.asarray([gradient_train_rows], dtype=np.int64),
            "training_sampler__optimizer_updates_per_epoch": np.asarray(
                [int(np.ceil(gradient_train_rows / 1024.0))], dtype=np.int64
            ),
            "optimizer_budget__mode": np.asarray(["fixed_optimizer_updates"]),
            "optimizer_budget__fingerprint_sha256": np.asarray(["2" * 64]),
            "optimizer_budget__forward_target_updates": np.asarray([1200], dtype=np.int64),
            "optimizer_budget__inverse_target_updates": np.asarray([1200], dtype=np.int64),
            "inverse_geometry_projection__mode": np.asarray(["independent_sigmoid"]),
            "inverse_geometry_projection__topology_contract_json": np.asarray(
                [
                    json.dumps(
                        {
                            "enabled": False,
                            "weight": 0.0,
                            "geometry_columns": list(EVALUATOR.GEOMETRY_COLUMNS),
                            "power_line_port_ground_overlap": {"enabled": False},
                        },
                        sort_keys=True,
                    )
                ]
            ),
        }
    )
    np.savez_compressed(path, **arrays)


def _csv_fixture(path: Path, test_ids: list[str]) -> None:
    rows: list[dict[str, Any]] = []
    geometry_midpoint = 0.5 * (
        np.asarray(EVALUATOR.GEOMETRY_LOWER) + np.asarray(EVALUATOR.GEOMETRY_UPPER)
    )
    physical = ([1.75, 1.75, 15.0, 0.4], [1.25, 2.25, 10.0, 0.7])
    for index, identity in enumerate(test_ids):
        row: dict[str, Any] = {
            "controlled_source_row_number": index + 1,
            "controlled_origin": "synthetic_fixture",
            "controlled_physical_cell_4d": EVALUATOR.canonical_physical_cell_id(
                physical[index]
            ),
            "controlled_split_assignment": "test",
            "canonical_geometry_identity_sha256": identity,
            "portable_geometry_decimal12_sha256": _identity(f"portable-{index}"),
            "evaluation": f"synthetic-real-emx-{index}",
            "touchstone_path": f"/fixture/{index}.s4p",
            "touchstone_sha256": _identity(f"touchstone-{index}"),
        }
        row.update(dict(zip(EVALUATOR.INPUT_COLUMNS, physical[index])))
        row.update(dict(zip(EVALUATOR.GEOMETRY_COLUMNS, geometry_midpoint)))
        rows.append(row)
    with path.open("w", newline="", encoding="utf-8") as handle:
        # Use the frozen source schema even though the evaluator reads only test rows.
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "controlled_source_row_number",
                "controlled_origin",
                "controlled_physical_cell_4d",
                "controlled_split_assignment",
                "canonical_geometry_identity_sha256",
                "portable_geometry_decimal12_sha256",
                "evaluation",
                "touchstone_path",
                "touchstone_sha256",
                *EVALUATOR.INPUT_COLUMNS,
                *EVALUATOR.GEOMETRY_COLUMNS,
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _make_fixture(
    tmp_path: Path, *, shared_binding_override: dict[str, Any] | None = None
) -> dict[str, Any]:
    root = tmp_path / "contract"
    root.mkdir()
    normalization = root / "declared_midpoint_half_range_normalization_contract.json"
    _write_json(
        normalization,
        {
            "schema": EVALUATOR.NORMALIZATION_SCHEMA,
            "input_columns": list(EVALUATOR.INPUT_COLUMNS),
            "geometry_columns": list(EVALUATOR.GEOMETRY_COLUMNS),
            "input_lower": list(EVALUATOR.INPUT_LOWER),
            "input_upper": list(EVALUATOR.INPUT_UPPER),
            "geometry_lower": list(EVALUATOR.GEOMETRY_LOWER),
            "geometry_upper": list(EVALUATOR.GEOMETRY_UPPER),
            "train_arm_specific_statistics_used": False,
            "large_arm_empirical_statistics_used": False,
            "all_loaded_rows_required_inside_declared_bounds": True,
        },
    )
    test_ids = [_identity("test-0"), _identity("test-1")]
    holdout = root / "fixed_common_holdout_manifest.json"
    _write_json(
        holdout,
        {
            "schema": EVALUATOR.HOLDOUT_SCHEMA,
            "identity_kind": "canonical_geometry_sha256",
            "validation_geometry_identities": [],
            "test_geometry_identities": test_ids,
        },
    )
    small_csv = root / "arm_source_n10000.csv"
    _csv_fixture(small_csv, test_ids)
    large_csv = root / "arm_source_n20000.csv"
    large_csv.write_bytes(small_csv.read_bytes())
    material = root / "controlled_real10k_20k_nested_summary.json"
    _write_json(
        material,
        {
            "schema": EVALUATOR.MATERIAL_SCHEMA,
            "status": "PASS",
            "artifacts": {
                holdout.name: _binding(holdout),
                normalization.name: _binding(normalization),
                small_csv.name: _binding(small_csv),
                large_csv.name: _binding(large_csv),
            },
        },
    )
    targets = root / "fixed_targets.json"
    _write_json(
        targets,
        {
            "schema": "direct_mlp_one_shot_targets_v1",
            "targets": [
                {"target_id": "legacy-a", "Lp_nH": 1.75, "Ls_nH": 1.75, "Q_min": 15.0, "K_abs": 0.4},
                {"target_id": "legacy-b", "Lp_nH": 1.0, "Ls_nH": 2.0, "Q_min": 10.0, "K_abs": 0.8},
                {"target_id": "high-a", "Lp_nH": 2.0, "Ls_nH": 1.0, "Q_min": 20.0, "K_abs": 0.9},
                {"target_id": "high-b", "Lp_nH": 2.5, "Ls_nH": 2.5, "Q_min": 18.0, "K_abs": 0.95},
            ],
        },
    )
    normalization_sha = _sha(normalization)
    holdout_sha = _sha(holdout)
    controller_root = root / "controller"
    (controller_root / "receipts").mkdir(parents=True)
    (controller_root / "commands").mkdir()
    run_contract = controller_root / "run_contract.json"
    _write_json(
        run_contract,
        {
            "schema": EVALUATOR.LEGACY_FIXTURE_PAIRED_RUN_CONTRACT_SCHEMA,
            "out_dir": str(controller_root),
            "runner": _binding(RUNNER),
            "shared_contract": shared_binding_override or _binding(SHARED_CONTRACT),
            "trainer": _binding(TRAINER),
            "materialization": {
                "summary": _binding(material),
                "artifacts": {
                    "small_csv": _binding(small_csv),
                    "large_csv": _binding(large_csv),
                    "common_holdout": _binding(holdout),
                    "fixed_normalization": _binding(normalization),
                },
            },
            "paired_seeds": list(EVALUATOR.EXACT_PAIRED_SEEDS),
            "arm_order_within_seed": ["small", "large"],
            "process_contract": {
                "trainer_launch": EVALUATOR.FROZEN_TRAINER_LAUNCH_CONTRACT,
            },
            "training_contract": {
                "input_columns": list(EVALUATOR.INPUT_COLUMNS),
                "geometry_columns": list(EVALUATOR.GEOMETRY_COLUMNS),
                "forward_hidden_widths": [256, 256, 256],
                "inverse_hidden_widths": [256, 256, 256],
                "inverse_geometry_projection": "independent_sigmoid",
                "evaluation_mode": "validation_only",
                "test_access_event_count": 0,
            },
            "release_boundary": {
                "fresh_emx_accessed": False,
                "test_evaluation_performed": False,
                "numerical_metrics_released": False,
                "success_after_training": "READY_FOR_ONE_TIME_COMMON_TEST_EVALUATION",
            },
        },
    )
    pointer_paths: dict[tuple[int, str], Path] = {}
    attempt_paths: dict[tuple[int, str], Path] = {}
    summary_paths: dict[tuple[int, str], Path] = {}
    weights_paths: dict[tuple[int, str], Path] = {}
    for seed in EVALUATOR.EXACT_PAIRED_SEEDS:
        for arm in ("small", "large"):
            model_dir = controller_root / "runs" / f"seed_{seed}" / arm
            model_dir.mkdir(parents=True)
            weights = model_dir / "physical_feature_tandem_inverse_weights.npz"
            gradient_train_rows = 4 if arm == "small" else 6
            _weights(weights, normalization_sha, gradient_train_rows)
            summary = model_dir / "physical_feature_tandem_inverse_summary.json"
            _write_json(
                summary,
                {
                    "overall_status": "COMPLETE_REVIEW_REQUIRED",
                    "execution_status": "PASS",
                    "quality_status": "REVIEW_REQUIRED_VALIDATION_ONLY",
                    "eligible_for_checkpoint_model_acceptance": False,
                    "eligible_for_model_success_claim": False,
                    "evaluation_mode": "validation_only",
                    "training_count": 4 if arm == "small" else 6,
                    "input_columns": list(EVALUATOR.INPUT_COLUMNS),
                    "geometry_columns": list(EVALUATOR.GEOMETRY_COLUMNS),
                    "weights_npz": str(weights),
                    "arguments": {
                        "seed": seed,
                        "local_refinement_steps": 0,
                        "inverse_geometry_projection": "independent_sigmoid",
                    },
                    "test_access_contract": {
                        "test_access_event_count": 0,
                        "test_evaluator_called": False,
                        "test_used_for_training": False,
                        "test_used_for_model_or_hyperparameter_selection": False,
                    },
                    "normalization_contract": {
                        "mode": "external_declared_midpoint_half_range",
                        "sha256": normalization_sha,
                    },
                    "split_audit": {
                        "split_mode": "fixed_common_holdout_manifest",
                        "row_counts": {
                            "train": gradient_train_rows,
                            "validation": 1,
                            "test": 2,
                        },
                        "fixed_common_holdout_manifest": {"sha256": holdout_sha},
                    },
                },
            )
            command = controller_root / "commands" / f"seed_{seed}_{arm}.json"
            _write_json(command, {"seed": seed, "arm": arm, "fixture": True})
            receipt_dir = controller_root / "receipts" / f"seed_{seed}_{arm}"
            attempt_dir = receipt_dir / "attempt_0001"
            attempt_dir.mkdir(parents=True)
            intent = attempt_dir / "INTENT_RECEIPT.json"
            running = attempt_dir / "RUNNING_RECEIPT.json"
            stdout = attempt_dir / "stdout.log"
            stderr = attempt_dir / "stderr.log"
            output_manifest = attempt_dir / "OUTPUT_ARTIFACT_MANIFEST.json"
            _write_json(intent, {"fixture": "intent", "seed": seed, "arm": arm})
            _write_json(running, {"fixture": "running", "seed": seed, "arm": arm})
            stdout.write_text("fixture stdout\n", encoding="utf-8")
            stderr.write_text("", encoding="utf-8")
            _write_json(
                output_manifest,
                {
                    "schema": "controlled_real10k_20k_arm_output_manifest_v1",
                    "root": str(model_dir),
                    "artifacts": [
                        _artifact_record(summary, model_dir),
                        _artifact_record(weights, model_dir),
                    ],
                    "excluded_paths": [],
                    "all_regular_outputs_indexed": True,
                },
            )
            attempt = attempt_dir / "COMPLETE_RECEIPT.json"
            _write_json(
                attempt,
                {
                    "schema": EVALUATOR.LEGACY_FIXTURE_ARM_TERMINAL_RECEIPT_SCHEMA,
                    "generated_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "COMPLETE",
                    "seed": seed,
                    "arm": arm,
                    "returncode": 0,
                    "evaluation_mode": "validation_only",
                    "test_access_event_count": 0,
                    "python_isolation_flags": EVALUATOR.FROZEN_TRAINER_LAUNCH_CONTRACT[
                        "python_isolation_flags"
                    ],
                    "effective_environment": EVALUATOR.FROZEN_TRAINER_LAUNCH_CONTRACT[
                        "effective_environment"
                    ],
                    "effective_environment_sha256": EVALUATOR.FROZEN_TRAINER_LAUNCH_CONTRACT[
                        "effective_environment_sha256"
                    ],
                    "run_contract": _binding(run_contract),
                    "command": _binding(command),
                    "intent": _binding(intent),
                    "running": _binding(running),
                    "stdout": _binding(stdout),
                    "stderr": _binding(stderr),
                    "output_manifest": _binding(output_manifest),
                    "contract_checks": {"fixture_contract": True, "test_sealed": True},
                    "summary": _binding(summary),
                    "weights": {
                        **_binding(weights),
                        "required_layer_shapes_exact": True,
                        "all_numeric_finite": True,
                    },
                    "fresh_emx_accessed": False,
                    "numerical_metrics_released": False,
                },
            )
            pointer = receipt_dir / "COMPLETE_RECEIPT.json"
            _write_json(
                pointer,
                {
                    "schema": EVALUATOR.ARM_TERMINAL_POINTER_SCHEMA,
                    "status": "COMPLETE",
                    "seed": seed,
                    "arm": arm,
                    "attempt_complete": _binding(attempt),
                },
            )
            pointer_paths[(seed, arm)] = pointer
            attempt_paths[(seed, arm)] = attempt
            summary_paths[(seed, arm)] = summary
            weights_paths[(seed, arm)] = weights
    pair_paths: dict[int, Path] = {}
    for seed in EVALUATOR.EXACT_PAIRED_SEEDS:
        receipt = controller_root / "receipts" / f"seed_{seed}_PAIR_COMPLETE_RECEIPT.json"
        _write_json(
            receipt,
            {
                "schema": EVALUATOR.PAIR_TERMINAL_RECEIPT_SCHEMA,
                "status": "COMPLETE",
                "seed": seed,
                "arm_completion_receipts": [
                    _binding(pointer_paths[(seed, arm)]) for arm in ("small", "large")
                ],
                "checks": {
                    "paired_seed_exact": True,
                    "execution_order_exact": True,
                    "both_validation_only": True,
                    "both_test_access_zero": True,
                    "both_complete": True,
                },
            },
        )
        pair_paths[seed] = receipt
    final_manifest_path = controller_root / "FINAL_ARTIFACT_MANIFEST.json"
    indexed_files = sorted(
        [path for path in controller_root.rglob("*") if path.is_file()],
        key=lambda path: path.relative_to(controller_root).as_posix(),
    )
    _write_json(
        final_manifest_path,
        {
            "schema": EVALUATOR.FINAL_ARTIFACT_MANIFEST_SCHEMA,
            "root": str(controller_root),
            "artifacts": [
                _artifact_record(path, controller_root) for path in indexed_files
            ],
            "excluded_paths": [
                "controller.lock",
                "SHA256SUMS.txt",
                "FINAL_SHA256SUMS.txt",
                "FINAL_ARTIFACT_MANIFEST.json",
                "receipts/COMPLETE_RECEIPT.json",
            ],
            "all_other_regular_outputs_indexed": True,
        },
    )
    terminal_manifest = controller_root / "receipts" / "COMPLETE_RECEIPT.json"
    _write_json(
        terminal_manifest,
        {
            "schema": EVALUATOR.LEGACY_FIXTURE_SIX_ARM_TERMINAL_SCHEMA,
            "status": "READY_FOR_ONE_TIME_COMMON_TEST_EVALUATION",
            "run_contract": _binding(run_contract),
            "pairs": [
                _binding(pair_paths[seed]) for seed in EVALUATOR.EXACT_PAIRED_SEEDS
            ],
            "final_artifact_manifest": _binding(final_manifest_path),
            "exact_paired_seeds": list(EVALUATOR.EXACT_PAIRED_SEEDS),
            "evaluation_mode": "validation_only",
            "test_access_event_count": 0,
            "one_time_common_test_evaluation_performed": False,
            "fresh_emx_accessed": False,
            "numerical_metrics_released": False,
            "next_legal_gate": "INDEPENDENT_QA_FOR_ONE_TIME_COMMON_TEST_EVALUATOR",
        },
    )
    final_index = _write_runner_final_index(controller_root)
    out_dir = tmp_path / "evaluation"
    common_args = [
        "--preregistration-addendum",
        str(PREREGISTRATION_ADDENDUM),
        "--expected-preregistration-addendum-sha256",
        _sha(PREREGISTRATION_ADDENDUM),
        "--materialization-summary",
        str(material),
        "--expected-materialization-summary-sha256",
        _sha(material),
        "--common-holdout",
        str(holdout),
        "--expected-common-holdout-sha256",
        holdout_sha,
        "--fixed-normalization",
        str(normalization),
        "--expected-fixed-normalization-sha256",
        normalization_sha,
        "--six-arm-terminal-manifest",
        str(terminal_manifest),
        "--expected-six-arm-terminal-manifest-sha256",
        _sha(terminal_manifest),
        "--fixed-targets-json",
        str(targets),
        "--expected-fixed-targets-sha256",
        _sha(targets),
        "--trainer-source",
        str(TRAINER),
        "--expected-trainer-sha256",
        _sha(TRAINER),
        "--out-dir",
        str(out_dir),
        "--fixture-mode",
        "--expected-common-test-rows",
        "2",
        "--expected-fixed-rows",
        "4",
        "--expected-legacy-rows",
        "2",
        "--expected-extension-rows",
        "2",
        "--bootstrap-replicates",
        "12",
    ]
    return {
        "root": root,
        "out": out_dir,
        "args": common_args,
        "terminal": terminal_manifest,
        "controller_root": controller_root,
        "run_contract": run_contract,
        "pairs": pair_paths,
        "pointers": pointer_paths,
        "attempts": attempt_paths,
        "summaries": summary_paths,
        "weights": weights_paths,
        "final_index": final_index,
        "holdout": holdout,
        "normalization": normalization,
        "targets": targets,
    }


def _write_go(fixture: dict[str, Any], *, expires_delta: timedelta = timedelta(hours=1)) -> Path:
    out = fixture["out"]
    manifest = json.loads((out / EVALUATOR.MANIFEST_NAME).read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    def utc_z(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    go = fixture["root"] / f"go_{len(list(fixture['root'].glob('go_*.json')))}.json"
    _write_json(
        go,
        {
            "schema": EVALUATOR.GO_SCHEMA,
            "status": "PASS",
            "verdict": "EXACT_GO",
            "scope": EVALUATOR.GO_SCOPE,
            "nonce": manifest["qa_challenge_nonce"],
            "issued_utc": utc_z(now - timedelta(minutes=1)),
            "expires_utc": utc_z(now + expires_delta),
            "reviewer": {
                "role": "independent_qa",
                "identity": "synthetic-fixture-independent-reviewer",
                "independent_of_builder_and_execution": True,
                "result_blind": True,
            },
            "findings": {"p0": 0, "p1": 0},
            "checks": dict(EVALUATOR.GO_CHECKS),
            "bindings": {
                "prepared_receipt": {
                    "path": str(out / EVALUATOR.PREPARED_NAME),
                    "sha256": _sha(out / EVALUATOR.PREPARED_NAME),
                },
                "evaluation_manifest": {
                    "path": str(out / EVALUATOR.MANIFEST_NAME),
                    "sha256": _sha(out / EVALUATOR.MANIFEST_NAME),
                },
                "independent_qa_required": {
                    "path": str(out / EVALUATOR.QA_REQUIRED_NAME),
                    "sha256": _sha(out / EVALUATOR.QA_REQUIRED_NAME),
                },
                "prepare_sha_index": {
                    "path": str(out / EVALUATOR.PREPARE_INDEX_NAME),
                    "sha256": _sha(out / EVALUATOR.PREPARE_INDEX_NAME),
                },
                "release_contract_sha256": manifest["release_contract"]["release_contract_sha256"],
                "release_bindings": manifest["release_contract"]["release_bindings"],
            },
            "authorities": {
                "one_time_common_test_release": True,
                "common_real_emx_holdout_evaluation": True,
                "fixed10k_own_forward_proxy_evaluation": True,
                "fresh_emx": False,
                "data_generation": False,
                "training": False,
                "process_signal": False,
                "retry_after_claim": False,
                "fixture_only": True,
            },
        },
    )
    return go


def _execute_args(fixture: dict[str, Any], go: Path) -> list[str]:
    return [
        "--phase",
        "execute",
        *fixture["args"],
        "--independent-qa-go-receipt",
        str(go),
        "--expected-independent-qa-go-receipt-sha256",
        _sha(go),
    ]


def test_metric_contract_q_guardrail_k_policy_and_percentiles() -> None:
    target = np.asarray([[1.0, 1.0, 10.0, 0.0], [2.0, 2.0, 20.0, 0.5]])
    predicted = np.asarray([[1.5, 0.5, 8.0, 0.1], [1.0, 3.0, 25.0, 0.7]])
    metrics = EVALUATOR._response_metrics(
        target,
        predicted,
        declared_spans=np.asarray([2.5, 2.5, 20.0, 0.8]),
        evidence_class="fixture",
        requested_rows=2,
    )
    assert metrics["per_feature"]["Lp_nH"]["MAE"] == pytest.approx(0.75)
    assert metrics["per_feature"]["K_abs"]["P95"] == pytest.approx(0.195)
    assert metrics["Q_guardrail"]["target_met_rate"] == pytest.approx(0.5)
    assert metrics["Q_guardrail"]["shortfall_MAE"] == pytest.approx(1.0)
    assert metrics["K_policy"]["target_relative_APE_reported_as_primary"] is False
    assert metrics["denominator"] == {
        "requested_rows": 2,
        "evaluated_rows": 2,
        "failed_rows": 0,
        "finite_rows": 2,
    }


def test_physical_cell_high_k_cap_is_clustering_only_and_seed_is_exact() -> None:
    assert len(EVALUATOR.COMMON_CSV_FIELDS) == len(set(EVALUATOR.COMMON_CSV_FIELDS))
    assert len(EVALUATOR.FIXED_CSV_FIELDS) == len(set(EVALUATOR.FIXED_CSV_FIELDS))
    assert EVALUATOR.FIXED_CSV_FIELDS.count("target_id") == 1
    values = np.asarray(
        [[1.0, 2.0, 10.0, 0.7], [2.5, 1.5, 20.0, 0.95]], dtype=np.float64
    )
    original = values.copy()
    cell_ids, capped = EVALUATOR._physical_cell_ids(
        values, cap_high_k_for_clustering_only=True
    )
    assert np.array_equal(values, original)
    assert capped.tolist() == [False, True]
    assert cell_ids[0] == EVALUATOR.canonical_physical_cell_id(original[0])
    clipped = original[1].copy()
    clipped[3] = 0.8
    assert cell_ids[1] == EVALUATOR.canonical_physical_cell_id(clipped)
    for frame_id in EVALUATOR.SPATIAL_FRAME_IDS:
        expected = int.from_bytes(
            hashlib.sha256(
                f"{EVALUATOR.SPATIAL_BOOTSTRAP_MASTER_SEED}:{frame_id}".encode(
                    "utf-8"
                )
            ).digest()[:8],
            "big",
        )
        assert EVALUATOR._spatial_frame_seed(frame_id) == expected


def test_weighted_bootstrap_scalars_match_explicit_integer_expansion() -> None:
    response_target = np.asarray(
        [
            [0.5, 1.0, 5.0, 0.0],
            [1.0, 1.5, 10.0, 0.2],
            [2.0, 2.5, 20.0, 0.6],
            [3.0, 3.0, 25.0, 0.8],
        ],
        dtype=np.float64,
    )
    response_prediction = np.stack(
        [
            response_target
            + (model + 1) * np.asarray([0.01, -0.02, 0.03, -0.004])[None, :]
            + np.arange(4)[:, None] * np.asarray([0.02, 0.01, -0.04, 0.003])[None, :]
            for model in range(6)
        ],
        axis=0,
    )
    row_weights = np.asarray([[1, 0, 2, 1], [0, 3, 1, 0]], dtype=np.uint16)
    spans = np.asarray(EVALUATOR.INPUT_UPPER) - np.asarray(EVALUATOR.INPUT_LOWER)
    response_scalars = EVALUATOR._response_bootstrap_scalar_matrices(
        response_target,
        response_prediction,
        declared_spans=spans,
        row_weights=row_weights,
    )
    for replicate in range(row_weights.shape[0]):
        expanded = np.repeat(np.arange(response_target.shape[0]), row_weights[replicate])
        for model in range(6):
            expected = EVALUATOR._metric_scalars(
                EVALUATOR._response_metrics(
                    response_target[expanded],
                    response_prediction[model, expanded],
                    declared_spans=spans,
                    evidence_class="fixture",
                    requested_rows=len(expanded),
                )
            )
            assert set(response_scalars) == set(expected)
            for metric, value in expected.items():
                assert response_scalars[metric][replicate, model] == pytest.approx(
                    value, abs=1e-14
                )

    geometry_target = np.linspace(170.0, 260.0, 40).reshape(4, 10)
    geometry_prediction = np.stack(
        [geometry_target + (model + 1) * 0.1 + np.arange(4)[:, None] * 0.03 for model in range(6)],
        axis=0,
    )
    geometry_scalars = EVALUATOR._geometry_bootstrap_scalar_matrices(
        geometry_target, geometry_prediction, row_weights=row_weights
    )
    for replicate in range(row_weights.shape[0]):
        expanded = np.repeat(np.arange(geometry_target.shape[0]), row_weights[replicate])
        for model in range(6):
            expected = EVALUATOR._metric_scalars(
                EVALUATOR._geometry_metrics(
                    geometry_target[expanded],
                    geometry_prediction[model, expanded],
                    requested_rows=len(expanded),
                )
            )
            assert set(geometry_scalars) == set(expected)
            for metric, value in expected.items():
                assert geometry_scalars[metric][replicate, model] == pytest.approx(
                    value, abs=1e-14
                )


def test_unequal_cell_cluster_plan_and_nonzero_paired_delta_match_expansion() -> None:
    target = np.asarray(
        [
            [0.75, 0.75, 7.0, 0.1],
            [0.80, 0.80, 8.0, 0.15],
            [0.85, 0.85, 9.0, 0.19],
            [2.5, 2.5, 22.0, 0.7],
        ],
        dtype=np.float64,
    )
    cell_ids, capped = EVALUATOR._physical_cell_ids(
        target, cap_high_k_for_clustering_only=False
    )
    assert not np.any(capped)
    assert sorted(np.bincount([0 if value == cell_ids[0] else 1 for value in cell_ids])) == [1, 3]
    plan = EVALUATOR._cluster_resampling_plan(
        cell_ids,
        frame_id="common_real_emx_holdout_902",
        replicates=9,
    )
    predictions = np.stack(
        [
            target
            + np.asarray(
                [
                    0.01 * model,
                    -0.005 * model,
                    0.04 * model,
                    0.002 * model,
                ]
            )[None, :]
            + np.arange(4)[:, None] * np.asarray([0.003, 0.002, -0.01, 0.001])[None, :]
            for model in range(6)
        ],
        axis=0,
    )
    spans = np.asarray(EVALUATOR.INPUT_UPPER) - np.asarray(EVALUATOR.INPUT_LOWER)
    scalars = EVALUATOR._response_bootstrap_scalar_matrices(
        target,
        predictions,
        declared_spans=spans,
        row_weights=plan["row_weights"],
    )
    paired = EVALUATOR._bootstrap_paired_mean_deltas(scalars)
    metric = "per_feature.Lp_nH.P95"
    explicit: list[float] = []
    for replicate, weights in enumerate(plan["row_weights"]):
        expanded = np.repeat(np.arange(target.shape[0]), weights)
        model_values: list[float] = []
        for model in range(6):
            model_values.append(
                EVALUATOR._metric_scalars(
                    EVALUATOR._response_metrics(
                        target[expanded],
                        predictions[model, expanded],
                        declared_spans=spans,
                        evidence_class="fixture",
                        requested_rows=len(expanded),
                    )
                )[metric]
            )
        explicit.append(
            np.mean(
                [
                    model_values[1] - model_values[0],
                    model_values[3] - model_values[2],
                    model_values[5] - model_values[4],
                ]
            )
        )
        assert paired[metric][replicate] == pytest.approx(explicit[-1], abs=1e-14)
    assert any(abs(value) > 0.0 for value in explicit)


def test_paired_statistics_uses_large_minus_small_sample_sd_and_df2_t95() -> None:
    records: dict[str, dict[str, Any]] = {}
    for index, seed in enumerate(EVALUATOR.EXACT_PAIRED_SEEDS):
        records[EVALUATOR._model_key(seed, "small")] = {
            "status": "PASS",
            "metrics": {"per_feature": {"Lp": {"MAE": 5.0}}},
        }
        records[EVALUATOR._model_key(seed, "large")] = {
            "status": "PASS",
            "metrics": {"per_feature": {"Lp": {"MAE": 4.0 + index}}},
        }
    result = EVALUATOR._paired_statistics(records, "fixture")
    metric = result["metric_summaries"]["per_feature.Lp.MAE"]
    assert metric["paired_deltas"] == pytest.approx([-1.0, 0.0, 1.0])
    assert metric["mean_paired_delta"] == pytest.approx(0.0)
    assert metric["sample_SD"] == pytest.approx(1.0)
    assert metric["degrees_of_freedom"] == 2
    assert metric["two_sided_t95_CI"] == pytest.approx(
        [-EVALUATOR.T95_DF2 / np.sqrt(3.0), EVALUATOR.T95_DF2 / np.sqrt(3.0)]
    )


def test_prepare_binds_actual_imported_shared_contract_and_full_runtime(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    manifest = json.loads(
        (fixture["out"] / EVALUATOR.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    bindings = manifest["release_contract"]["release_bindings"]
    shared = bindings["shared_scientific_contract"]
    assert shared == bindings["consumed_inputs"]["shared_scientific_contract"]
    assert shared["path"] == str(SHARED_CONTRACT)
    assert shared["sha256"] == _sha(SHARED_CONTRACT)
    run_contract = json.loads(fixture["run_contract"].read_text(encoding="utf-8"))
    assert run_contract["shared_contract"]["path"] == shared["path"]
    assert run_contract["shared_contract"]["sha256"] == shared["sha256"]
    runtime = bindings["numerical_runtime"]
    assert set(runtime) == {
        "python_version",
        "python_implementation",
        "numpy_version",
        "numpy_show_config_sha256",
        "files",
    }
    assert set(runtime["files"]) == {
        "python_executable",
        "numpy_core",
        "numpy_config",
    }
    for role, identity in runtime["files"].items():
        assert identity == bindings["consumed_inputs"][f"runtime__{role}"]
        assert identity["nlink"] == 1
        assert len(identity["sha256"]) == 64
        assert Path(identity["path"]).is_absolute()


def test_evaluator_freezes_runner_v5_and_exact_isolated_launch_contract() -> None:
    assert _sha(RUNNER) == EVALUATOR.FROZEN_PAIRED_RUNNER_SHA256
    launch = EVALUATOR._audit_trainer_launch_contract(
        EVALUATOR.FROZEN_TRAINER_LAUNCH_CONTRACT, "fixture trainer launch"
    )
    assert launch["python_isolation_flags"] == ["-I", "-B", "-S"]
    assert launch["parent_environment_inherited"] is False
    assert launch["python_prefixed_environment_keys"] == []
    assert launch["effective_environment_sha256"] == EVALUATOR._canonical_sha(
        {
            "schema": "controlled_real10k_20k_exact_child_environment_v2",
            "environment": launch["effective_environment"],
        }
    )


@pytest.mark.parametrize(
    "mutation",
    ["flags", "parent_inherited", "python_key", "environment_sha"],
)
def test_evaluator_rejects_every_isolated_launch_contract_drift(
    mutation: str,
) -> None:
    launch = json.loads(json.dumps(EVALUATOR.FROZEN_TRAINER_LAUNCH_CONTRACT))
    if mutation == "flags":
        launch["python_isolation_flags"] = ["-B", "-I"]
    elif mutation == "parent_inherited":
        launch["parent_environment_inherited"] = True
    elif mutation == "python_key":
        launch["effective_environment"]["PYTHONHASHSEED"] = "0"
    elif mutation == "environment_sha":
        launch["effective_environment_sha256"] = "0" * 64
    else:  # pragma: no cover - exact parametrization
        raise AssertionError(mutation)
    with pytest.raises(EVALUATOR.EvaluationError, match="exact JSON"):
        EVALUATOR._audit_trainer_launch_contract(launch, "fixture trainer launch")


def test_prepare_rejects_paired_run_shared_binding_not_actual_imported_bytes(
    tmp_path: Path,
) -> None:
    alternate = tmp_path / "alternate_shared_contract.py"
    alternate.write_bytes(SHARED_CONTRACT.read_bytes())
    fixture = _make_fixture(
        tmp_path,
        shared_binding_override=_binding(alternate),
    )
    with pytest.raises(
        EVALUATOR.EvaluationError,
        match="differs from actual imported bytes",
    ):
        EVALUATOR.main(["--phase", "prepare", *fixture["args"]])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_prepare_rejects_runtime_or_shared_identity_drift_from_actual_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    drifted = dict(EVALUATOR._MODULE_LOAD_RUNTIME_SCALARS)
    drifted["numpy_version"] = "drifted-runtime"
    monkeypatch.setattr(EVALUATOR, "_MODULE_LOAD_RUNTIME_SCALARS", drifted)
    with pytest.raises(EVALUATOR.EvaluationError, match="changed after module import"):
        EVALUATOR.main(["--phase", "prepare", *fixture["args"]])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_execute_rejects_running_evaluator_not_identical_to_prepared_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    drifted = dict(EVALUATOR._MODULE_LOAD_EVALUATOR_IDENTITY)
    drifted["sha256"] = "0" * 64
    monkeypatch.setattr(EVALUATOR, "_MODULE_LOAD_EVALUATOR_IDENTITY", drifted)
    with pytest.raises(EVALUATOR.EvaluationError, match="executing evaluator code differs"):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("extra_top_level", "keyset"),
        ("wrong_nonce", "semantic gate"),
        ("nonzero_findings", "zero P0/P1"),
        ("inexact_checks", "check keyset"),
        ("expanded_authority", "authority scope"),
        ("wrong_qa_required_sha", "exact bindings mismatch"),
        ("noncanonical_timestamp", "UTC-Z seconds"),
    ],
)
def test_execute_exact_go_rejects_every_schema_or_authority_drift_before_claim(
    tmp_path: Path, mutation: str, match: str
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    payload = json.loads(go.read_text(encoding="utf-8"))
    if mutation == "extra_top_level":
        payload["extra"] = False
    elif mutation == "wrong_nonce":
        payload["nonce"] = "0" * 32
    elif mutation == "nonzero_findings":
        payload["findings"]["p1"] = 1
    elif mutation == "inexact_checks":
        payload["checks"].pop("numerical_runtime_exact")
    elif mutation == "expanded_authority":
        payload["authorities"]["training"] = True
    elif mutation == "wrong_qa_required_sha":
        payload["bindings"]["independent_qa_required"]["sha256"] = "0" * 64
    elif mutation == "noncanonical_timestamp":
        payload["issued_utc"] = payload["issued_utc"].replace("Z", "+00:00")
    else:  # pragma: no cover - parametrization is exact
        raise AssertionError(mutation)
    _write_json(go, payload)
    with pytest.raises(EVALUATOR.EvaluationError, match=match):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


@pytest.mark.parametrize("hostile_kind", ["symlink", "hardlink", "unsafe_mode"])
def test_prepare_rejects_symlink_hardlink_and_unsafe_input_mode(
    tmp_path: Path, hostile_kind: str
) -> None:
    fixture = _make_fixture(tmp_path)
    holdout = fixture["holdout"]
    args = list(fixture["args"])
    if hostile_kind == "symlink":
        hostile = tmp_path / "holdout_symlink.json"
        hostile.symlink_to(holdout)
        args[args.index("--common-holdout") + 1] = str(hostile)
    elif hostile_kind == "hardlink":
        os.link(holdout, tmp_path / "holdout_hardlink.json")
    else:
        holdout.chmod(0o666)
    with pytest.raises(EVALUATOR.EvaluationError):
        EVALUATOR.main(["--phase", "prepare", *args])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_execute_rejects_preopen_inode_replacement_before_claim(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    csv_path = fixture["root"] / "arm_source_n10000.csv"
    replacement = fixture["root"] / "replacement.csv"
    replacement.write_bytes(csv_path.read_bytes())
    os.replace(replacement, csv_path)
    with pytest.raises(EVALUATOR.EvaluationError, match="descriptor identity changed"):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_output_root_inode_lease_rejects_same_lexical_replacement_and_second_claim(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    out = fixture["out"]
    original = tmp_path / "held-original-output-root"
    pristine = tmp_path / "pristine-prepared-copy"
    shutil.copytree(out, pristine)
    out.rename(original)
    shutil.copytree(original, out)
    go = _write_go(fixture)
    with pytest.raises(EVALUATOR.EvaluationError, match="output-root identity"):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (out / EVALUATOR.CLAIM_NAME).exists()

    shutil.rmtree(out)
    original.rename(out)
    assert EVALUATOR.main(_execute_args(fixture, go)) == 0
    manifest = json.loads((out / EVALUATOR.MANIFEST_NAME).read_text(encoding="utf-8"))
    lease = manifest["release_contract"]["release_bindings"][
        "one_time_release_lease"
    ]
    lease_path = Path(lease["path"])
    root_identity = manifest["release_contract"]["release_bindings"][
        "evaluation_output_root_identity"
    ]

    # A normal owner can chmod/remove the visible closure, so the security
    # boundary is the GO-bound inode/state, not the advisory 0555/0444 modes.
    out.chmod(0o755)
    for path in list(out.iterdir()):
        if path.name not in EVALUATOR.PREPARE_FILE_NAMES:
            path.unlink()
    lease_path.chmod(0o600)
    lease_path.write_bytes(
        EVALUATOR._lease_state_bytes(
            "PREPARED", manifest["qa_challenge_nonce"], root_identity
        )
    )
    with pytest.raises(
        EVALUATOR.EvaluationError,
        match="one-time release lease identity|one-time release lease",
    ):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (out / EVALUATOR.CLAIM_NAME).exists()


def test_go_is_single_open_held_bytes_and_path_replacement_fails_before_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    original_bytes = go.read_bytes()
    replacement = fixture["root"] / "replacement_go.json"
    payload = json.loads(original_bytes.decode("utf-8"))
    payload["reviewer"]["identity"] = "hostile-substituted-reviewer"
    _write_json(replacement, payload)
    original_json_from_bytes = EVALUATOR._json_from_bytes
    original_open = EVALUATOR.os.open
    go_open_count = 0
    replaced = False

    def counting_open(path: Any, *args: Any, **kwargs: Any) -> int:
        nonlocal go_open_count
        if os.fspath(path) == str(go):
            go_open_count += 1
        return original_open(path, *args, **kwargs)

    def replace_between_read_and_parse(raw: bytes, label: str) -> dict[str, Any]:
        nonlocal replaced
        if label == "independent exact-GO receipt" and not replaced:
            assert raw == original_bytes
            os.replace(replacement, go)
            replaced = True
        return original_json_from_bytes(raw, label)

    monkeypatch.setattr(EVALUATOR.os, "open", counting_open)
    monkeypatch.setattr(EVALUATOR, "_json_from_bytes", replace_between_read_and_parse)
    with pytest.raises(
        EVALUATOR.EvaluationError,
        match="GO (descriptor identity changed|pathname was replaced)",
    ):
        EVALUATOR.main(_execute_args(fixture, go))
    assert replaced is True
    assert go_open_count == 1
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


@pytest.mark.parametrize(
    "mutation",
    ["findings_bool_for_int", "check_int_for_bool", "authority_int_for_bool", "binding_bool_for_int"],
)
def test_go_recursive_json_types_are_exact_before_claim(
    tmp_path: Path, mutation: str
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    payload = json.loads(go.read_text(encoding="utf-8"))
    if mutation == "findings_bool_for_int":
        payload["findings"]["p0"] = False
    elif mutation == "check_int_for_bool":
        payload["checks"]["result_blind_independent_review"] = 1
    elif mutation == "authority_int_for_bool":
        payload["authorities"]["fresh_emx"] = 0
    elif mutation == "binding_bool_for_int":
        payload["bindings"]["release_bindings"]["models"][0]["seed"] = True
    else:  # pragma: no cover - exact parametrization
        raise AssertionError(mutation)
    _write_json(go, payload)
    with pytest.raises(EVALUATOR.EvaluationError, match="exact JSON type mismatch"):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_held_descriptor_detects_path_replacement_without_reopening_hostile_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    csv_path = fixture["root"] / "arm_source_n10000.csv"
    hostile = fixture["root"] / "hostile_replacement.csv"
    hostile.write_text("hostile bytes must never be consumed\n", encoding="utf-8")
    original_verify = EVALUATOR._verify_paired_shared_contract_snapshot
    replaced = False

    def replace_after_all_descriptors_open(*args: Any, **kwargs: Any) -> None:
        nonlocal replaced
        if not replaced:
            os.replace(hostile, csv_path)
            replaced = True
        original_verify(*args, **kwargs)

    monkeypatch.setattr(
        EVALUATOR,
        "_verify_paired_shared_contract_snapshot",
        replace_after_all_descriptors_open,
    )
    with pytest.raises(EVALUATOR.EvaluationError, match="held-byte identity mismatch"):
        EVALUATOR.main(_execute_args(fixture, go))
    assert replaced is True
    assert (fixture["out"] / EVALUATOR.CLAIM_NAME).is_file()
    assert (fixture["out"] / EVALUATOR.FATAL_FAIL_NAME).is_file()
    assert not (fixture["out"] / EVALUATOR.COMMON_ROWS_NAME).exists()


def test_claim_is_file_directory_parent_durable_before_protected_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    durable_events = 0
    original_durable = EVALUATOR._durable_held_directories
    original_snapshot = EVALUATOR._snapshot_held_input

    def record_durable(root_descriptor: int, parent_descriptor: int) -> None:
        nonlocal durable_events
        original_durable(root_descriptor, parent_descriptor)
        durable_events += 1

    def assert_claim_first(handle: Any, record: Any, role: str) -> bytes:
        if role in {"common_source_csv", "common_holdout", "fixed_targets"}:
            assert (fixture["out"] / EVALUATOR.CLAIM_NAME).is_file()
            assert durable_events >= 1  # claim file/root/parent completed as one event
        return original_snapshot(handle, record, role)

    monkeypatch.setattr(EVALUATOR, "_durable_held_directories", record_durable)
    monkeypatch.setattr(EVALUATOR, "_snapshot_held_input", assert_claim_first)
    assert EVALUATOR.main(_execute_args(fixture, go)) == 0
    claim = json.loads(
        (fixture["out"] / EVALUATOR.CLAIM_NAME).read_text(encoding="utf-8")
    )
    assert claim["durability"] == {
        "claim_file_fsync": True,
        "claim_directory_fsync": True,
        "claim_parent_directory_fsync": True,
    }
    assert claim["recovery_boundary"]["claim_present"].startswith(
        "RELEASE_CONSUMED_IRREVERSIBLY"
    )


def test_final_output_rejects_extra_file_and_retains_irreversible_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    original_write_index = EVALUATOR._write_index_at_x

    def inject_extra_after_result_index(
        root_descriptor: int,
        parent_descriptor: int,
        name: str,
        names: Any,
    ) -> None:
        original_write_index(root_descriptor, parent_descriptor, name, names)
        if name == EVALUATOR.RESULT_INDEX_NAME:
            descriptor = os.open(
                "UNDECLARED_EXTRA",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
                dir_fd=root_descriptor,
            )
            try:
                os.write(descriptor, b"must fail\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    monkeypatch.setattr(EVALUATOR, "_write_index_at_x", inject_extra_after_result_index)
    with pytest.raises(EVALUATOR.EvaluationError, match="filesystem closure differs"):
        EVALUATOR.main(_execute_args(fixture, go))
    out = fixture["out"]
    assert (out / EVALUATOR.CLAIM_NAME).is_file()
    fatal = json.loads(
        (out / EVALUATOR.FATAL_FAIL_NAME).read_text(encoding="utf-8")
    )
    assert fatal["status"] == "FAIL_IRREVERSIBLE_TEST_RELEASE_CONSUMED"
    assert fatal["retry_authorized"] is False
    assert (out.stat().st_mode & 0o777) == EVALUATOR.FINAL_DIRECTORY_MODE
    failure_names = {path.name for path in out.iterdir()}
    assert EVALUATOR.FAILURE_INDEX_NAME in failure_names
    EVALUATOR._verify_index(
        out / EVALUATOR.FAILURE_INDEX_NAME,
        out,
        failure_names - {EVALUATOR.FAILURE_INDEX_NAME},
    )
    assert all(
        (path.stat().st_mode & 0o777) == EVALUATOR.FINAL_FILE_MODE
        and path.stat().st_nlink == 1
        for path in out.iterdir()
    )


def test_prepare_is_result_blind_and_requires_fresh_exact_go(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    out = fixture["out"]
    assert (out / EVALUATOR.PREPARED_NAME).is_file()
    assert (out / EVALUATOR.QA_REQUIRED_NAME).is_file()
    assert not (out / EVALUATOR.CLAIM_NAME).exists()
    assert not (out / EVALUATOR.COMMON_ROWS_NAME).exists()
    with pytest.raises(SystemExit):
        EVALUATOR.main(["--phase", "execute", *fixture["args"]])
    assert not (out / EVALUATOR.CLAIM_NAME).exists()

    stale = _write_go(fixture, expires_delta=timedelta(minutes=-1))
    with pytest.raises(EVALUATOR.EvaluationError, match="stale"):
        EVALUATOR.main(
            [
                "--phase",
                "execute",
                *fixture["args"],
                "--independent-qa-go-receipt",
                str(stale),
                "--expected-independent-qa-go-receipt-sha256",
                _sha(stale),
            ]
        )
    assert not (out / EVALUATOR.CLAIM_NAME).exists()


def test_execute_one_time_release_metrics_sha_closure_and_no_retry(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    execute_args = [
        "--phase",
        "execute",
        *fixture["args"],
        "--independent-qa-go-receipt",
        str(go),
        "--expected-independent-qa-go-receipt-sha256",
        _sha(go),
    ]
    assert EVALUATOR.main(execute_args) == 0
    out = fixture["out"]
    summary = json.loads((out / EVALUATOR.SUMMARY_NAME).read_text(encoding="utf-8"))
    assert summary["status"] == "PASS_SYNTHETIC_FIXTURE_ONLY_NOT_RESEARCH"
    assert summary["eligible_for_research_conclusion"] is False
    assert summary["release"]["test_access_event_count_this_evaluator"] == 1
    assert summary["release"]["fresh_emx_generated"] is False
    assert summary["denominators"]["model_arms_evaluated_pass"] == 6
    assert summary["denominators"]["paired_seeds_complete"] == 3
    assert summary["paired_effects"]["common_forward_primary"]["formal_three_pair_effect_complete"] is True
    delta_metrics = summary["paired_effects"]["common_forward_primary"]["metric_summaries"]
    assert delta_metrics["per_feature.Lp_nH.MAE"]["paired_deltas"] == [0.0, 0.0, 0.0]
    assert summary["metric_contract"]["K_target_relative_APE_primary"] is False
    spatial = summary["spatial_sensitivity"]
    assert spatial["status"] == "PASS_FINITE_FRAME_SPATIAL_COMPOSITION_SENSITIVITY"
    assert spatial["preregistration_addendum_sha256"] == EVALUATOR.PREREGISTRATION_ADDENDUM_SHA256
    assert spatial["bootstrap_replicates"] == 12
    assert spatial["frames_complete"] == 4
    assert set(spatial["frames"]) == set(EVALUATOR.SPATIAL_FRAME_IDS)
    for frame_id, frame in spatial["frames"].items():
        assert frame["status"] == "PASS"
        assert frame["frame_seed"] == EVALUATOR._spatial_frame_seed(frame_id)
        assert frame["bootstrap_replicates_complete"] == 12
        for estimand in frame["estimands"].values():
            assert estimand["paired_seed_denominator_complete"] == 3
            for metric in estimand["metric_summaries"].values():
                assert metric["bootstrap_percentile_95_interval"] == pytest.approx(
                    [0.0, 0.0], abs=0.0
                )
                assert metric["bootstrap_replicates_finite"] == 12
    with (out / EVALUATOR.COMMON_ROWS_NAME).open(newline="", encoding="utf-8") as handle:
        common_rows = list(csv.DictReader(handle))
    with (out / EVALUATOR.FIXED_ROWS_NAME).open(newline="", encoding="utf-8") as handle:
        fixed_rows = list(csv.DictReader(handle))
    assert len(common_rows) == 6 * 2
    assert len(fixed_rows) == 6 * 4
    assert all(row["physical_cell_4d"] for row in common_rows + fixed_rows)
    assert {
        row["K_abs_capped_to_0p8_for_clustering_only"] for row in common_rows
    } == {"False"}
    assert {
        (row["target_id"], row["K_abs_capped_to_0p8_for_clustering_only"])
        for row in fixed_rows
    } == {
        ("legacy-a", "False"),
        ("legacy-b", "False"),
        ("high-a", "True"),
        ("high-b", "True"),
    }
    assert {
        (row["target_id"], float(row["target__K_abs"])) for row in fixed_rows
    } >= {("high-a", 0.9), ("high-b", 0.95)}
    EVALUATOR._verify_index(
        out / EVALUATOR.RESULT_INDEX_NAME,
        out,
        EVALUATOR.FINAL_FILE_NAMES - {EVALUATOR.RESULT_INDEX_NAME},
    )
    assert {path.name for path in out.iterdir()} == EVALUATOR.FINAL_FILE_NAMES
    assert (out.stat().st_mode & 0o777) == EVALUATOR.FINAL_DIRECTORY_MODE
    assert all(
        (path.stat().st_mode & 0o777) == EVALUATOR.FINAL_FILE_MODE
        and path.stat().st_nlink == 1
        for path in out.iterdir()
    )
    with pytest.raises(EVALUATOR.EvaluationError, match="untouched prepared directory"):
        EVALUATOR.main(execute_args)


def test_prepare_rejects_any_pre_release_test_access_and_pair_sha_mismatch(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    terminal_path = fixture["terminal"]
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["test_access_event_count"] = 1
    _write_json(terminal_path, terminal)
    args = list(fixture["args"])
    index = args.index("--expected-six-arm-terminal-manifest-sha256") + 1
    args[index] = _sha(terminal_path)
    with pytest.raises(EVALUATOR.EvaluationError, match="status/test.seal"):
        EVALUATOR.main(["--phase", "prepare", *args])
    fail = json.loads((fixture["out"] / "PREPARE_FAIL.json").read_text(encoding="utf-8"))
    assert fail["test_rows_read"] is False


def test_prepare_rejects_pair_receipt_sha_tamper(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    pair = fixture["pairs"][EVALUATOR.EXACT_PAIRED_SEEDS[0]]
    payload = json.loads(pair.read_text(encoding="utf-8"))
    payload["checks"]["both_test_access_zero"] = False
    _write_json(pair, payload)
    with pytest.raises(EVALUATOR.EvaluationError, match="SHA mismatch"):
        EVALUATOR.main(["--phase", "prepare", *fixture["args"]])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


@pytest.mark.parametrize("level", ["pointer", "attempt", "summary", "weights"])
def test_prepare_rejects_controller_pair_arm_model_chain_tamper(
    tmp_path: Path, level: str
) -> None:
    fixture = _make_fixture(tmp_path)
    identity = (EVALUATOR.EXACT_PAIRED_SEEDS[0], "small")
    path = fixture[
        {
            "pointer": "pointers",
            "attempt": "attempts",
            "summary": "summaries",
            "weights": "weights",
        }[level]
    ][identity]
    if level == "weights":
        path.write_bytes(path.read_bytes() + b"tamper")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload[f"tampered_{level}"] = True
        _write_json(path, payload)
    with pytest.raises(EVALUATOR.EvaluationError):
        EVALUATOR.main(["--phase", "prepare", *fixture["args"]])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


@pytest.mark.parametrize(
    "field,wrong_field",
    [
        ("command", "intent"),
        ("intent", "running"),
        ("running", "intent"),
        ("stdout", "stderr"),
        ("stderr", "stdout"),
        ("output_manifest", "intent"),
    ],
)
def test_arm_attempt_artifact_binding_requires_canonical_runner_path(
    tmp_path: Path, field: str, wrong_field: str
) -> None:
    fixture = _make_fixture(tmp_path)
    seed, arm = EVALUATOR.EXACT_PAIRED_SEEDS[0], "small"
    identity = (seed, arm)
    attempt = fixture["attempts"][identity]
    payload = json.loads(attempt.read_text(encoding="utf-8"))
    payload[field] = payload[wrong_field]
    _write_json(attempt, payload)
    pointer = fixture["pointers"][identity]
    pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
    pointer_payload["attempt_complete"] = _binding(attempt)
    _write_json(pointer, pointer_payload)
    normalization_payload = json.loads(
        fixture["normalization"].read_text(encoding="utf-8")
    )
    with pytest.raises(EVALUATOR.EvaluationError, match=f"{field} canonical path mismatch"):
        EVALUATOR._audit_arm_terminal(
            _binding(pointer),
            controller_root=fixture["controller_root"],
            run_contract_binding=_binding(fixture["run_contract"]),
            seed=seed,
            arm=arm,
            normalization_sha=_sha(fixture["normalization"]),
                holdout_sha=_sha(fixture["holdout"]),
                normalization=EVALUATOR._normalization_vectors(normalization_payload),
                paired_runtime=None,
                controlled_singleton=None,
                fixture_mode=True,
        )


def test_prepare_rejects_paired_final_sha_index_tamper(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    index = fixture["final_index"]
    lines = index.read_text(encoding="ascii").splitlines()
    lines[0] = "0" * 64 + lines[0][64:]
    index.write_text("\n".join(lines) + "\n", encoding="ascii")
    with pytest.raises(EVALUATOR.EvaluationError, match="paired final SHA index"):
        EVALUATOR.main(["--phase", "prepare", *fixture["args"]])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_prepare_rejects_incomplete_final_artifact_manifest_set(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    controller_root = fixture["controller_root"]
    manifest_path = controller_root / "FINAL_ARTIFACT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed = next(
        index
        for index, record in enumerate(manifest["artifacts"])
        if str(record["relative_path"]).startswith("commands/")
    )
    manifest["artifacts"].pop(removed)
    _write_json(manifest_path, manifest)
    terminal = fixture["terminal"]
    terminal_payload = json.loads(terminal.read_text(encoding="utf-8"))
    terminal_payload["final_artifact_manifest"] = _binding(manifest_path)
    _write_json(terminal, terminal_payload)
    _write_runner_final_index(controller_root)
    args = list(fixture["args"])
    terminal_sha_index = args.index("--expected-six-arm-terminal-manifest-sha256") + 1
    args[terminal_sha_index] = _sha(terminal)
    with pytest.raises(EVALUATOR.EvaluationError, match="exact lexical nonexcluded file set"):
        EVALUATOR.main(["--phase", "prepare", *args])
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_execute_rejects_exact_go_binding_tamper_before_claim(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)
    payload = json.loads(go.read_text(encoding="utf-8"))
    payload["bindings"]["release_contract_sha256"] = "0" * 64
    _write_json(go, payload)
    with pytest.raises(EVALUATOR.EvaluationError, match="exact bindings mismatch"):
        EVALUATOR.main(
            [
                "--phase",
                "execute",
                *fixture["args"],
                "--independent-qa-go-receipt",
                str(go),
                "--expected-independent-qa-go-receipt-sha256",
                _sha(go),
            ]
        )
    assert not (fixture["out"] / EVALUATOR.CLAIM_NAME).exists()


def test_copied_prepared_package_cannot_create_second_release_lane(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    copied = tmp_path / "copied-evaluation"
    shutil.copytree(fixture["out"], copied)
    copied_args = list(fixture["args"])
    copied_args[copied_args.index("--out-dir") + 1] = str(copied)
    go = _write_go(fixture)
    with pytest.raises(EVALUATOR.EvaluationError, match="output-root identity"):
        EVALUATOR.main(
            [
                "--phase",
                "execute",
                *copied_args,
                "--independent-qa-go-receipt",
                str(go),
                "--expected-independent-qa-go-receipt-sha256",
                _sha(go),
            ]
        )
    assert not (copied / EVALUATOR.CLAIM_NAME).exists()


def test_post_claim_fatal_error_writes_irreversible_failure_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _make_fixture(tmp_path)
    assert EVALUATOR.main(["--phase", "prepare", *fixture["args"]]) == 0
    go = _write_go(fixture)

    def fail_test_load(*_args: Any, **_kwargs: Any):
        raise EVALUATOR.EvaluationError("fixture post-claim test-load failure")

    monkeypatch.setattr(EVALUATOR, "_load_common_test", fail_test_load)
    with pytest.raises(EVALUATOR.EvaluationError, match="post-claim"):
        EVALUATOR.main(
            [
                "--phase",
                "execute",
                *fixture["args"],
                "--independent-qa-go-receipt",
                str(go),
                "--expected-independent-qa-go-receipt-sha256",
                _sha(go),
            ]
        )
    out = fixture["out"]
    assert (out / EVALUATOR.CLAIM_NAME).is_file()
    fatal = json.loads((out / EVALUATOR.FATAL_FAIL_NAME).read_text(encoding="utf-8"))
    assert fatal["status"] == "FAIL_IRREVERSIBLE_TEST_RELEASE_CONSUMED"
    assert fatal["retry_authorized"] is False
    assert fatal["failed_model_arm_denominator_retained"] == 6
    names = {path.name for path in out.iterdir()}
    assert names == EVALUATOR.PREPARE_FILE_NAMES | {
        EVALUATOR.CLAIM_NAME,
        EVALUATOR.FATAL_FAIL_NAME,
        EVALUATOR.FAILURE_INDEX_NAME,
    }
    assert (out.stat().st_mode & 0o777) == EVALUATOR.FINAL_DIRECTORY_MODE
    assert all(
        path.is_file()
        and (path.stat().st_mode & 0o777) == EVALUATOR.FINAL_FILE_MODE
        and path.stat().st_nlink == 1
        for path in out.iterdir()
    )
    EVALUATOR._verify_index(
        out / EVALUATOR.FAILURE_INDEX_NAME,
        out,
        names - {EVALUATOR.FAILURE_INDEX_NAME},
    )
    assert fatal["failure_filesystem_contract"]["regular_files_exact"] == sorted(
        names
    )

    manifest = json.loads((out / EVALUATOR.MANIFEST_NAME).read_text(encoding="utf-8"))
    bindings = manifest["release_contract"]["release_bindings"]
    lease = bindings["one_time_release_lease"]
    lease_path = Path(lease["path"])
    out.chmod(0o755)
    for name in (
        EVALUATOR.CLAIM_NAME,
        EVALUATOR.FATAL_FAIL_NAME,
        EVALUATOR.FAILURE_INDEX_NAME,
    ):
        (out / name).unlink()
    lease_path.chmod(0o600)
    lease_path.write_bytes(
        EVALUATOR._lease_state_bytes(
            "PREPARED",
            manifest["qa_challenge_nonce"],
            bindings["evaluation_output_root_identity"],
        )
    )
    with pytest.raises(EVALUATOR.EvaluationError, match="one-time release lease"):
        EVALUATOR.main(_execute_args(fixture, go))
    assert not (out / EVALUATOR.CLAIM_NAME).exists()
    assert not (out / EVALUATOR.FATAL_FAIL_NAME).exists()


def test_weights_loader_is_pickle_forbidden_and_rejects_nonfinite(tmp_path: Path) -> None:
    normalization = {
        "x_mean": np.zeros(4),
        "x_scale": np.ones(4),
        "y_mean": np.zeros(10),
        "y_scale": np.ones(10),
        "geometry_lower_normalized": -np.ones(10),
        "geometry_upper_normalized": np.ones(10),
    }
    bad = tmp_path / "bad.npz"
    payload = np.empty(1, dtype=object)
    payload[0] = {"unsafe": True}
    np.savez(bad, forward_weight_0=payload)
    with pytest.raises((ValueError, EVALUATOR.EvaluationError), match="Object arrays"):
        EVALUATOR._audit_weights(bad, "0" * 64, normalization, 1)


def _descriptor_runtime_fixture(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    python = tmp_path / "sealed-python"
    python.write_bytes(b"fixture-python-executable\n")
    python.chmod(0o555)
    python_identity = EVALUATOR._pinned_file_identity(
        python, "fixture sealed Python"
    )
    roles = {
        role: {
            "member": f"sealed/{role}.py",
            "sha256": _identity(role),
            "size_bytes": len(role) + 1,
        }
        for role in (
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
        )
    }
    closure = {
        "schema": "fixture_descriptor_closure_v1",
        "manifest": {
            "path": str(tmp_path / "manifest.json"),
            "sha256": _identity("manifest"),
            "size_bytes": 1,
        },
        "bootstrap": {
            "path": str(tmp_path / "bootstrap.py"),
            "sha256": _identity("bootstrap"),
            "size_bytes": 2,
        },
        "pure_archive": {
            "path": "closure/pure.zip",
            "sha256": _identity("pure"),
            "size_bytes": 3,
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.13",
            "abi_tag": "cpython-312-x86_64-linux-gnu",
            "platform": "linux-x86_64",
            "executable_sha256": python_identity["sha256"],
        },
        "numpy": {"version": "2.5.0"},
        "native_extensions": [],
        "native_libraries": [],
        "system_library_allowlist": list(
            EVALUATOR.runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
        ),
        "role_bindings": roles,
    }
    evaluator_runtime = {
        "schema": "controlled_real10k_20k_descriptor_runtime_binding_v1",
        "python_version": "3.12.13",
        "python_implementation": "cpython",
        "numpy_version": "2.5.0",
        "numpy_show_config_sha256": _identity("numpy-config"),
        "active_runtime": {
            "schema": "controlled_real10k_20k_runtime_attestation_v1",
            "entrypoint": "evaluator",
            "manifest_sha256": closure["manifest"]["sha256"],
            "pure_archive_sha256": closure["pure_archive"]["sha256"],
            "bootstrap_sha256": closure["bootstrap"]["sha256"],
        },
        "descriptor_closure": closure,
        "role_bindings": roles,
        "system_library_allowlist": list(
            EVALUATOR.runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
        ),
        "files": {"python_executable": python_identity},
    }
    paired_runtime = {
        "python": {
            "path": str(python),
            "resolved_path_at_open": str(python),
            "sha256": python_identity["sha256"],
            "size_bytes": python_identity["size_bytes"],
            "device": python_identity["device"],
            "inode": python_identity["inode"],
            "nlink": python_identity["nlink"],
            "execution_mode": "pinned_descriptor_procfd_executable_v1",
        },
        "numpy_version": "2.5.0",
        "bootstrap": closure["bootstrap"],
        "descriptor_closure": closure,
    }
    return evaluator_runtime, paired_runtime


def _runtime_attestation_fixture_records(
    paired_runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    closure = paired_runtime["descriptor_closure"]
    roles = closure["role_bindings"]
    module_roles = {
        "rfic_transformer_inverse_design": "package_init_code",
        "rfic_transformer_inverse_design.controlled_real10k_20k_contract": (
            "shared_contract_code"
        ),
        "rfic_transformer_inverse_design.model_splitting": "splitter_code",
        EVALUATOR.runtime_bootstrap.BOOTSTRAP_MODULE: "runtime_bootstrap_code",
    }
    module_origins = {
        "numpy": {
            "kind": "sealed_pure_zip",
            "origin": "descriptor-zip:/proc/self/fd/30!/numpy/__init__.py",
            "sha256": _identity("numpy-init"),
        },
        **{
            module_name: {
                "kind": "sealed_pure_zip",
                "origin": (
                    "descriptor-zip:/proc/self/fd/30!/"
                    + module_name.replace(".", "/")
                    + ".py"
                ),
                "sha256": roles[role]["sha256"],
            }
            for module_name, role in module_roles.items()
        },
    }
    common = {
        "schema": EVALUATOR.runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "trainer",
        "manifest_sha256": closure["manifest"]["sha256"],
        "pure_archive_sha256": closure["pure_archive"]["sha256"],
        "bootstrap_sha256": closure["bootstrap"]["sha256"],
    }
    startup = {
        **common,
        "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
        "entrypoint_sha256": roles["trainer_code"]["sha256"],
        "python": {
            key: closure["python"][key]
            for key in ("implementation", "version", "abi_tag", "platform")
        },
        "python_flags": {
            "isolated": 1,
            "no_site": 1,
            "dont_write_bytecode": True,
        },
        "numpy_version": paired_runtime["numpy_version"],
        "module_origins": module_origins,
        "native_library_sha256": {},
        "native_extension_sha256": {},
        "system_library_allowlist": closure["system_library_allowlist"],
        "site_initialization_disabled": True,
        "external_package_fallback_allowed": False,
    }
    terminal = {
        **common,
        "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
        "exit_code": 0,
        "module_origins": module_origins,
        "system_library_allowlist": closure["system_library_allowlist"],
        "external_package_fallback_allowed": False,
    }
    return [startup, terminal]


def _write_runtime_attestation_fixture(
    path: Path,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if path.exists():
        path.chmod(0o600)
    payload = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
        for record in records
    ).encode("ascii")
    path.write_bytes(payload)
    path.chmod(0o400)
    return {"path": str(path), "sha256": _sha(path), "size_bytes": len(payload)}


def _process_singleton_contract_fixture(
    role_destinations: dict[str, str],
) -> dict[str, Any]:
    expected = {
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
    return {
        "schema": EVALUATOR.PROCESS_SINGLETON_CONTRACT_SCHEMA,
        "lock": {
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
        "protected_entrypoints": [
            {
                "role": role,
                "path": role_destinations[role],
                "controller": expected[role][0],
                "execution_identity": expected[role][1],
                "runtime_entrypoint": expected[role][2],
            }
            for role in sorted(expected)
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


def _file_identity_fixture(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    return {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "st_uid": metadata.st_uid,
        "st_gid": metadata.st_gid,
        "mode_octal": f"{metadata.st_mode & 0o7777:04o}",
        "nlink": metadata.st_nlink,
        "size_bytes": metadata.st_size,
    }


def _preflight_record_fixture(path: Path, displayed_path: str) -> dict[str, Any]:
    return {
        "path": displayed_path,
        "sha256": _sha(path),
        "identity": _file_identity_fixture(path),
    }


def _candidate_record_fixture(role: str, path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    return {
        "role": role,
        "path": str(path),
        "sha256": _sha(path),
        "size_bytes": metadata.st_size,
        "mode_octal": f"{metadata.st_mode & 0o7777:04o}",
        "nlink": metadata.st_nlink,
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
    }


def _singleton_transmission_fixture(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    _evaluator_runtime, paired_runtime = _descriptor_runtime_fixture(tmp_path / "runtime")
    closure = paired_runtime["descriptor_closure"]
    package_root = tmp_path / "package"
    package_root.mkdir()
    role_destinations = {
        "evaluator_code": "runtime/scripts/evaluate.py",
        "materialization_builder_code": "runtime/scripts/build.py",
        "materialization_gate_code": "runtime/scripts/materialize.py",
        "native_smoke_test": "runtime/tests/native_smoke.py",
        "preflight_code": "runtime/scripts/preflight.py",
        "runner_code": "runtime/scripts/runner.py",
        "runtime_bootstrap_code": "runtime/bootstrap/bootstrap.py",
        "trainer_code": "runtime/scripts/trainer.py",
        "process_singleton_contract_json": (
            "runtime/contracts/PROCESS_SINGLETON_CONTRACT.json"
        ),
        "runtime_dependency_closure_json": "runtime/contracts/RUNTIME_CLOSURE.json",
        "runtime_dependency_closure_tree": "runtime/dependencies",
    }
    contract = _process_singleton_contract_fixture(role_destinations)
    contract_path = package_root / role_destinations["process_singleton_contract_json"]
    _write_json(contract_path, contract)
    contract_path.chmod(0o444)
    lock_path = package_root / "CONTROLLED_SINGLETON.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o444)

    runtime_manifest_role = {
        "kind": "file",
        "path": role_destinations["runtime_dependency_closure_json"],
        "sha256": closure["manifest"]["sha256"],
    }
    runtime_tree_role = {
        "kind": "tree",
        "path": role_destinations["runtime_dependency_closure_tree"],
        "sha256": _identity("runtime-tree"),
    }
    role_identity = {
        role: {
            "kind": "file",
            "path": destination,
            "sha256": _identity(role),
        }
        for role, destination in role_destinations.items()
        if role not in {"runtime_dependency_closure_tree"}
    }
    role_identity["process_singleton_contract_json"]["sha256"] = _sha(contract_path)
    role_identity["runtime_dependency_closure_json"] = runtime_manifest_role
    role_identity["runtime_dependency_closure_tree"] = runtime_tree_role
    package_manifest = {
        "schema": "controlled_real10k_20k_mars_package_v2",
        "package_version": EVALUATOR.PACKAGE_VERSION,
        "build_spec": {},
        "required_roles": sorted(role_identity),
        "role_destinations": role_destinations,
        "role_identity": role_identity,
        "artifacts": [],
        "runtime": {
            "entrypoints": {},
            "import_graph": {},
            "dependency_closure": {},
            "process_singleton_contract": {
                "schema": EVALUATOR.PROCESS_SINGLETON_CONTRACT_SCHEMA,
                "path": role_destinations["process_singleton_contract_json"],
                "sha256": _sha(contract_path),
                "lock_path": "CONTROLLED_SINGLETON.lock",
                "lock_sha256": _sha(lock_path),
                "protected_entrypoints": contract["protected_entrypoints"],
            },
        },
        "authorities": {},
        "execution_authorized": False,
        "result_accessed": False,
        "numerical_metrics_accessed": False,
    }
    package_manifest_path = package_root / "MANIFEST.json"
    _write_json(package_manifest_path, package_manifest)
    package_manifest_path.chmod(0o444)

    package_receipt_path = package_root / "RECEIPT.json"
    package_qa_path = package_root / "INDEPENDENT_QA_REQUIRED.json"
    package_index_path = package_root / "SHA256SUMS.txt"
    _write_json(package_receipt_path, {"fixture": "package-receipt"})
    _write_json(package_qa_path, {"fixture": "package-qa"})
    package_index_path.write_text("fixture package index\n", encoding="ascii")
    for path in (package_receipt_path, package_qa_path, package_index_path):
        path.chmod(0o444)

    attempt_root = tmp_path / "package-attempt"
    attempt_root.mkdir(mode=0o755)
    attempt_body_path = attempt_root / EVALUATOR.PACKAGE_BUILD_ATTEMPT_BODY_NAME
    attempt_committed_path = (
        attempt_root / EVALUATOR.PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
    )
    package_commit_path = package_root / "PACKAGE_COMMIT.json"
    package_authorities = dict(EVALUATOR.PACKAGE_NO_AUTHORITY)
    package_commit = {
        "schema": EVALUATOR.PACKAGE_COMMIT_SCHEMA,
        "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        "package_version": EVALUATOR.PACKAGE_VERSION,
        "manifest": {"path": "MANIFEST.json", "sha256": _sha(package_manifest_path)},
        "receipt": {"path": "RECEIPT.json", "sha256": _sha(package_receipt_path)},
        "independent_qa_required": {
            "path": "INDEPENDENT_QA_REQUIRED.json",
            "sha256": _sha(package_qa_path),
        },
        "sha256sums": {
            "path": "SHA256SUMS.txt",
            "sha256": _sha(package_index_path),
        },
        "required_external_pass_attempt": {
            "body": {
                "path": str(attempt_body_path),
                "schema": EVALUATOR.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
                "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
            },
            "committed": {
                "path": str(attempt_committed_path),
                "schema": EVALUATOR.PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
                "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
            },
        },
        "creation_order_contract": {
            "this_member_created_last": True,
            "post_commit_package_file_creation_permitted": False,
        },
        "authorities": package_authorities,
        "execution_authorized": False,
    }
    _write_json(package_commit_path, package_commit)
    package_commit_path.chmod(0o444)
    package_root.chmod(0o555)

    package_meta = package_root.lstat()
    attempt_body = {
        "schema": EVALUATOR.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
        "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        "started_utc": "2026-08-24T00:00:00Z",
        "completed_utc": "2026-08-24T00:00:30Z",
        "invocation": {
            "argv": ["python3", "build-package"],
            "cwd": {
                "lexical": str(tmp_path),
                "resolved": str(tmp_path.resolve()),
                "device": tmp_path.lstat().st_dev,
                "inode": tmp_path.lstat().st_ino,
            },
            "output_dir": str(package_root),
            "failure_receipt_dir": str(attempt_root),
            "package_spec": {
                "path": str(tmp_path / "PACKAGE_SPEC.json"),
                "expected_sha256": _identity("package-spec"),
            },
            "builder": {
                "path": str(tmp_path / "package-builder.py"),
                "expected_sha256": _identity("package-builder"),
            },
            "python": {
                "implementation": "CPython",
                "version": "3.12.13",
                "version_info": [3, 12, 13, "final", 0],
                "executable_lexical": "/usr/bin/python3",
                "executable_resolved": "/usr/bin/python3",
                "executable_sha256": _identity("package-python"),
                "flags": {},
            },
            "runtime": {
                "platform": "Linux-fixture",
                "machine": "x86_64",
                "system": "Linux",
                "release": "fixture",
                "byteorder": "little",
                "filesystem_encoding": "utf-8",
            },
            "environment": {
                "raw_values_recorded": False,
                "key_count": 0,
                "keys": [],
                "keyset_sha256": _identity("package-env-keys"),
                "key_value_map_sha256": _identity("package-env-map"),
            },
        },
        "observed_identity": {
            "package_spec_sha256": _identity("package-spec"),
            "builder_sha256": _identity("package-builder"),
            "package_output_device": package_meta.st_dev,
            "package_output_inode": package_meta.st_ino,
        },
        "package": {
            "path": str(package_root),
            "manifest_sha256": _sha(package_manifest_path),
            "receipt_sha256": _sha(package_receipt_path),
            "independent_qa_required_sha256": _sha(package_qa_path),
            "sha256sums_sha256": _sha(package_index_path),
            "package_commit_sha256": _sha(package_commit_path),
            "file_count": 7,
        },
        "partial_output_preserved": False,
        "authorities": package_authorities,
        "execution_authorized": False,
    }
    _write_json(attempt_body_path, attempt_body)
    attempt_body_path.chmod(0o444)
    attempt_meta = attempt_root.lstat()
    attempt_parent_meta = attempt_root.parent.lstat()
    attempt_committed = {
        "schema": EVALUATOR.PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
        "status": "PASS_PACKAGE_BUILD_ATTEMPT_DURABLY_COMMITTED",
        "committed_utc": "2026-08-24T00:00:31Z",
        "body": {
            "path": str(attempt_body_path),
            "sha256": _sha(attempt_body_path),
            "schema": EVALUATOR.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
            "status": "PASS_PACKAGE_BUILT_IMMUTABLE_AWAITING_QA",
        },
        "package_commit": {
            "path": str(package_commit_path),
            "sha256": _sha(package_commit_path),
            "schema": EVALUATOR.PACKAGE_COMMIT_SCHEMA,
            "status": "PACKAGE_DURABLY_COMMITTED_AWAITING_EXTERNAL_PASS_ATTEMPT",
        },
        "package_root": {
            "path": str(package_root),
            "st_dev": package_meta.st_dev,
            "st_ino": package_meta.st_ino,
            "mode_octal": "0555",
        },
        "attempt_root": {
            "path": str(attempt_root),
            "st_dev": attempt_meta.st_dev,
            "st_ino": attempt_meta.st_ino,
            "mode_octal": "0555",
        },
        "attempt_parent": {
            "path": str(attempt_root.parent),
            "st_dev": attempt_parent_meta.st_dev,
            "st_ino": attempt_parent_meta.st_ino,
            "mode_octal": f"{attempt_parent_meta.st_mode & 0o7777:04o}",
        },
        "publication": dict(EVALUATOR.PACKAGE_BUILD_ATTEMPT_PUBLICATION),
        "authorities": package_authorities,
        "execution_authorized": False,
    }
    _write_json(attempt_committed_path, attempt_committed)
    attempt_committed_path.chmod(0o444)
    attempt_root.chmod(0o555)

    preflight_root = tmp_path / "preflight"
    preflight_root.mkdir(mode=0o700)
    bound_paths: dict[str, Path] = {
        "package_build_attempt_body": attempt_body_path,
        "package_build_attempt_committed": attempt_committed_path,
        "package_process_singleton_contract": contract_path,
        "package_singleton_lock": lock_path,
    }
    for role, filename in EVALUATOR.MARS_PREFLIGHT_ROLE_FILENAMES.items():
        bound_paths[role] = preflight_root / filename
    for role in EVALUATOR.MATERIALIZATION_BOUND_ROLE_ORDER:
        if role in bound_paths or role == "mars_preflight_consumed_lease":
            continue
        path = tmp_path / "bound" / f"{role}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"{role}\n", encoding="ascii")
        path.chmod(0o444)
        bound_paths[role] = path

    for role in (
        "mars_preflight_prepared",
        "mars_preflight_execution_qa_required",
        "mars_preflight_prepare_sha_index",
    ):
        bound_paths[role].write_text(f"{role}\n", encoding="ascii")
        bound_paths[role].chmod(0o444)
    authorities = {
        "direct_data_materialization_authorized": False,
        "training_authorized": False,
        "common_test_access_authorized": False,
        "numerical_metric_access_authorized": False,
        "fresh_emx_authorized": False,
        "process_signal_authorized": False,
    }
    root_metadata = preflight_root.lstat()
    prepared_root_identity = {
        "st_dev": root_metadata.st_dev,
        "st_ino": root_metadata.st_ino,
        "st_uid": root_metadata.st_uid,
        "st_gid": root_metadata.st_gid,
        "mode_octal": "0700",
    }
    lease_path = (
        preflight_root.parent
        / f".{preflight_root.name}.controlled_real10k_20k_preflight_once_lease.json"
    )
    lease = {
        "schema": EVALUATOR.MARS_PREFLIGHT_LEASE_SCHEMA,
        "state": "CONSUMED",
        "challenge_nonce": "0" * 32,
        "receipt_root": {
            "path": str(preflight_root),
            "identity": prepared_root_identity,
        },
        "created_utc": "2026-08-24T00:00:00Z",
        "consumed_utc": "2026-08-24T00:01:00Z",
        "single_use": True,
        "retry_authorized": False,
        "authorities": authorities,
    }
    _write_json(lease_path, lease)
    lease_path.chmod(0o444)
    bound_paths["mars_preflight_consumed_lease"] = lease_path
    consumed_lease_record = {
        **_preflight_record_fixture(lease_path, str(lease_path)),
        "schema": EVALUATOR.MARS_PREFLIGHT_LEASE_SCHEMA,
        "state": "CONSUMED",
    }
    contract_record = _preflight_record_fixture(contract_path, str(contract_path))
    lock_record = _preflight_record_fixture(lock_path, str(lock_path))
    process_audit = {
        "schema": "controlled_real10k_20k_preflight_process_audit_v2",
        "uid": os.geteuid(),
        "current_pid": 12345,
        "substring_matching_used": False,
        "exact_argv_executable_and_descriptor_identity_required": True,
        "matches": [],
        "match_count": 0,
    }
    process_singleton = {
        "contract": contract_record,
        "contract_payload": contract,
        "lock": lock_record,
        "lock_operation": "LOCK_EX|LOCK_NB",
        "lock_held_for_full_execute_lifetime": True,
        "protected_entrypoints": contract["protected_entrypoints"],
        "proc_audit_contract": contract["proc_audit"],
        "before": process_audit,
        "after": copy.deepcopy(process_audit),
        "all_counts_zero": True,
        "current_uid_only": True,
    }
    expected_checks = {
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
    body = {
        "schema": EVALUATOR.MARS_PREFLIGHT_BODY_SCHEMA,
        "status": "PASS_BODY_AWAITING_DURABLE_COMMIT",
        "started_utc": "2026-08-24T00:00:00Z",
        "body_generated_utc": "2026-08-24T00:01:00Z",
        "package": {
            "root": str(package_root),
            "manifest_sha256": _sha(package_manifest_path),
            "sha_index_sha256": _sha(package_index_path),
            "receipt_sha256": _sha(package_receipt_path),
            "independent_qa_required_sha256": _sha(package_qa_path),
            "commit_sha256": _sha(package_commit_path),
            "build_attempt_body_path": str(attempt_body_path),
            "build_attempt_body_sha256": _sha(attempt_body_path),
            "build_attempt_committed_path": str(attempt_committed_path),
            "build_attempt_committed_sha256": _sha(attempt_committed_path),
            "role_sha256": {role: record["sha256"] for role, record in role_identity.items()},
            "role_identity": role_identity,
            "runtime_dependency_closure": {},
            "runtime_entrypoints": {},
        },
        "external_code_go": {},
        "receipt_transaction": {
            "prepared_binding": {},
            "consumed_external_one_use_lease": consumed_lease_record,
        },
        "host_identity": {},
        "runtime_identity": {},
        "process_singleton": process_singleton,
        "candidate_output_dirs": [],
        "candidate_output_dirs_absent_before_and_after": True,
        "native_tests": {},
        "host_load_snapshot": {},
        "checks": {key: True for key in expected_checks},
        "preflight_pass": False,
        "committed_terminal_marker_required": "PREFLIGHT_COMMITTED.json",
        "authorities": authorities,
        "next_legal_action": "NO_ACTION_UNTIL_DURABLE_COMMITTED_MARKER_IS_VERIFIED",
    }
    _write_json(bound_paths["mars_preflight_receipt_body"], body)
    bound_paths["mars_preflight_receipt_body"].chmod(0o444)
    bound_paths["mars_preflight_sha_index"].write_text("fixture index\n", encoding="ascii")
    bound_paths["mars_preflight_sha_index"].chmod(0o444)
    prepared_artifacts = {
        "prepared_receipt": _preflight_record_fixture(
            bound_paths["mars_preflight_prepared"], "PREFLIGHT_PREPARED.json"
        ),
        "execution_qa_required": _preflight_record_fixture(
            bound_paths["mars_preflight_execution_qa_required"],
            "PREFLIGHT_EXECUTION_QA_REQUIRED.json",
        ),
        "prepare_sha256sums": _preflight_record_fixture(
            bound_paths["mars_preflight_prepare_sha_index"],
            "PREPARE_SHA256SUMS.txt",
        ),
    }
    committed_root_identity = dict(prepared_root_identity)
    committed_root_identity["mode_octal"] = "0555"
    parent_meta = preflight_root.parent.lstat()
    parent_identity = {
        "st_dev": parent_meta.st_dev,
        "st_ino": parent_meta.st_ino,
        "st_uid": parent_meta.st_uid,
        "st_gid": parent_meta.st_gid,
        "mode_octal": f"{parent_meta.st_mode & 0o7777:04o}",
    }
    committed = {
        "schema": EVALUATOR.MARS_PREFLIGHT_COMMITTED_SCHEMA,
        "status": "COMMITTED_PASS_PREFLIGHT_ONLY",
        "committed_utc": "2026-08-24T00:02:00Z",
        "preflight_pass": True,
        "receipt_root": {
            "path": str(preflight_root),
            "prepared_identity": prepared_root_identity,
            "committed_identity": committed_root_identity,
        },
        "receipt_parent": {"path": str(preflight_root.parent), "identity": parent_identity},
        "prepared_artifacts": prepared_artifacts,
        "receipt_body": _preflight_record_fixture(
            bound_paths["mars_preflight_receipt_body"], "PREFLIGHT_RECEIPT_BODY.json"
        ),
        "sha256_index": _preflight_record_fixture(
            bound_paths["mars_preflight_sha_index"], "PREFLIGHT_SHA256SUMS.txt"
        ),
        "external_code_go": {},
        "consumed_external_one_use_lease": consumed_lease_record,
        "process_singleton": process_singleton,
        "exact_root_filenames": list(EVALUATOR.MARS_PREFLIGHT_SUCCESS_FILES),
        "failure_marker_absent_at_commit": True,
        "failure_marker_has_absolute_precedence": True,
        "body_is_not_authority": True,
        "authorities": authorities,
        "next_legal_action": (
            "SEPARATE_RESULT_BLIND_MATERIALIZATION_RECEIPT_AND_EXACT_"
            "AUTHORIZATION_REQUIRED"
        ),
    }
    _write_json(bound_paths["mars_preflight_committed"], committed)
    bound_paths["mars_preflight_committed"].chmod(0o444)
    preflight_root.chmod(0o555)

    full_bindings = {
        role: _candidate_record_fixture(role, bound_paths[role])
        for role in EVALUATOR.MATERIALIZATION_BOUND_ROLE_ORDER
    }
    reduced_bindings = {
        role: {"path": record["path"], "sha256": record["sha256"]}
        for role, record in full_bindings.items()
    }
    sealed_runtime = {
        "expected_runtime_closure_json_sha256": closure["manifest"]["sha256"],
        "attestation": {
            "schema": EVALUATOR.runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
            "entrypoint": "materialization",
            "manifest_sha256": closure["manifest"]["sha256"],
            "pure_archive_sha256": closure["pure_archive"]["sha256"],
            "bootstrap_sha256": closure["bootstrap"]["sha256"],
        },
        "runtime_manifest_role_identity": runtime_manifest_role,
        "runtime_tree_role_identity": runtime_tree_role,
        "required_external_entrypoint": "materialization",
        "raw_runtime_fallback_authorized": False,
    }
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
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    material_output_root = tmp_path / "material-output"
    material_output_root.mkdir()
    execution_root = tmp_path / "material-execution"
    execution_root.mkdir()
    runtime_identity_sha = _identity("candidate-runtime-identity")
    host_identity_sha = _identity("candidate-host-identity")
    candidate_manifest = {
        "schema": EVALUATOR.MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA,
        "generated_utc": "2026-08-24T00:03:00+00:00",
        "status": "PREPARED_IMMUTABLE_RESULT_BLIND_NO_AUTHORITY",
        "result_blind": True,
        "candidate_dir": str(candidate_root),
        "challenge_nonce": "1" * 32,
        "bindings": full_bindings,
        "bound_role_order": list(EVALUATOR.MATERIALIZATION_BOUND_ROLE_ORDER),
        "materialization_contract": {},
        "materialization_contract_sha256": _identity("materialization-contract"),
        "runtime_identity": {"identity_sha256": runtime_identity_sha},
        "host_identity": {"identity_sha256": host_identity_sha},
        "sealed_runtime": sealed_runtime,
        "host_constraints_asserted": {},
        "future_paths": {
            "materialization_out_dir": str(material_output_root),
            "execution_receipt_dir": str(execution_root),
        },
        "authorities": candidate_authorities,
        "result_or_row_access": {},
        "next_legal_gate": EVALUATOR.MATERIALIZATION_GO_SCHEMA,
    }
    candidate_manifest_path = candidate_root / "MANIFEST.json"
    _write_json(candidate_manifest_path, candidate_manifest)
    candidate_manifest_path.chmod(0o444)
    candidate_index_path = candidate_root / "SHA256SUMS.txt"
    candidate_index_path.write_text("fixture candidate index\n", encoding="ascii")
    candidate_index_path.chmod(0o444)

    go_authorities = dict(candidate_authorities)
    go_authorities["result_blind_data_materialization"] = True
    artifact_sha = {
        role: full_bindings[role]["sha256"]
        for role in EVALUATOR.MATERIALIZATION_BOUND_ROLE_ORDER
    }
    materialization_go = {
        "schema": EVALUATOR.MATERIALIZATION_GO_SCHEMA,
        "status": "GO",
        "scope": "RESULT_BLIND_NESTED_10K_20K_MATERIALIZATION_ONLY",
        "issued_utc": "2026-08-24T00:03:01Z",
        "expires_utc": "2026-08-24T01:03:01Z",
        "challenge_nonce": candidate_manifest["challenge_nonce"],
        "reviewer": {
            "reviewer_id": "fixture-independent-qa",
            "independent": True,
            "result_blind": True,
            "reviewed_without_numerical_results": True,
        },
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "bindings": {
            "candidate_manifest_sha256": _sha(candidate_manifest_path),
            "candidate_sha256sums_sha256": _sha(candidate_index_path),
            "challenge_nonce": candidate_manifest["challenge_nonce"],
            "artifact_sha256": artifact_sha,
            "materialization_out_dir": str(material_output_root),
            "execution_receipt_dir": str(execution_root),
            "runtime_identity_sha256": runtime_identity_sha,
            "host_identity_sha256": host_identity_sha,
            "materialization_contract_sha256": candidate_manifest[
                "materialization_contract_sha256"
            ],
            "sealed_runtime": sealed_runtime,
        },
        "authorities": go_authorities,
    }
    go_path = execution_root / "GO_AUTHORITY.json"
    _write_json(go_path, materialization_go)
    go_path.chmod(0o444)
    output_index_sha = _identity("material-output-index")
    materialization_complete = {
        "schema": EVALUATOR.MATERIALIZATION_COMPLETE_SCHEMA,
        "generated_utc": "2026-08-24T00:04:00Z",
        "status": "COMPLETE_RESULT_BLIND_MATERIALIZATION_DEEP_VALIDATED",
        "candidate_manifest_sha256": _sha(candidate_manifest_path),
        "candidate_sha256sums_sha256": _sha(candidate_index_path),
        "go_sha256": _sha(go_path),
        "challenge_nonce": candidate_manifest["challenge_nonce"],
        "candidate_manifest": {
            "path": str(candidate_manifest_path),
            "sha256": _sha(candidate_manifest_path),
        },
        "candidate_sha_index": {
            "path": str(candidate_index_path),
            "sha256": _sha(candidate_index_path),
        },
        "materialization_go_authority": {
            "path": str(go_path),
            "sha256": _sha(go_path),
        },
        "materialization_output": {
            "path": str(material_output_root),
            "sha256sums": {
                "path": str(material_output_root / "SHA256SUMS.txt"),
                "sha256": output_index_sha,
            },
            "artifact_closure": {},
        },
        "materialization_validation": {
            "status": "PASS_MATERIALIZATION_DEEP_VALIDATED_RESULT_BLIND",
            "root": str(material_output_root),
            "arm_rows": {},
            "gradient_train_rows": {},
            "validation_rows_common": 902,
            "test_rows_common": 902,
            "artifact_closure": {},
            "sha256sums_sha256": output_index_sha,
            "training_authorized": False,
            "evaluation_authorized": False,
            "fresh_emx_authorized": False,
        },
        "frozen_closure_after_materialization": {
            "candidate_manifest_sha256": _sha(candidate_manifest_path),
            "candidate_sha256sums_sha256": _sha(candidate_index_path),
            "artifact_sha256": artifact_sha,
            "go_sha256": _sha(go_path),
            "held_snapshot_consumption": True,
            "path_reopen_for_consumed_inputs": False,
        },
        "sealed_runtime": sealed_runtime,
        "execution_precursor_closure": {},
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
    complete_path = execution_root / "COMPLETE.json"
    _write_json(complete_path, materialization_complete)
    complete_path.chmod(0o444)
    material = {
        "outer_materialization_authority": {
            "complete": {"path": str(complete_path), "sha256": _sha(complete_path)},
            "candidate_manifest": {
                "path": str(candidate_manifest_path),
                "sha256": _sha(candidate_manifest_path),
            },
            "candidate_sha_index": {
                "path": str(candidate_index_path),
                "sha256": _sha(candidate_index_path),
            },
            "materialization_go_authority": {
                "path": str(go_path),
                "sha256": _sha(go_path),
            },
            "candidate_bindings": reduced_bindings,
            "sealed_runtime": sealed_runtime,
            "materialization_output_closure": {},
        }
    }
    pinned = EVALUATOR._pinned_file_identity(lock_path, "fixture package singleton")
    held_singleton = {
        "schema": EVALUATOR.CONTROLLED_SINGLETON_SCHEMA,
        "path": pinned["path"],
        "sha256": pinned["sha256"],
        "size_bytes": pinned["size_bytes"],
        "device": pinned["device"],
        "inode": pinned["inode"],
        "nlink": pinned["nlink"],
        "lock_mode": EVALUATOR.CONTROLLED_SINGLETON_LOCK_MODE,
    }
    return material, paired_runtime, held_singleton, lock_path


def _package_attempt_audit_inputs(
    material: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path, Path]:
    candidate_path = Path(
        material["outer_materialization_authority"]["candidate_manifest"]["path"]
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    body_path = Path(candidate["bindings"]["mars_preflight_receipt_body"]["path"])
    preflight_body = json.loads(body_path.read_text(encoding="utf-8"))
    package = copy.deepcopy(preflight_body["package"])
    roles = {
        role: copy.deepcopy(candidate["bindings"][role])
        for role in (
            "package_build_attempt_body",
            "package_build_attempt_committed",
        )
    }
    return (
        package,
        roles,
        Path(roles["package_build_attempt_body"]["path"]),
        Path(roles["package_build_attempt_committed"]["path"]),
    )


def _materialization_gate_audit_inputs(
    material: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    outer = material["outer_materialization_authority"]
    candidate_binding = outer["candidate_manifest"]
    candidate = json.loads(
        Path(candidate_binding["path"]).read_text(encoding="utf-8")
    )
    return (
        outer,
        candidate,
        candidate_binding,
        outer["sealed_runtime"],
        candidate["bindings"],
    )


def _rewrite_frozen_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.chmod(0o755)
    path.chmod(0o644)
    _write_json(path, payload)
    path.chmod(0o444)
    path.parent.chmod(0o555)


def test_raw_cli_without_descriptor_runtime_fails_closed_before_usage() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-S", str(SCRIPT), "--help"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "usage:" not in completed.stdout.lower()
    assert (
        "production evaluator must start inside the exact descriptor runtime"
        in completed.stderr
        or "No module named 'numpy'" in completed.stderr
    )


def test_strict_json_rejects_duplicate_nonfinite_and_bool_integer_alias() -> None:
    with pytest.raises(EVALUATOR.EvaluationError, match="duplicate JSON"):
        EVALUATOR._json_from_bytes(b'{"count":0,"count":1}', "duplicate fixture")
    for token in (b"NaN", b"Infinity", b"-Infinity"):
        with pytest.raises(EVALUATOR.EvaluationError, match="non-finite"):
            EVALUATOR._json_from_bytes(
                b'{"value":' + token + b"}", "nonfinite fixture"
            )
    for alias in (False, True, 0.0, "0", None):
        with pytest.raises(EVALUATOR.EvaluationError, match="exact JSON integer"):
            EVALUATOR._require_exact_int(alias, "hostile count", expected=0)


def test_production_runtime_member_and_keyset_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    evaluator_runtime, paired_runtime = _descriptor_runtime_fixture(tmp_path)
    assert EVALUATOR._audit_paired_runtime_identity(
        paired_runtime, evaluator_runtime
    ) == paired_runtime

    extra = copy.deepcopy(paired_runtime)
    extra["unexpected"] = False
    with pytest.raises(EVALUATOR.EvaluationError, match="keyset mismatch"):
        EVALUATOR._audit_paired_runtime_identity(extra, evaluator_runtime)

    closure_mismatch = copy.deepcopy(paired_runtime)
    closure_mismatch["descriptor_closure"]["pure_archive"]["sha256"] = "0" * 64
    with pytest.raises(EVALUATOR.EvaluationError, match="runtime closure"):
        EVALUATOR._audit_paired_runtime_identity(
            closure_mismatch, evaluator_runtime
        )

    expected_shared = evaluator_runtime["role_bindings"]["shared_contract_code"]
    hostile_shared = dict(expected_shared)
    hostile_shared["size_bytes"] += 1
    with pytest.raises(EVALUATOR.EvaluationError, match="shared member binding"):
        EVALUATOR._audit_paired_shared_member(
            hostile_shared,
            evaluator_runtime,
            expected_shared["sha256"],
        )


def test_runtime_attestation_independent_two_record_closure(tmp_path: Path) -> None:
    _evaluator_runtime, paired_runtime = _descriptor_runtime_fixture(tmp_path)
    records = _runtime_attestation_fixture_records(paired_runtime)
    path = tmp_path / "RUNTIME_ATTESTATION.jsonl"
    binding = _write_runtime_attestation_fixture(path, records)

    audited = EVALUATOR._audit_runtime_attestation_file(
        binding,
        paired_runtime,
        "fixture trainer runtime attestation",
    )
    assert audited == {
        **binding,
        "record_count": 2,
        "startup_status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
        "terminal_status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
        "manifest_sha256": paired_runtime["descriptor_closure"]["manifest"]["sha256"],
        "pure_archive_sha256": paired_runtime["descriptor_closure"]["pure_archive"][
            "sha256"
        ],
        "bootstrap_sha256": paired_runtime["descriptor_closure"]["bootstrap"][
            "sha256"
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("startup_extra_key", "keyset mismatch"),
        ("terminal_bool_exit", "exact JSON type mismatch"),
        ("manifest_mismatch", "exact JSON value mismatch"),
        ("role_member_mismatch", "descriptor role"),
        ("terminal_origin_drift", "changed before terminal"),
    ],
)
def test_runtime_attestation_hostile_identity_and_type_mutations_fail_closed(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    _evaluator_runtime, paired_runtime = _descriptor_runtime_fixture(tmp_path)
    records = _runtime_attestation_fixture_records(paired_runtime)
    if mutation == "startup_extra_key":
        records[0]["unexpected"] = False
    elif mutation == "terminal_bool_exit":
        records[1]["exit_code"] = False
    elif mutation == "manifest_mismatch":
        records[1]["manifest_sha256"] = "0" * 64
    elif mutation == "role_member_mismatch":
        records[0]["module_origins"][
            "rfic_transformer_inverse_design.model_splitting"
        ]["sha256"] = "0" * 64
    elif mutation == "terminal_origin_drift":
        records[1]["module_origins"] = copy.deepcopy(records[1]["module_origins"])
        records[1]["module_origins"]["numpy"]["origin"] = (
            "descriptor-zip:/proc/self/fd/31!/numpy/__init__.py"
        )
    else:  # pragma: no cover - parametrization is frozen above
        raise AssertionError(mutation)
    binding = _write_runtime_attestation_fixture(
        tmp_path / "RUNTIME_ATTESTATION.jsonl", records
    )
    with pytest.raises(EVALUATOR.EvaluationError, match=error):
        EVALUATOR._audit_runtime_attestation_file(
            binding,
            paired_runtime,
            "hostile trainer runtime attestation",
        )


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity"])
def test_runtime_attestation_duplicate_and_nonfinite_json_fail_closed(
    tmp_path: Path,
    nonfinite: str,
) -> None:
    _evaluator_runtime, paired_runtime = _descriptor_runtime_fixture(tmp_path)
    records = _runtime_attestation_fixture_records(paired_runtime)
    path = tmp_path / "RUNTIME_ATTESTATION.jsonl"
    valid_terminal = json.dumps(
        records[1], sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    hostile_startup = json.dumps(
        records[0], sort_keys=True, separators=(",", ":"), allow_nan=False
    ).replace(
        '"bootstrap_sha256":',
        '"bootstrap_sha256":"' + records[0]["bootstrap_sha256"] + '","bootstrap_sha256":',
        1,
    )
    payload = (hostile_startup + "\n" + valid_terminal + "\n").encode("ascii")
    path.write_bytes(payload)
    path.chmod(0o400)
    duplicate_binding = {
        "path": str(path),
        "sha256": _sha(path),
        "size_bytes": len(payload),
    }
    with pytest.raises(EVALUATOR.EvaluationError, match="duplicate JSON"):
        EVALUATOR._audit_runtime_attestation_file(
            duplicate_binding,
            paired_runtime,
            "duplicate trainer runtime attestation",
        )

    path.chmod(0o600)
    valid_startup = json.dumps(
        records[0], sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    hostile_terminal = valid_terminal.replace('"exit_code":0', f'"exit_code":{nonfinite}')
    payload = (valid_startup + "\n" + hostile_terminal + "\n").encode("ascii")
    path.write_bytes(payload)
    path.chmod(0o400)
    nonfinite_binding = {
        "path": str(path),
        "sha256": _sha(path),
        "size_bytes": len(payload),
    }
    with pytest.raises(EVALUATOR.EvaluationError, match="non-finite"):
        EVALUATOR._audit_runtime_attestation_file(
            nonfinite_binding,
            paired_runtime,
            "nonfinite trainer runtime attestation",
        )


def test_singleton_transmission_rebuilds_exact21_preflight_package_chain(
    tmp_path: Path,
) -> None:
    material, paired_runtime, held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    proof = EVALUATOR._audit_singleton_transmission_closure(
        material,
        paired_runtime,
        held_singleton,
    )
    assert proof["flat_singleton"] == held_singleton
    assert proof["exact_bound_role_order"] == list(
        EVALUATOR.MATERIALIZATION_BOUND_ROLE_ORDER
    )
    assert proof["process_singleton_contract"]["sha256"]
    assert proof["package_manifest"]["sha256"]
    assert proof["package_build_attempt"]["body"]["sha256"]
    assert proof["package_build_attempt"]["committed"]["sha256"]


def test_materialization_candidate_v1_is_rejected_after_v2_freeze(
    tmp_path: Path,
) -> None:
    material, paired_runtime, held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    outer = material["outer_materialization_authority"]
    candidate_path = Path(outer["candidate_manifest"]["path"])
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["schema"] = "controlled_real10k_20k_materialization_gate_manifest_v1"
    _rewrite_frozen_json(candidate_path, candidate)
    outer["candidate_manifest"]["sha256"] = _sha(candidate_path)
    with pytest.raises(EVALUATOR.EvaluationError, match="value mismatch.*schema"):
        EVALUATOR._audit_singleton_transmission_closure(
            material,
            paired_runtime,
            held_singleton,
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [("old_schema", "value mismatch.*schema"), ("extra_key", "keyset mismatch")],
)
def test_materialization_complete_v3_schema_and_keyset_are_exact(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    material, _paired_runtime, _held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    outer, candidate, candidate_binding, sealed_runtime, audited_roles = (
        _materialization_gate_audit_inputs(material)
    )
    complete_path = Path(outer["complete"]["path"])
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    if mutation == "old_schema":
        complete["schema"] = "controlled_real10k_20k_materialization_complete_v2"
    elif mutation == "extra_key":
        complete["unexpected"] = False
    else:  # pragma: no cover - parametrization is frozen above
        raise AssertionError(mutation)
    _rewrite_frozen_json(complete_path, complete)
    outer["complete"]["sha256"] = _sha(complete_path)
    with pytest.raises(EVALUATOR.EvaluationError, match=error):
        EVALUATOR._audit_materialization_gate_authority(
            outer,
            candidate,
            candidate_binding,
            sealed_runtime,
            audited_roles,
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [("old_schema", "value mismatch.*schema"), ("extra_key", "keyset mismatch")],
)
def test_materialization_go_v2_schema_and_keyset_are_exact(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    material, _paired_runtime, _held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    outer, candidate, candidate_binding, sealed_runtime, audited_roles = (
        _materialization_gate_audit_inputs(material)
    )
    go_path = Path(outer["materialization_go_authority"]["path"])
    go = json.loads(go_path.read_text(encoding="utf-8"))
    if mutation == "old_schema":
        go["schema"] = "controlled_real10k_20k_materialization_exact_go_v1"
    elif mutation == "extra_key":
        go["unexpected"] = False
    else:  # pragma: no cover - parametrization is frozen above
        raise AssertionError(mutation)
    _rewrite_frozen_json(go_path, go)
    go_sha = _sha(go_path)
    outer["materialization_go_authority"]["sha256"] = go_sha
    complete_path = Path(outer["complete"]["path"])
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["go_sha256"] = go_sha
    complete["materialization_go_authority"]["sha256"] = go_sha
    complete["frozen_closure_after_materialization"]["go_sha256"] = go_sha
    _rewrite_frozen_json(complete_path, complete)
    outer["complete"]["sha256"] = _sha(complete_path)
    with pytest.raises(EVALUATOR.EvaluationError, match=error):
        EVALUATOR._audit_materialization_gate_authority(
            outer,
            candidate,
            candidate_binding,
            sealed_runtime,
            audited_roles,
        )


def test_package_build_attempt_body_without_committed_marker_fails_closed(
    tmp_path: Path,
) -> None:
    material, _paired_runtime, _held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    package, roles, body_path, committed_path = _package_attempt_audit_inputs(
        material
    )
    committed_path.parent.chmod(0o755)
    committed_path.unlink()
    committed_path.parent.chmod(0o555)
    with pytest.raises(EVALUATOR.EvaluationError, match="cannot open.*committed"):
        EVALUATOR._audit_package_build_attempt_closure(
            package, Path(package["root"]), roles
        )
    assert body_path.is_file()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("extra_key", "keyset mismatch"),
        ("bool_as_int", "exact JSON type mismatch"),
        ("false_durability", "exact JSON value mismatch"),
    ],
)
def test_package_build_attempt_committed_exact_keyset_and_durability_fail_closed(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    material, _paired_runtime, _held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    package, roles, _body_path, committed_path = _package_attempt_audit_inputs(
        material
    )
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    if mutation == "extra_key":
        committed["unexpected"] = False
    elif mutation == "bool_as_int":
        committed["publication"]["body_file_fsync"] = 1
    elif mutation == "false_durability":
        committed["publication"]["body_file_fsync"] = False
    else:  # pragma: no cover - parametrization is frozen above
        raise AssertionError(mutation)
    _rewrite_frozen_json(committed_path, committed)
    roles["package_build_attempt_committed"] = _candidate_record_fixture(
        "package_build_attempt_committed", committed_path
    )
    package["build_attempt_committed_sha256"] = _sha(committed_path)
    with pytest.raises(EVALUATOR.EvaluationError, match=error):
        EVALUATOR._audit_package_build_attempt_closure(
            package, Path(package["root"]), roles
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [("duplicate", "duplicate JSON"), ("nonfinite", "non-finite")],
)
def test_package_build_attempt_body_strict_json_fails_closed(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    material, _paired_runtime, _held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    package, roles, body_path, _committed_path = _package_attempt_audit_inputs(
        material
    )
    raw = body_path.read_text(encoding="utf-8")
    body_path.parent.chmod(0o755)
    body_path.chmod(0o644)
    if mutation == "duplicate":
        raw = raw.replace('"schema":', '"schema":"hostile","schema":', 1)
    elif mutation == "nonfinite":
        raw = raw.replace("{", '{"hostile":NaN,', 1)
    else:  # pragma: no cover - parametrization is frozen above
        raise AssertionError(mutation)
    body_path.write_text(raw, encoding="utf-8")
    body_path.chmod(0o444)
    body_path.parent.chmod(0o555)
    roles["package_build_attempt_body"] = _candidate_record_fixture(
        "package_build_attempt_body", body_path
    )
    package["build_attempt_body_sha256"] = _sha(body_path)
    with pytest.raises(EVALUATOR.EvaluationError, match=error):
        EVALUATOR._audit_package_build_attempt_closure(
            package, Path(package["root"]), roles
        )


def test_singleton_transmission_same_sha_lock_inode_replacement_fails_closed(
    tmp_path: Path,
) -> None:
    material, paired_runtime, held_singleton, lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    old = lock_path.with_name("CONTROLLED_SINGLETON.original")
    lock_path.parent.chmod(0o755)
    lock_path.rename(old)
    lock_path.write_bytes(b"")
    lock_path.chmod(0o444)
    lock_path.parent.chmod(0o555)
    with pytest.raises(EVALUATOR.EvaluationError, match="live materialization candidate"):
        EVALUATOR._audit_singleton_transmission_closure(
            material,
            paired_runtime,
            held_singleton,
        )


def test_process_singleton_contract_bool_integer_and_extra_key_fail_closed(
    tmp_path: Path,
) -> None:
    material, _paired_runtime, _held_singleton, _lock_path = (
        _singleton_transmission_fixture(tmp_path)
    )
    candidate_path = Path(
        material["outer_materialization_authority"]["candidate_manifest"]["path"]
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    contract_path = Path(
        candidate["bindings"]["package_process_singleton_contract"]["path"]
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    hostile_bool = copy.deepcopy(contract)
    hostile_bool["lock"]["required_nlink"] = True
    with pytest.raises(EVALUATOR.EvaluationError, match="exact JSON type mismatch"):
        EVALUATOR._audit_process_singleton_contract_payload(hostile_bool)
    hostile_key = copy.deepcopy(contract)
    hostile_key["unexpected"] = False
    with pytest.raises(EVALUATOR.EvaluationError, match="keyset mismatch"):
        EVALUATOR._audit_process_singleton_contract_payload(hostile_key)


def _singleton_args(lock: Path) -> SimpleNamespace:
    return SimpleNamespace(
        fixture_mode=False,
        controlled_singleton_lock=str(lock),
        expected_controlled_singleton_lock_sha256=_sha(lock),
    )


def test_controlled_singleton_conflict_is_exclusive_nonblocking(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "CONTROLLED_SINGLETON.lock"
    lock.write_bytes(b"controlled-real10k-20k-singleton\n")
    lock.chmod(0o444)
    descriptor = os.open(
        lock,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EVALUATOR.EvaluationError, match="already held"):
            with EVALUATOR._held_controlled_singleton(_singleton_args(lock)):
                pytest.fail("conflicting singleton lock unexpectedly entered")
    finally:
        os.close(descriptor)


def test_controlled_singleton_path_replacement_is_detected_before_unlock(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "CONTROLLED_SINGLETON.lock"
    original = b"controlled-real10k-20k-singleton\n"
    lock.write_bytes(original)
    lock.chmod(0o444)
    replaced = tmp_path / "original-singleton-inode"
    with pytest.raises(EVALUATOR.EvaluationError, match="descriptor/path identity changed"):
        with EVALUATOR._held_controlled_singleton(_singleton_args(lock)) as identity:
            assert set(identity) == {
                "schema",
                "path",
                "sha256",
                "size_bytes",
                "device",
                "inode",
                "nlink",
                "lock_mode",
            }
            lock.rename(replaced)
            lock.write_bytes(original)
            lock.chmod(0o444)


def test_controlled_singleton_identity_requires_exact_keyset_and_types(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "CONTROLLED_SINGLETON.lock"
    lock.write_bytes(b"controlled-real10k-20k-singleton\n")
    lock.chmod(0o444)
    with EVALUATOR._held_controlled_singleton(_singleton_args(lock)) as identity:
        assert EVALUATOR._validate_controlled_singleton_identity(
            identity, "fixture singleton"
        ) == identity
        hostile = dict(identity)
        hostile["nlink"] = True
        with pytest.raises(EVALUATOR.EvaluationError, match="exact JSON integer"):
            EVALUATOR._validate_controlled_singleton_identity(
                hostile, "hostile singleton"
            )
        hostile = dict(identity)
        hostile["extra"] = 0
        with pytest.raises(EVALUATOR.EvaluationError, match="keyset mismatch"):
            EVALUATOR._validate_controlled_singleton_identity(
                hostile, "hostile singleton"
            )
