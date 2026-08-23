import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_balanced_mse_bni_ablation.py"
    spec = importlib.util.spec_from_file_location("compare_balanced_mse_bni_ablation_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _budget(module) -> dict:
    values = {
        "seed": 20260711,
        "split_seed": 20260712,
        "validation_fraction": 0.15,
        "test_fraction": 0.10,
        "split_mode": "physical_cell_grouped",
        "physical_cell_bins": 4,
        "physical_cell_lower": "0.5,0.5,5,0",
        "physical_cell_upper": "3,3,25,0.8",
        "forward_depth": 3,
        "forward_width": 256,
        "inverse_depth": 3,
        "inverse_width": 256,
        "batch_size": 1024,
        "forward_epochs": 160,
        "inverse_epochs": 180,
        "patience": 20,
        "learning_rate": 0.001,
        "weight_decay": 1.0e-6,
        "response_weight": 1.0,
        "geometry_anchor_weight": 0.01,
        "topology_feasibility_weight": 0.02,
        "response_ramp_fraction": 0.20,
        "response_loss_scaling": "declared_range",
        "response_weight_schedule": "warmup_ramp_adaptive_ema",
        "response_warmup_fraction": 0.05,
        "response_adaptive_ema_decay": 0.95,
        "response_adaptive_min_multiplier": 0.25,
        "response_adaptive_max_multiplier": 4.0,
        "normalization_floor": 1.0e-12,
    }
    assert set(module.REQUIRED_BUDGET_FIELDS) == set(values)
    return values


def _summary(module, family: str, response_rmse: float, *, tau: float = 0.08, training_sha: str = "a" * 64) -> dict:
    budget = _budget(module)
    budget.update(
        {
            "response_loss_family": family,
            "balanced_mse_temperature": tau if family == "balanced_mse_bni" else None,
        }
    )
    response_loss = {"family": family, "balanced_mse_bni": None}
    if family == "balanced_mse_bni":
        response_loss["balanced_mse_bni"] = {
            "enabled": True,
            "validation_or_test_rows_used_in_prior": False,
            "temperature_tau": tau,
        }
    return {
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "training_count": 100_000,
        "training_csv_sha256": training_sha,
        "input_columns": [
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ],
        "geometry_columns": ["geom__width_um", "geom__spacing_um"],
        "response_loss_contract": response_loss,
        "arguments": budget,
        "split_audit": {
            "split_fingerprint_sha256": "b" * 64,
            "physical_cell_partition_fingerprint_sha256": "c" * 64,
        },
        "metrics": {
            "tandem_inverse": {"test_response_range_normalized_rmse": response_rmse},
            "per_feature_range_normalized_mae": {"input__lp_nh_center": response_rmse / 2.0},
            "range_normalization": {
                "source": "declared_physical_cell_range",
                "feature_span": {
                    "input__lp_nh_center": 2.5,
                    "input__ls_nh_center": 2.5,
                    "input__q_center": 20.0,
                    "input__k_abs_center": 0.8,
                },
            },
            "test_row_count": 16,
        },
    }


def _write_predictions(path: Path, cell_errors: list[float]) -> float:
    lower = [0.5, 0.5, 5.0, 0.0]
    spans = [2.5, 2.5, 20.0, 0.8]
    names = ["lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"]
    rows = []
    row_index = 0
    for cell_number, error in enumerate(cell_errors):
        cell = (
            cell_number % 4,
            (cell_number // 4) % 4,
            (cell_number // 2) % 4,
            (cell_number // 3) % 4,
        )
        target = [lower[index] + (cell[index] + 0.5) * spans[index] / 4.0 for index in range(4)]
        for _ in range(2):
            row = {"matrix_index": row_index, "source_row_index": row_index}
            for index, name in enumerate(names):
                row[f"target__{name}"] = target[index]
                row[f"reconstructed__{name}"] = target[index] + error * spans[index]
            rows.append(row)
            row_index += 1
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return math.sqrt(sum(error * error for error in cell_errors) / len(cell_errors))


def _write_selection(module, path: Path, mse_path: Path, *, tau: float = 0.08) -> None:
    selection = {
        "overall_status": "PASS",
        "selected_temperature_tau": tau,
        "test_metrics_used": False,
        "test_predictions_used": False,
        "hyperparameter_sweep_performed": False,
        "provenance": {
            "mse_summary_sha256": module._sha256_file(mse_path),
            "training_csv_sha256": "a" * 64,
            "split_fingerprint_sha256": "b" * 64,
        },
    }
    path.write_text(json.dumps(selection), encoding="utf-8")


def _run(module, tmp_path: Path, mse_errors: list[float], bni_errors: list[float], *, mutate=None):
    mse_predictions = tmp_path / "mse.csv"
    bni_predictions = tmp_path / "bni.csv"
    mse_rmse = _write_predictions(mse_predictions, mse_errors)
    bni_rmse = _write_predictions(bni_predictions, bni_errors)
    mse_path = tmp_path / "mse.json"
    bni_path = tmp_path / "bni.json"
    mse = _summary(module, "mse", mse_rmse)
    bni = _summary(module, "balanced_mse_bni", bni_rmse)
    if mutate is not None:
        mutate(mse, bni)
    mse_path.write_text(json.dumps(mse), encoding="utf-8")
    bni_path.write_text(json.dumps(bni), encoding="utf-8")
    selection_path = tmp_path / "selection.json"
    _write_selection(module, selection_path, mse_path)
    status = module.main(
        [
            "--mse-summary",
            str(mse_path),
            "--bni-summary",
            str(bni_path),
            "--mse-predictions",
            str(mse_predictions),
            "--bni-predictions",
            str(bni_predictions),
            "--temperature-selection",
            str(selection_path),
            "--minimum-paired-test-rows",
            "16",
            "--minimum-paired-test-cells",
            "8",
            "--bootstrap-replicates",
            "500",
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )
    result = json.loads((tmp_path / "out" / "balanced_mse_bni_ablation_summary.json").read_text())
    return status, result


def test_recommends_bni_review_only_when_row_equal_cell_and_tail_cis_clear_gate(tmp_path):
    module = _load_module()
    status, result = _run(module, tmp_path, [0.20] * 8, [0.10] * 8)
    assert status == 0
    assert result["overall_status"] == "PASS"
    assert result["decision"] == "REVIEW_BNI_FOR_REAL_EMX_CLOSURE"
    bootstrap = result["paired_cluster_bootstrap"]
    assert bootstrap["relative_improvement_ci_lower"] >= 0.05
    assert bootstrap["cell_balanced_relative_improvement_ci_lower"] >= 0.05
    assert bootstrap["p90_tail_relative_improvement_ci_lower"] >= 0.05
    assert bootstrap["arm_aliases"] == {"anchored": "mse", "response_only": "balanced_mse_bni"}


def test_point_improvement_with_tail_regression_retains_mse(tmp_path):
    module = _load_module()
    status, result = _run(module, tmp_path, [0.20] * 8, [0.10] * 6 + [0.30] * 2)
    assert status == 0
    assert result["overall_status"] == "PASS"
    assert result["bni_relative_improvement"] > 0.05
    assert result["paired_cluster_bootstrap"]["p90_tail_relative_improvement_point"] < 0.0
    assert result["decision"] != "REVIEW_BNI_FOR_REAL_EMX_CLOSURE"


def test_rejects_different_training_csv_content_sha(tmp_path):
    module = _load_module()

    def mutate(_mse, bni):
        bni["training_csv_sha256"] = "d" * 64

    status, result = _run(module, tmp_path, [0.20] * 8, [0.10] * 8, mutate=mutate)
    assert status == 0
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["same_training_csv_sha256"] is False


def test_rejects_different_optimizer_budget(tmp_path):
    module = _load_module()

    def mutate(_mse, bni):
        bni["arguments"]["batch_size"] = 512

    status, result = _run(module, tmp_path, [0.20] * 8, [0.10] * 8, mutate=mutate)
    assert status == 0
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["same_training_budget"] is False


def test_rejects_temperature_selection_that_used_test_evidence(tmp_path):
    module = _load_module()
    mse_predictions = tmp_path / "mse.csv"
    bni_predictions = tmp_path / "bni.csv"
    mse_rmse = _write_predictions(mse_predictions, [0.20] * 8)
    bni_rmse = _write_predictions(bni_predictions, [0.10] * 8)
    mse_path = tmp_path / "mse.json"
    bni_path = tmp_path / "bni.json"
    mse_path.write_text(json.dumps(_summary(module, "mse", mse_rmse)), encoding="utf-8")
    bni_path.write_text(json.dumps(_summary(module, "balanced_mse_bni", bni_rmse)), encoding="utf-8")
    selection_path = tmp_path / "selection.json"
    _write_selection(module, selection_path, mse_path)
    selection = json.loads(selection_path.read_text())
    selection["test_metrics_used"] = True
    selection_path.write_text(json.dumps(selection), encoding="utf-8")

    assert module.main(
        [
            "--mse-summary",
            str(mse_path),
            "--bni-summary",
            str(bni_path),
            "--mse-predictions",
            str(mse_predictions),
            "--bni-predictions",
            str(bni_predictions),
            "--temperature-selection",
            str(selection_path),
            "--minimum-paired-test-rows",
            "16",
            "--minimum-paired-test-cells",
            "8",
            "--bootstrap-replicates",
            "200",
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    ) == 0
    result = json.loads((tmp_path / "out" / "balanced_mse_bni_ablation_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["temperature_selection_used_no_test_evidence"] is False
