import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_tandem_geometry_anchor_ablation.py"
    spec = importlib.util.spec_from_file_location("compare_tandem_geometry_anchor_ablation_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _summary(anchor_weight: float, response_rmse: float, split: str = "a" * 64) -> dict:
    return {
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "training_count": 100_000,
        "input_columns": [
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ],
        "geometry_columns": ["geom__width_um", "geom__spacing_um"],
        "method": {
            "geometry_anchor_weight": anchor_weight,
            "geometry_label_used_in_inverse_objective": anchor_weight > 0.0,
            "topology_feasibility_weight": 0.02,
            "topology_feasibility_is_label_free": True,
        },
        "arguments": {"response_warmup_fraction": 0.05 if anchor_weight > 0.0 else 0.0},
        "split_audit": {
            "split_fingerprint_sha256": split,
            "physical_cell_partition_fingerprint_sha256": "b" * 64,
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


def _write_predictions(path: Path, cell_errors: list[float], *, rows_per_cell: int | list[int] = 2) -> float:
    lower = [0.5, 0.5, 5.0, 0.0]
    spans = [2.5, 2.5, 20.0, 0.8]
    names = ["lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"]
    rows = []
    row_index = 0
    row_counts = (
        [int(rows_per_cell)] * len(cell_errors)
        if isinstance(rows_per_cell, int)
        else [int(value) for value in rows_per_cell]
    )
    assert len(row_counts) == len(cell_errors) and all(value > 0 for value in row_counts)
    for cell_number, error in enumerate(cell_errors):
        cell = (
            cell_number % 4,
            (cell_number // 4) % 4,
            (cell_number // 2) % 4,
            (cell_number // 3) % 4,
        )
        target = [lower[index] + (cell[index] + 0.5) * spans[index] / 4.0 for index in range(4)]
        for _ in range(row_counts[cell_number]):
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
    return math.sqrt(
        sum(error * error * count for error, count in zip(cell_errors, row_counts)) / sum(row_counts)
    )


def test_recommends_review_when_response_only_materially_improves(tmp_path):
    module = _load_module()
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, 0.20)), encoding="utf-8")
    response_only.write_text(json.dumps(_summary(0.0, 0.16)), encoding="utf-8")

    status = module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["decision"] == "REVIEW_RESPONSE_ONLY_FOR_REAL_EMX_CLOSURE"
    assert summary["response_only_relative_improvement"] == pytest.approx(0.20)
    assert summary["checks"]["same_label_free_topology_feasibility_weight"] is True
    assert summary["shared_topology_feasibility_weight"] == 0.02
    assert Path(summary["artifacts"]["report"]).is_file()


def test_rejects_mismatched_physical_cell_split(tmp_path):
    module = _load_module()
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, 0.20, "a" * 64)), encoding="utf-8")
    response_only.write_text(json.dumps(_summary(0.0, 0.16, "c" * 64)), encoding="utf-8")

    status = module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "FIX_GEOMETRY_ANCHOR_ABLATION_CONTRACT"
    assert summary["checks"]["same_split_fingerprint"] is False


def test_rejects_nonzero_warmup_for_response_only_arm(tmp_path):
    module = _load_module()
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, 0.20)), encoding="utf-8")
    response_data = _summary(0.0, 0.16)
    response_data["arguments"]["response_warmup_fraction"] = 0.05
    response_only.write_text(json.dumps(response_data), encoding="utf-8")

    status = module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["response_only_has_no_zero_gradient_warmup"] is False


def test_rejects_mismatched_topology_feasibility_weight(tmp_path):
    module = _load_module()
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, 0.20)), encoding="utf-8")
    response_data = _summary(0.0, 0.16)
    response_data["method"]["topology_feasibility_weight"] = 0.0
    response_only.write_text(json.dumps(response_data), encoding="utf-8")

    status = module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--out-dir",
            str(tmp_path / "out"),
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["same_label_free_topology_feasibility_weight"] is False


def test_paired_physical_cell_bootstrap_requires_ci_lower_bound_for_review(tmp_path):
    module = _load_module()
    anchored_predictions = tmp_path / "anchored.csv"
    response_predictions = tmp_path / "response.csv"
    anchored_rmse = _write_predictions(anchored_predictions, [0.20] * 8)
    response_rmse = _write_predictions(response_predictions, [0.10] * 8)
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, anchored_rmse)), encoding="utf-8")
    response_only.write_text(json.dumps(_summary(0.0, response_rmse)), encoding="utf-8")

    status = module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--anchored-predictions",
            str(anchored_predictions),
            "--response-only-predictions",
            str(response_predictions),
            "--require-paired-bootstrap",
            "--minimum-paired-test-rows",
            "16",
            "--minimum-paired-test-cells",
            "8",
            "--bootstrap-replicates",
            "200",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    bootstrap = summary["paired_cluster_bootstrap"]
    assert bootstrap["status"] == "PASS"
    assert bootstrap["paired_test_row_count"] == 16
    assert bootstrap["paired_physical_cell_count"] == 8
    assert bootstrap["relative_improvement_ci_lower"] >= 0.05
    assert bootstrap["cell_balanced_relative_improvement_ci_lower"] >= 0.05
    assert bootstrap["p90_tail_relative_improvement_ci_lower"] >= 0.05
    assert bootstrap["anchored_physical_cell_tail"]["p90_rmse"] == pytest.approx(0.20)
    assert bootstrap["response_only_physical_cell_tail"]["p90_rmse"] == pytest.approx(0.10)
    assert summary["decision"] == "REVIEW_RESPONSE_ONLY_FOR_REAL_EMX_CLOSURE"
    assert summary["decision_rule"] == "paired_cluster_bootstrap_row_and_cell_balanced_ci_lower_ge_material_improvement"


