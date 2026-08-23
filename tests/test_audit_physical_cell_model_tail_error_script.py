import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_cell_model_tail_error.py"
    spec = importlib.util.spec_from_file_location("audit_physical_cell_model_tail_error_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path, errors: list[float], row_counts: list[int] | None = None):
    lower = [0.5, 0.5, 5.0, 0.0]
    spans = [2.5, 2.5, 20.0, 0.8]
    columns = [
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
    ]
    names = [column.removeprefix("input__") for column in columns]
    counts = row_counts or [2] * len(errors)
    assert len(counts) == len(errors)
    rows = []
    cells = []
    row_id = 0
    for cell_number, (error, count) in enumerate(zip(errors, counts)):
        cell = (
            cell_number % 4,
            (cell_number // 4) % 4,
            (cell_number // 2) % 4,
            (cell_number // 3) % 4,
        )
        cells.append(":".join(str(value) for value in cell))
        target = [lower[index] + (cell[index] + 0.5) * spans[index] / 4.0 for index in range(4)]
        for _ in range(count):
            row = {"test_index": row_id, "matrix_index": row_id, "source_row_index": row_id}
            for index, name in enumerate(names):
                row[f"target__{name}"] = target[index]
                row[f"forward__{name}"] = target[index] + 0.5 * error * spans[index]
                row[f"reconstructed__{name}"] = target[index] + error * spans[index]
            rows.append(row)
            row_id += 1
    prediction_path = tmp_path / "predictions.csv"
    with prediction_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    rmse = math.sqrt(sum(error * error * count for error, count in zip(errors, counts)) / sum(counts))
    summary = {
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "input_columns": columns,
        "split_audit": {
            "split_mode": "physical_cell_grouped",
            "physical_cell_bins_per_dimension": 4,
            "physical_cell_lower": lower,
            "physical_cell_upper": [lower[index] + spans[index] for index in range(4)],
            "row_counts": {"train": 100, "validation": 20, "test": len(rows)},
            "cell_ids": {"train": ["3:3:3:3"], "validation": ["2:2:2:2"], "test": cells},
            "split_fingerprint_sha256": "a" * 64,
            "physical_cell_partition_fingerprint_sha256": "b" * 64,
        },
        "metrics": {
            "test_row_count": len(rows),
            "tandem_inverse": {"test_response_range_normalized_rmse": rmse},
            "range_normalization": {
                "source": "declared_physical_cell_range",
                "feature_span": {column: spans[index] for index, column in enumerate(columns)},
            },
        },
    }
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary_path, prediction_path, summary, rows


def _run(module, tmp_path: Path, summary_path: Path, prediction_path: Path, *, no_fail: bool = False) -> int:
    args = [
        "--model-summary",
        str(summary_path),
        "--predictions-csv",
        str(prediction_path),
        "--out-dir",
        str(tmp_path / "out"),
        "--minimum-test-rows",
        "16",
        "--minimum-test-cells",
        "8",
    ]
    if no_fail:
        args.append("--no-fail-exit")
    return module.main(args)


def test_complete_physical_cell_tail_audit_generates_metrics_and_plot(tmp_path):
    module = _load_module()
    summary_path, predictions, _, _ = _fixture(tmp_path, [0.1] * 8)
    assert _run(module, tmp_path, summary_path, predictions) == 0
    result = json.loads((tmp_path / "out" / "physical_cell_model_tail_error_summary.json").read_text())
    assert result["overall_status"] == "PASS"
    assert result["test_row_count"] == 16
    assert result["test_physical_cell_count"] == 8
    assert result["metrics"]["row_weighted_response_range_normalized_rmse"] == pytest.approx(0.1)
    assert result["metrics"]["equal_cell_response_range_normalized_rmse"] == pytest.approx(0.1)
    assert len(result["contract"]["fingerprint_sha256"]) == 64
    assert Path(result["artifacts"]["distribution_plot"]).stat().st_size > 0
    assert Path(result["artifacts"]["cell_metrics_csv"]).is_file()


def test_tail_audit_rejects_truncated_prediction_csv(tmp_path):
    module = _load_module()
    summary_path, predictions, _, rows = _fixture(tmp_path, [0.1] * 8)
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows[:-1])
    assert _run(module, tmp_path, summary_path, predictions, no_fail=True) == 0
    result = json.loads((tmp_path / "out" / "physical_cell_model_tail_error_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert any("expected complete test rows" in item for item in result["errors"])


def test_tail_audit_exposes_sparse_cell_regression_hidden_by_dense_average(tmp_path):
    module = _load_module()
    summary_path, predictions, _, _ = _fixture(
        tmp_path,
        [0.01] + [0.30] * 7,
        [100] + [2] * 7,
    )
    assert _run(module, tmp_path, summary_path, predictions) == 0
    result = json.loads((tmp_path / "out" / "physical_cell_model_tail_error_summary.json").read_text())
    metrics = result["metrics"]
    assert metrics["equal_cell_response_range_normalized_rmse"] > 2.0 * metrics[
        "row_weighted_response_range_normalized_rmse"
    ]
    assert metrics["cell_response_range_normalized_rmse_max"] == 0.3
    assert result["worst_cell"]["physical_cell_id"] != "0:0:0:0"


def test_tail_audit_rejects_cell_set_not_matching_split_manifest(tmp_path):
    module = _load_module()
    summary_path, predictions, summary, _ = _fixture(tmp_path, [0.1] * 8)
    summary["split_audit"]["cell_ids"]["test"][0] = "3:3:3:3"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    assert _run(module, tmp_path, summary_path, predictions, no_fail=True) == 0
    result = json.loads((tmp_path / "out" / "physical_cell_model_tail_error_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert any("physical cells differ" in item for item in result["errors"])
