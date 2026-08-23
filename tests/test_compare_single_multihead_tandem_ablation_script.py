import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np


INPUT_COLUMNS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]
GEOMETRY_COLUMNS = [f"geom__g{index}" for index in range(10)]


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "compare_single_multihead_tandem_ablation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "compare_single_multihead_tandem_ablation_script", path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shared_arguments() -> dict:
    return {
        "validation_fraction": 0.15,
        "test_fraction": 0.10,
        "split_mode": "physical_cell_grouped",
        "physical_cell_bins": 4,
        "physical_cell_lower": "0.5,0.5,5,0",
        "physical_cell_upper": "3,3,25,0.8",
        "seed": 20260712,
        "split_seed": 20260711,
        "forward_depth": 2,
        "forward_width": 32,
        "inverse_depth": 2,
        "inverse_width": 32,
        "batch_size": 64,
        "forward_epochs": 20,
        "inverse_epochs": 20,
        "patience": 5,
        "learning_rate": 0.001,
        "weight_decay": 1.0e-6,
        "geometry_anchor_weight": 0.01,
        "topology_feasibility_weight": 0.01,
        "response_loss_scaling": "declared_range",
        "response_loss_family": "mse",
        "normalization_floor": 1.0e-12,
    }


def _write_predictions(
    single_path: Path,
    multi_path: Path,
    *,
    cells: int = 8,
    rows_per_cell: int = 2,
    single_error: float = 0.20,
    multi_error: float = 0.10,
) -> tuple[float, float, int]:
    lower = np.asarray([0.5, 0.5, 5.0, 0.0])
    spans = np.asarray([2.5, 2.5, 20.0, 0.8])
    feature_names = [column.removeprefix("input__") for column in INPUT_COLUMNS]
    single_rows = []
    multi_rows = []
    row_index = 0
    for cell_index in range(cells):
        cell = np.asarray(
            [
                cell_index % 4,
                (cell_index // 4) % 4,
                (cell_index // 2) % 4,
                (cell_index // 3) % 4,
            ]
        )
        target = lower + (cell + 0.5) * spans / 4.0
        for _ in range(rows_per_cell):
            identity = hashlib.sha256(f"geometry-{row_index}".encode()).hexdigest()
            single = {
                "test_index": row_index,
                "matrix_index": row_index,
                "source_row_index": row_index,
                "source_geometry_identity_sha256": identity,
            }
            for feature_index, name in enumerate(feature_names):
                single[f"target__{name}"] = target[feature_index]
                single[f"reconstructed__{name}"] = (
                    target[feature_index] + single_error * spans[feature_index]
                )
            single_rows.append(single)
            winner = row_index % 2
            for head in range(2):
                candidate = {
                    "test_index": row_index,
                    "matrix_index": row_index,
                    "source_row_index": row_index,
                    "source_geometry_identity_sha256": identity,
                    "head_index": head,
                    "selected_best_of_k": head == winner,
                }
                error = multi_error if head == winner else multi_error + 0.15
                for feature_index, name in enumerate(feature_names):
                    candidate[f"target__{name}"] = target[feature_index]
                    candidate[f"reconstructed__{name}"] = (
                        target[feature_index] + error * spans[feature_index]
                    )
                multi_rows.append(candidate)
            row_index += 1
    for path, rows in ((single_path, single_rows), (multi_path, multi_rows)):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return single_error, multi_error, row_index


def _write_fixture(
    tmp_path: Path,
    *,
    training_count: int = 100_000,
    change_forward_weights: bool = False,
) -> dict[str, Path]:
    training = tmp_path / "training.csv"
    training.write_text("evaluation,value\na,1\n", encoding="utf-8")
    training_sha = _sha256(training)
    single_predictions = tmp_path / "single.csv"
    multi_candidates = tmp_path / "multi.csv"
    single_rmse, multi_rmse, test_count = _write_predictions(
        single_predictions, multi_candidates
    )
    single_weights = tmp_path / "single.npz"
    multi_weights = tmp_path / "multi.npz"
    np.savez_compressed(
        single_weights,
        forward_weight_0=np.asarray([[1.0, 2.0]]),
        forward_bias_0=np.asarray([0.0]),
        inverse_weight_0=np.asarray([[1.0]]),
        inverse_bias_0=np.asarray([0.0]),
    )
    np.savez_compressed(
        multi_weights,
        forward_weight_0=np.asarray([[1.0, 3.0] if change_forward_weights else [1.0, 2.0]]),
        forward_bias_0=np.asarray([0.0]),
        inverse_weight_0=np.asarray([[1.0], [2.0]]),
        inverse_bias_0=np.asarray([0.0]),
    )
    split = {
        "split_fingerprint_sha256": "a" * 64,
        "physical_cell_partition_fingerprint_sha256": "b" * 64,
        "physical_cell_bins_per_dimension": 4,
        "physical_cell_lower": [0.5, 0.5, 5.0, 0.0],
        "physical_cell_upper": [3.0, 3.0, 25.0, 0.8],
    }
    isolation = {
        "overall_status": "PASS",
        "checks": {
            "all_rows_assigned_once": True,
            "no_geometry_identity_overlap_across_splits": True,
            "all_splits_nonempty": True,
        },
    }
    base = {
        "overall_status": "COMPLETE_REVIEW_REQUIRED",
        "training_count": training_count,
        "training_csv": str(training),
        "training_csv_sha256": training_sha,
        "input_columns": INPUT_COLUMNS,
        "geometry_columns": GEOMETRY_COLUMNS,
        "split_audit": split,
        "evaluation_isolation": isolation,
        "arguments": _shared_arguments(),
    }
    single = {
        **base,
        "weights_npz": str(single_weights),
        "test_predictions_csv_sha256": _sha256(single_predictions),
        "metrics": {
            "test_row_count": test_count,
            "tandem_inverse": {"test_response_range_normalized_rmse": single_rmse},
        },
    }
    multi = {
        **base,
        "weights_npz": str(multi_weights),
        "test_candidates_csv_sha256": _sha256(multi_candidates),
        "method": {"head_count": 2},
        "metrics": {
            "test_row_count": test_count,
            "best_of_k": {"response_range_normalized_rmse": multi_rmse},
            "diversity": {
                "head_utilization_counts": [test_count // 2, test_count // 2],
                "head_utilization_entropy": 1.0,
            },
        },
    }
    single_summary = tmp_path / "single.json"
    multi_summary = tmp_path / "multi.json"
    single_summary.write_text(json.dumps(single), encoding="utf-8")
    multi_summary.write_text(json.dumps(multi), encoding="utf-8")
    return {
        "single_summary": single_summary,
        "multi_summary": multi_summary,
        "single_predictions": single_predictions,
        "multi_candidates": multi_candidates,
        "single_weights": single_weights,
        "multi_weights": multi_weights,
    }


def _arguments(paths: dict[str, Path], out_dir: Path) -> list[str]:
    return [
        "--single-summary",
        str(paths["single_summary"]),
        "--multihead-summary",
        str(paths["multi_summary"]),
        "--single-predictions",
        str(paths["single_predictions"]),
        "--multihead-candidates",
        str(paths["multi_candidates"]),
        "--out-dir",
        str(out_dir),
    ]


def test_formal_fixture_reviews_multihead_for_real_emx_closure(tmp_path):
    module = _load_module()
    paths = _write_fixture(tmp_path)
    out_dir = tmp_path / "out"
    status = module.main(
        _arguments(paths, out_dir)
        + [
            "--minimum-paired-test-rows",
            "16",
            "--minimum-paired-test-cells",
            "8",
            "--bootstrap-replicates",
            "200",
        ]
    )

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_ablation_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["formal_evidence"] is True
    assert summary["decision"] == "REVIEW_MULTIHEAD_FOR_FIXED_BUDGET_REAL_EMX_CLOSURE"
    assert all(summary["checks"].values())
    assert all(summary["review_gates"].values())
    assert summary["metrics"]["multihead_relative_improvement"] == 0.5
    assert Path(summary["artifacts"]["report"]).is_file()


def test_small_real_emx_style_fixture_is_interface_only(tmp_path):
    module = _load_module()
    paths = _write_fixture(tmp_path, training_count=55)
    out_dir = tmp_path / "out"
    status = module.main(_arguments(paths, out_dir))

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_ablation_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["formal_evidence"] is False
    assert summary["decision"] == "INTERFACE_ONLY_NO_MODEL_PROMOTION"


def test_rejects_changed_frozen_forward_weights(tmp_path):
    module = _load_module()
    paths = _write_fixture(tmp_path, change_forward_weights=True)
    out_dir = tmp_path / "out"
    status = module.main(_arguments(paths, out_dir) + ["--no-fail-exit"])

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_ablation_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["forward_weights_exist_and_match_exactly"] is False


def test_rejects_declared_training_hash_mismatch(tmp_path):
    module = _load_module()
    paths = _write_fixture(tmp_path)
    multi = json.loads(paths["multi_summary"].read_text())
    multi["training_csv_sha256"] = "f" * 64
    paths["multi_summary"].write_text(json.dumps(multi), encoding="utf-8")
    out_dir = tmp_path / "out"
    status = module.main(_arguments(paths, out_dir) + ["--no-fail-exit"])

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_ablation_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["same_training_csv_sha256"] is False
    assert summary["checks"]["actual_training_csv_matches_summary_sha256"] is False


def test_rejects_target_mismatch_in_selected_candidate(tmp_path):
    module = _load_module()
    paths = _write_fixture(tmp_path)
    rows = list(csv.DictReader(paths["multi_candidates"].open(encoding="utf-8")))
    selected = next(row for row in rows if row["selected_best_of_k"] == "True")
    selected["target__lp_nh_center"] = str(float(selected["target__lp_nh_center"]) + 0.01)
    with paths["multi_candidates"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    multi = json.loads(paths["multi_summary"].read_text())
    multi["test_candidates_csv_sha256"] = _sha256(paths["multi_candidates"])
    paths["multi_summary"].write_text(json.dumps(multi), encoding="utf-8")
    out_dir = tmp_path / "out"
    status = module.main(_arguments(paths, out_dir) + ["--no-fail-exit"])

    assert status == 0
    summary = json.loads(
        (out_dir / "single_multihead_tandem_ablation_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["paired_test_rows_complete_and_identical"] is False
    assert any("target values differ" in item for item in summary["contract_errors"])