def test_point_gain_without_cluster_ci_support_keeps_anchored_baseline(tmp_path):
    module = _load_module()
    anchored_predictions = tmp_path / "anchored.csv"
    response_predictions = tmp_path / "response.csv"
    anchored_rmse = _write_predictions(anchored_predictions, [0.20] * 8)
    response_rmse = _write_predictions(response_predictions, [0.10] * 4 + [0.22] * 4)
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, anchored_rmse)), encoding="utf-8")
    response_only.write_text(json.dumps(_summary(0.0, response_rmse)), encoding="utf-8")

    assert module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--anchored-predictions",
            str(anchored_predictions),
            "--response-only-predictions",
            str(response_predictions),
            "--require-paired-bootstrap",
            "--minimum-paired-test-rows",
            "16",
            "--minimum-paired-test-cells",
            "8",
            "--bootstrap-replicates",
            "2000",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    ) == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    assert summary["response_only_relative_improvement"] > 0.05
    assert summary["paired_cluster_bootstrap"]["relative_improvement_ci_lower"] < 0.05
    assert summary["paired_cluster_bootstrap"]["cell_balanced_relative_improvement_ci_lower"] < 0.05
    assert summary["decision"] == "RETAIN_ANCHORED_BASELINE_UNCERTAIN_RESPONSE_ONLY_GAIN"


def test_paired_bootstrap_rejects_misaligned_test_targets(tmp_path):
    module = _load_module()
    anchored_predictions = tmp_path / "anchored.csv"
    response_predictions = tmp_path / "response.csv"
    anchored_rmse = _write_predictions(anchored_predictions, [0.20] * 8)
    response_rmse = _write_predictions(response_predictions, [0.10] * 8)
    rows = list(csv.DictReader(response_predictions.open(newline="", encoding="utf-8")))
    rows[0]["target__lp_nh_center"] = str(float(rows[0]["target__lp_nh_center"]) + 0.01)
    with response_predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored.write_text(json.dumps(_summary(0.01, anchored_rmse)), encoding="utf-8")
    response_only.write_text(json.dumps(_summary(0.0, response_rmse)), encoding="utf-8")

    assert module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--anchored-predictions",
            str(anchored_predictions),
            "--response-only-predictions",
            str(response_predictions),
            "--require-paired-bootstrap",
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
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["paired_cluster_bootstrap"]["status"] == "FAIL"
    assert any("paired targets differ" in item for item in summary["paired_cluster_bootstrap"]["errors"])


def test_dense_cell_gain_cannot_hide_equal_cell_regression(tmp_path):
    module = _load_module()
    anchored_predictions = tmp_path / "anchored.csv"
    response_predictions = tmp_path / "response.csv"
    row_counts = [100] + [2] * 7
    anchored_rmse = _write_predictions(anchored_predictions, [0.20] * 8, rows_per_cell=row_counts)
    response_rmse = _write_predictions(
        response_predictions,
        [0.10] + [0.22] * 7,
        rows_per_cell=row_counts,
    )
    anchored = tmp_path / "anchored.json"
    response_only = tmp_path / "response_only.json"
    anchored_data = _summary(0.01, anchored_rmse)
    response_data = _summary(0.0, response_rmse)
    anchored_data["metrics"]["test_row_count"] = sum(row_counts)
    response_data["metrics"]["test_row_count"] = sum(row_counts)
    anchored.write_text(json.dumps(anchored_data), encoding="utf-8")
    response_only.write_text(json.dumps(response_data), encoding="utf-8")

    assert module.main(
        [
            "--anchored-summary",
            str(anchored),
            "--response-only-summary",
            str(response_only),
            "--anchored-predictions",
            str(anchored_predictions),
            "--response-only-predictions",
            str(response_predictions),
            "--require-paired-bootstrap",
            "--minimum-paired-test-rows",
            "100",
            "--minimum-paired-test-cells",
            "8",
            "--bootstrap-replicates",
            "1000",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    ) == 0
    summary = json.loads((tmp_path / "out" / "tandem_geometry_anchor_ablation_summary.json").read_text())
    bootstrap = summary["paired_cluster_bootstrap"]
    assert summary["response_only_relative_improvement"] > 0.05
    assert bootstrap["cell_balanced_relative_improvement_point"] < 0.0
    assert summary["decision"] == "RETAIN_ANCHORED_BASELINE_UNCERTAIN_RESPONSE_ONLY_GAIN"
