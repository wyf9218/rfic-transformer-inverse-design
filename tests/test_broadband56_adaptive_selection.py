from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_adaptive_selection import (
    PREDICTION_FEATURES,
    SOURCE_STATIC_WEIGHTS,
    compute_candidate_components,
    required_prediction_columns,
    select_source_quotas,
    selection_policy_contract,
    source_static_scores,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    PRIMARY_CELLS_PER_ANCHOR,
    primary_cell_for_values,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _prediction_fixture(count: int = 2) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    shape = (count, len(ANCHOR_FREQUENCIES_GHZ))
    predictions = {
        "xp_ohm": np.vstack((np.full(shape[1], 12.0), np.full(shape[1], 20.0)))[:count],
        "xs_ohm": np.full(shape, 12.0),
        "qp": np.full(shape, 5.0),
        "qs": np.full(shape, 6.0),
        "qmin": np.full(shape, 5.0),
        "k_abs": np.full(shape, 0.10),
        "feature_validity_probability": np.full(shape, 0.90),
    }
    uncertainties = {feature: np.zeros(shape, dtype=float) for feature in PREDICTION_FEATURES}
    return predictions, uncertainties


def _load_selector_module():
    path = ROOT / "scripts" / "select_broadband56_adaptive_candidates.py"
    spec = importlib.util.spec_from_file_location("select_broadband56_adaptive_candidates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_selection_policy_has_exact_traceable_prediction_columns_and_weights() -> None:
    policy = selection_policy_contract()

    assert policy["candidate_predictions_are_labels"] is False
    assert policy["selected_label_status"] == "AWAITING_FRESH_REAL_EMX"
    assert len(required_prediction_columns()) == 2 * len(PREDICTION_FEATURES) * len(ANCHOR_FREQUENCIES_GHZ)
    assert len(set(required_prediction_columns())) == len(required_prediction_columns())
    assert all(sum(weights.values()) == pytest.approx(1.0) for weights in SOURCE_STATIC_WEIGHTS.values())


def test_candidate_components_prioritize_real_emx_coverage_deficit_without_creating_labels() -> None:
    predictions, uncertainties = _prediction_fixture()
    deficits = np.zeros((len(ANCHOR_FREQUENCIES_GHZ), PRIMARY_CELLS_PER_ANCHOR), dtype=float)
    targets = np.full_like(deficits, 10.0)
    first_cell = primary_cell_for_values(anchor_ghz=8, xp_ohm=12.0, xs_ohm=12.0, qmin=5.0, k_abs=0.10)
    assert first_cell is not None
    deficits[:, first_cell.local_index] = 10.0

    components = compute_candidate_components(
        candidate_geometry_normalized=np.asarray([[0.05] * 10, [0.50] * 10]),
        accepted_geometry_normalized=np.asarray([[0.50] * 10]),
        predictions=predictions,
        uncertainties=uncertainties,
        coverage_deficits=deficits,
        coverage_targets=targets,
    )

    assert components["deficit_gain"].tolist() == pytest.approx([1.0, 0.0])
    assert components["boundary_coverage"][0] > components["boundary_coverage"][1]
    assert components["predicted_local_cells"].shape == (2, len(ANCHOR_FREQUENCIES_GHZ))
    assert source_static_scores(components)["underfilled_response_repair"][0] > 0.0


def test_candidate_components_reject_inconsistent_qmin() -> None:
    predictions, uncertainties = _prediction_fixture()
    predictions["qmin"][0, 0] = 4.0

    with pytest.raises(ValueError, match="qmin is inconsistent"):
        compute_candidate_components(
            candidate_geometry_normalized=np.asarray([[0.05] * 10, [0.50] * 10]),
            accepted_geometry_normalized=np.asarray([[0.50] * 10]),
            predictions=predictions,
            uncertainties=uncertainties,
            coverage_deficits=np.zeros((len(ANCHOR_FREQUENCIES_GHZ), PRIMARY_CELLS_PER_ANCHOR)),
            coverage_targets=np.ones((len(ANCHOR_FREQUENCIES_GHZ), PRIMARY_CELLS_PER_ANCHOR)),
        )


def test_source_quota_selection_is_exact_disjoint_and_deterministic() -> None:
    candidate = np.asarray([[index / 31.0] * 10 for index in range(1, 21)], dtype=float)
    accepted = np.asarray([[0.0] * 10, [1.0] * 10], dtype=float)
    hashes = [f"{index:064x}" for index in range(1, 21)]
    quotas = {"underfilled_response_repair": 3, "maximin_geometry_exploration": 2}
    static = {"underfilled_response_repair": np.linspace(0.0, 1.0, len(candidate))}

    first = select_source_quotas(
        candidate_geometry_normalized=candidate,
        accepted_geometry_normalized=accepted,
        geometry_hashes=hashes,
        source_quotas=quotas,
        static_scores=static,
    )
    second = select_source_quotas(
        candidate_geometry_normalized=candidate,
        accepted_geometry_normalized=accepted,
        geometry_hashes=hashes,
        source_quotas=quotas,
        static_scores=static,
    )

    assert first["selected_count"] == 5
    assert first["selected_counts_by_source"] == quotas
    assert len(set(first["selected_indices"])) == 5
    assert first["selected_indices"] == second["selected_indices"]
    assert first["assignments"] == second["assignments"]


def test_selector_cli_fails_closed_without_round_evidence_or_candidate_pool(tmp_path: Path) -> None:
    module = _load_selector_module()
    out_dir = tmp_path / "failed_selection"

    status = module.main(
        [
            "--contract",
            str(CONTRACT),
            "--round-dir",
            str(tmp_path / "missing_round"),
            "--candidate-csv",
            str(tmp_path / "missing_candidates.csv"),
            "--out-dir",
            str(out_dir),
        ]
    )

    summary = json.loads((out_dir / "ADAPTIVE_SELECTION_SUMMARY.json").read_text(encoding="utf-8"))
    receipt = json.loads((out_dir / "ADAPTIVE_SELECTION_RECEIPT.json").read_text(encoding="utf-8"))
    assert status == 2
    assert summary["overall_status"] == "FAIL"
    assert summary["decision"] == "DO_NOT_RUN_CADENCE_CALIBRE_OR_EMX"
    assert receipt["overall_status"] == "FAIL"
    assert not (out_dir / "broadband56_adaptive_candidate_queue.csv").exists()


def test_coverage_loader_rejects_inconsistent_deficit(tmp_path: Path) -> None:
    module = _load_selector_module()
    coverage_path = tmp_path / "coverage.csv"
    with coverage_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "anchor_ghz",
                "local_cell_index",
                "conditioned_cell_index",
                "actual_count",
                "target_count",
                "deficit",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "anchor_ghz": ANCHOR_FREQUENCIES_GHZ[0],
                "local_cell_index": 0,
                "conditioned_cell_index": 0,
                "actual_count": 1,
                "target_count": 2,
                "deficit": 2,
            }
        )

    result = module._load_coverage_cells(coverage_path)
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["coverage_cells_valid"]["pass"] is False
    assert "invalid target/deficit" in checks["coverage_cells_valid"]["detail"][0]
