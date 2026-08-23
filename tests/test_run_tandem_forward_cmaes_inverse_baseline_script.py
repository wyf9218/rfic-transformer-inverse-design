import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


pytest.importorskip("cma")


INPUTS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
GEOMETRY = tuple(f"geom__g{index}" for index in range(4))
X_MEAN = np.asarray([1.75, 1.75, 15.0, 0.4])
X_SCALE = np.asarray([0.5, 0.5, 4.0, 0.15])


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_tandem_forward_cmaes_inverse_baseline.py"
    spec = importlib.util.spec_from_file_location("cmaes_inverse_baseline_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(root: Path, *, split_mode: str = "physical_cell_grouped") -> Path:
    weights = root / "weights.npz"
    np.savez_compressed(
        weights,
        forward_weight_0=np.eye(4),
        forward_bias_0=np.zeros(4),
        inverse_weight_0=np.eye(4),
        inverse_bias_0=np.zeros(4),
        normalization__x_mean=X_MEAN,
        normalization__x_scale=X_SCALE,
        normalization__y_mean=np.zeros(4),
        normalization__y_scale=np.ones(4),
        normalization__geometry_lower=-np.ones(4),
        normalization__geometry_upper=np.ones(4),
        normalization__response_loss_dimension_weights=np.ones(4),
    )
    targets = root / "targets.csv"
    normalized_targets = [
        np.asarray([0.7, -0.6, 0.5, -0.4]),
        np.asarray([-0.65, 0.55, -0.45, 0.35]),
        np.asarray([0.5, 0.45, -0.6, -0.55]),
        np.asarray([-0.4, -0.5, 0.65, 0.6]),
    ]
    rows = []
    for index, target_normalized in enumerate(normalized_targets):
        target_physical = target_normalized * X_SCALE + X_MEAN
        row: dict[str, object] = {
            "source_row_index": index,
            "source_evaluation": f"real-{index}",
            "source_geometry_identity_sha256": f"id-{index}",
        }
        for feature_index, column in enumerate(INPUTS):
            name = column.removeprefix("input__")
            row[f"target__{name}"] = target_physical[feature_index]
            row[f"reconstructed__{name}"] = X_MEAN[feature_index]
        for geometry_index, column in enumerate(GEOMETRY):
            row[f"paired_geometry__{column.removeprefix('geom__')}"] = target_normalized[geometry_index]
            row[f"predicted_geometry__{column.removeprefix('geom__')}"] = 0.0
        rows.append(row)
    _write_csv(targets, rows)
    summary = root / "tandem.json"
    summary.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "execution_status": "PASS",
                "weights_npz": str(weights),
                "test_predictions_csv": str(targets),
                "input_columns": list(INPUTS),
                "geometry_columns": list(GEOMETRY),
                "split_audit": {
                    "split_mode": split_mode,
                    "physical_cell_range_source": "explicit",
                    "physical_cell_overlap_count": 0,
                    "physical_cell_lower": [0.5, 0.5, 5.0, 0.0],
                    "physical_cell_upper": [3.0, 3.0, 25.0, 0.8],
                    "physical_cell_bins_per_dimension": 4,
                },
            }
        ),
        encoding="utf-8",
    )
    return summary


def test_cmaes_baseline_uses_same_targets_and_improves_frozen_proxy_score(tmp_path):
    module = _load_module()
    summary = _fixture(tmp_path)

    status = module.main(
        [
            "--tandem-summary", str(summary),
            "--out-dir", str(tmp_path / "out"),
            "--min-targets", "4",
            "--max-targets", "4",
            "--max-evaluations", "256",
            "--population-size", "16",
            "--seed", "41",
        ]
    )

    assert status == 0
    payload = json.loads((tmp_path / "out" / "tandem_forward_cmaes_inverse_summary.json").read_text())
    assert payload["overall_status"] == "PASS"
    assert payload["outcome_status"] == "SURROGATE_ONLY_NOT_REAL_EMX_VALIDATED"
    assert payload["eligible_for_model_success_claim"] is False
    assert payload["metrics"]["cmaes"]["range_normalized_rmse"] < payload["metrics"]["tandem"]["range_normalized_rmse"]
    assert payload["metrics"]["paired_proxy_row_improvement_fraction"] == pytest.approx(1.0)
    assert payload["checks"]["geometry_bounds_respected"] is True
    assert payload["method_reference"]["venue"] == "ASP-DAC 2026"


def test_random_split_is_rejected_for_formal_baseline(tmp_path):
    module = _load_module()
    summary = _fixture(tmp_path, split_mode="random")

    assert module.main(
        [
            "--tandem-summary", str(summary),
            "--out-dir", str(tmp_path / "out"),
            "--min-targets", "4",
            "--max-targets", "4",
            "--no-fail-exit",
        ]
    ) == 0
    payload = json.loads((tmp_path / "out" / "tandem_forward_cmaes_inverse_summary.json").read_text())
    assert payload["overall_status"] == "FAIL"
    assert payload["checks"]["physical_cell_ood_split"] is False
    assert payload["target_count"] == 0
