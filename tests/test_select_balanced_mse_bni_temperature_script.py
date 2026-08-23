import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "select_balanced_mse_bni_temperature.py"
    spec = importlib.util.spec_from_file_location("select_balanced_mse_bni_temperature_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_baseline(summary_path: Path, history_path: Path, *, family: str = "mse", best_epoch: int = 7) -> None:
    summary = {
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "training_csv_sha256": "a" * 64,
        "input_columns": [
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ],
        "response_loss_contract": {"family": family},
        "best_epochs": {"tandem_inverse": best_epoch},
        "split_audit": {
            "split_mode": "physical_cell_grouped",
            "physical_cell_range_source": "explicit",
            "split_fingerprint_sha256": "b" * 64,
            "physical_cell_partition_fingerprint_sha256": "c" * 64,
        },
        "metrics": {
            "tandem_inverse": {
                "test_response_range_normalized_rmse": 999.0,
            }
        },
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    rows = [
        {
            "stage": "tandem_inverse",
            "epoch": "6",
            "validation_feature_balanced_response_normalized_rmse": "0.3",
        },
        {
            "stage": "tandem_inverse",
            "epoch": str(best_epoch),
            "validation_feature_balanced_response_normalized_rmse": "0.2",
        },
    ]
    with history_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_selects_single_temperature_from_best_validation_epoch_without_test_evidence(tmp_path):
    module = _load_module()
    summary_path = tmp_path / "mse_summary.json"
    history_path = tmp_path / "history.csv"
    _write_baseline(summary_path, history_path)

    assert module.main(
        [
            "--mse-summary",
            str(summary_path),
            "--mse-history",
            str(history_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    ) == 0
    result = json.loads((tmp_path / "out" / "balanced_mse_bni_temperature_selection.json").read_text())
    assert result["overall_status"] == "PASS"
    assert result["selected_temperature_tau"] == pytest.approx(0.08)
    assert result["validation_rmse"] == pytest.approx(0.2)
    assert result["test_metrics_used"] is False
    assert result["test_predictions_used"] is False
    assert result["hyperparameter_sweep_performed"] is False
    assert result["provenance"]["mse_summary_sha256"] == module._sha256_file(summary_path)
    assert result["provenance"]["mse_history_sha256"] == module._sha256_file(history_path)


def test_rejects_non_mse_baseline(tmp_path):
    module = _load_module()
    summary_path = tmp_path / "summary.json"
    history_path = tmp_path / "history.csv"
    _write_baseline(summary_path, history_path, family="balanced_mse_bni")

    assert module.main(
        [
            "--mse-summary",
            str(summary_path),
            "--mse-history",
            str(history_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    ) == 0
    result = json.loads((tmp_path / "out" / "balanced_mse_bni_temperature_selection.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["baseline_loss_family_is_mse"] is False


def test_rejects_missing_best_epoch_history_row(tmp_path):
    module = _load_module()
    summary_path = tmp_path / "summary.json"
    history_path = tmp_path / "history.csv"
    _write_baseline(summary_path, history_path, best_epoch=9)
    rows = list(csv.DictReader(history_path.open(newline="", encoding="utf-8")))
    rows = [row for row in rows if row["epoch"] != "9"]
    with history_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stage", "epoch", "validation_feature_balanced_response_normalized_rmse"])
        writer.writeheader()
        writer.writerows(rows)

    assert module.main(
        [
            "--mse-summary",
            str(summary_path),
            "--mse-history",
            str(history_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    ) == 0
    result = json.loads((tmp_path / "out" / "balanced_mse_bni_temperature_selection.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["one_matching_validation_history_row"] is False
    assert result["selected_temperature_tau"] is None
