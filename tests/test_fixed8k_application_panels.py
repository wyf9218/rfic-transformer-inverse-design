from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.analysis.fixed8k_application_panels import (
    CORE_PANEL,
    EXTENDED_PANEL,
    FULL_PANEL,
    LEGACY_METRIC_ID,
    NORMALIZATION_SPANS,
    STRICT_METRIC_ID,
    SYMMETRIC_METRIC_ID,
    SYMMETRIC_JOINT_METRIC_ID,
    create_no_clobber_directory,
    derive_errors,
    existing_headline_gate,
    figure_sidecar,
    independent_metric_reproduction,
    joint_metrics_by_definition_rows,
    metric_definition_vectors,
    normalized_nearest_neighbor_distance,
    panel_membership,
    reconstruct_exact_training_response_cloud,
    report_headline_binding,
    require_sha256,
    sha256_file,
    tolerance_success_by_definition_rows,
    tolerance_success_rows,
    validate_figure_sidecar_sources,
    validate_target_prediction_matrices,
)
from rfic_transformer_inverse_design.model_splitting import (
    split_physical_feature_indices,
)


def _targets() -> np.ndarray:
    return np.asarray(
        [
            [1.0, 1.0, 15.0, 0.6],
            [1.8, 1.8, 9.0, 0.4],
            [2.5, 2.5, 22.0, 0.2],
        ],
        dtype=float,
    )


def _synthetic_reconciliation_fixture() -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    targets = np.tile(np.asarray([[1.0, 1.0, 10.0, 0.5]]), (8000, 1))
    predictions = targets.copy()
    masks = {
        CORE_PANEL.name: np.zeros(8000, dtype=bool),
        EXTENDED_PANEL.name: np.zeros(8000, dtype=bool),
        FULL_PANEL.name: np.ones(8000, dtype=bool),
    }
    masks[CORE_PANEL.name][:150] = True
    masks[EXTENDED_PANEL.name][:817] = True

    strict_indices: list[int] = []
    symmetric_only_indices: list[int] = []
    failed_indices: list[int] = []
    regions = (
        (0, 150, 97, 34),
        (150, 817, 354, 108),
        (817, 8000, 1763, 753),
    )
    for start, stop, strict_count, symmetric_only_count in regions:
        strict_indices.extend(range(start, start + strict_count))
        symmetric_only_indices.extend(
            range(start + strict_count, start + strict_count + symmetric_only_count)
        )
        failed_indices.extend(range(start + strict_count + symmetric_only_count, stop))

    e_lp = np.zeros(8000)
    e_q = np.zeros(8000)
    e_lp[strict_indices] = 0.10
    e_lp[symmetric_only_indices] = 0.14
    e_lp[failed_indices[:331]] = 0.10
    e_q[failed_indices[:331]] = 0.30
    e_lp[failed_indices[331:1451]] = 0.2492
    e_lp[failed_indices[1451:]] = 0.60
    predictions[:, 0] += e_lp * 2.5
    predictions[:, 2] += e_q * 20.0
    return targets, predictions, masks


def test_target_only_panel_filtering() -> None:
    targets = _targets()
    first, _, _ = panel_membership(targets)
    arbitrary_predictions = np.asarray([[99.0, 99.0, 99.0, 0.0]] * len(targets))
    _ = derive_errors(targets, arbitrary_predictions)
    second, _, _ = panel_membership(targets)
    for name in first:
        np.testing.assert_array_equal(first[name], second[name])


def test_exact_core_panel_limits() -> None:
    targets = np.asarray(
        [
            [0.5, 0.5, 10.0, 0.5],
            [1.0, 0.67, 20.0, 0.8],
            [1.0, 1.5, 15.0, 0.6],
            [1.500001, 1.0, 15.0, 0.6],
        ]
    )
    masks, _, _ = panel_membership(targets)
    assert masks[CORE_PANEL.name].tolist() == [True, True, True, False]


def test_exact_extended_panel_limits() -> None:
    targets = np.asarray(
        [
            [0.5, 0.5, 8.0, 0.3],
            [1.0, 0.5, 20.0, 0.8],
            [1.0, 2.0, 15.0, 0.4],
            [2.000001, 1.0, 15.0, 0.4],
        ]
    )
    masks, _, _ = panel_membership(targets)
    assert masks[EXTENDED_PANEL.name].tolist() == [True, True, True, False]


def test_full_panel_remains_exactly_8000() -> None:
    values = np.tile(np.asarray([[1.0, 1.0, 15.0, 0.6]]), (8000, 1))
    masks, _, _ = panel_membership(values)
    assert int(np.sum(masks[FULL_PANEL.name])) == 8000


def test_core_subset_of_extended() -> None:
    masks, _, _ = panel_membership(_targets())
    assert np.all(~masks[CORE_PANEL.name] | masks[EXTENDED_PANEL.name])


def test_extended_subset_of_full() -> None:
    masks, _, _ = panel_membership(_targets())
    assert np.all(~masks[EXTENDED_PANEL.name] | masks[FULL_PANEL.name])


def test_inductance_ratio_boundary_handling() -> None:
    targets = np.asarray(
        [
            [1.0, 0.67, 15.0, 0.6],
            [1.0, 1.5, 15.0, 0.6],
            [1.0, 0.669999, 15.0, 0.6],
            [1.0, 1.500001, 15.0, 0.6],
        ]
    )
    masks, _, _ = panel_membership(targets)
    assert masks[CORE_PANEL.name].tolist() == [True, True, False, False]


def test_no_filtering_by_achieved_values() -> None:
    targets = _targets()
    masks, _, _ = panel_membership(targets)
    achieved = np.asarray([[0.1, 0.1, 1.0, 0.0]] * len(targets))
    _ = derive_errors(targets, achieved)
    assert masks[CORE_PANEL.name].tolist() == [True, False, False]


def test_no_filtering_by_error() -> None:
    targets = _targets()
    masks, _, _ = panel_membership(targets)
    zero_error = derive_errors(targets, targets)
    huge_error = derive_errors(targets, targets + np.asarray([10.0, 10.0, 100.0, 10.0]))
    assert not np.array_equal(
        zero_error["joint_normalized_rmse"], huge_error["joint_normalized_rmse"]
    )
    assert masks[CORE_PANEL.name].tolist() == [True, False, False]


def test_frozen_normalization_spans_unchanged() -> None:
    np.testing.assert_array_equal(NORMALIZATION_SPANS, [2.5, 2.5, 20.0, 0.8])


def test_q_absolute_error_is_separate_from_q_shortfall() -> None:
    targets = np.asarray([[1.0, 1.0, 10.0, 0.5], [1.0, 1.0, 10.0, 0.5]])
    predictions = np.asarray([[1.0, 1.0, 14.0, 0.5], [1.0, 1.0, 7.0, 0.5]])
    errors = derive_errors(targets, predictions)
    np.testing.assert_allclose(errors["absolute"][:, 2], [4.0, 3.0])
    np.testing.assert_allclose(errors["q_shortfall"], [0.0, 3.0])


def test_dual_metric_definitions_remain_numerically_separate() -> None:
    targets = np.asarray([[1.0, 1.0, 10.0, 0.5]])
    predictions = np.asarray([[1.0, 1.0, 14.0, 0.5]])
    vectors = metric_definition_vectors(derive_errors(targets, predictions))
    assert vectors[LEGACY_METRIC_ID][0] == pytest.approx(0.0)
    assert vectors[SYMMETRIC_METRIC_ID][0] == pytest.approx(0.1)
    assert vectors[STRICT_METRIC_ID][0] == pytest.approx(0.2)


def test_independent_legacy_headline_reproduction() -> None:
    targets, predictions, masks = _synthetic_reconciliation_fixture()
    gate, _ = independent_metric_reproduction(targets, predictions, masks)
    legacy = gate["legacy_reproduction"]
    assert legacy["status"] == "PASS"
    assert legacy["denominator"] == 8000
    assert legacy["success_count_at_10pct"] == 3440
    assert legacy["rounded_median_percent"] == 12.46
    assert legacy["rounded_success_percent"] == 43.0


def test_independent_absolute_q_count_reproduction() -> None:
    targets, predictions, masks = _synthetic_reconciliation_fixture()
    gate, _ = independent_metric_reproduction(targets, predictions, masks)
    assert gate["status"] == "PASS"
    panels = gate["absolute_q_count_reproduction"]["panels"]
    expected = {
        CORE_PANEL.name: (150, 131, 97),
        EXTENDED_PANEL.name: (817, 593, 451),
        FULL_PANEL.name: (8000, 3109, 2214),
    }
    for panel_name, counts in expected.items():
        row = panels[panel_name]
        assert row["status"] == "PASS"
        assert (
            row["denominator"],
            row["symmetric_joint_rmse_le_10_count"],
            row["strict_all_feature_le_10_count"],
        ) == counts


def test_over_target_q_is_not_accidentally_mixed_between_metric_families() -> None:
    targets = np.asarray([[1.0, 1.0, 10.0, 0.5]])
    predictions = np.asarray([[1.0, 1.0, 18.0, 0.5]])
    errors = derive_errors(targets, predictions)
    rows = joint_metrics_by_definition_rows(
        {CORE_PANEL.name: np.asarray([True])}, errors
    )
    by_metric = {row["metric_family"]: row for row in rows}
    assert by_metric[LEGACY_METRIC_ID]["success_count_at_10pct"] == 1
    assert by_metric[LEGACY_METRIC_ID]["median"] == pytest.approx(0.0)
    assert by_metric[SYMMETRIC_METRIC_ID]["success_count_at_10pct"] == 0
    assert by_metric[SYMMETRIC_METRIC_ID]["median"] == pytest.approx(0.2)


def test_tolerance_table_preserves_expected_integer_success_counts() -> None:
    targets, predictions, masks = _synthetic_reconciliation_fixture()
    rows = tolerance_success_by_definition_rows(
        masks, derive_errors(targets, predictions), tolerances=(0.10,)
    )
    lookup = {
        (row["panel"], row["metric_definition"]): row for row in rows
    }
    expected = {
        CORE_PANEL.name: (150, 131, 97),
        EXTENDED_PANEL.name: (817, 593, 451),
        FULL_PANEL.name: (8000, 3109, 2214),
    }
    for panel_name, (denominator, joint_count, strict_count) in expected.items():
        joint = lookup[(panel_name, SYMMETRIC_JOINT_METRIC_ID)]
        strict = lookup[(panel_name, STRICT_METRIC_ID)]
        assert joint["denominator"] == denominator
        assert joint["success_count"] == joint_count
        assert strict["denominator"] == denominator
        assert strict["success_count"] == strict_count
    assert lookup[(FULL_PANEL.name, LEGACY_METRIC_ID)]["success_count"] == 3440


def test_report_headlines_are_bound_to_gated_counts() -> None:
    targets, predictions, masks = _synthetic_reconciliation_fixture()
    gate, _ = independent_metric_reproduction(targets, predictions, masks)
    headlines = report_headline_binding(gate)
    assert "131/150" in headlines[CORE_PANEL.name]
    assert "97/150" in headlines[CORE_PANEL.name]
    assert "593/817" in headlines[EXTENDED_PANEL.name]
    assert "451/817" in headlines[EXTENDED_PANEL.name]
    assert "3109/8000" in headlines[FULL_PANEL.name]
    assert "2214/8000" in headlines[FULL_PANEL.name]
    assert "3440/8000" in headlines["legacy_continuity"]
    assert "12.46%" in headlines["legacy_continuity"]


def test_joint_rmse_formula() -> None:
    targets = np.zeros((1, 4))
    predictions = NORMALIZATION_SPANS.reshape(1, 4) * 0.1
    errors = derive_errors(targets, predictions)
    assert errors["joint_normalized_rmse"][0] == pytest.approx(0.1)


def test_strict_all_feature_formula() -> None:
    targets = np.zeros((1, 4))
    predictions = np.asarray([[0.25, 0.5, 1.0, 0.16]])
    errors = derive_errors(targets, predictions)
    assert errors["strict_max_feature_error"][0] == pytest.approx(0.2)


def test_tolerance_success_counts() -> None:
    targets = np.zeros((2, 4))
    predictions = np.asarray(
        [NORMALIZATION_SPANS * 0.05, NORMALIZATION_SPANS * 0.20]
    )
    errors = derive_errors(targets, predictions)
    masks = {CORE_PANEL.name: np.asarray([True, True])}
    rows = tolerance_success_rows(masks, errors, tolerances=(0.10,))
    assert rows[0]["joint_rmse_success_count"] == 1
    assert rows[0]["strict_all_feature_success_count"] == 1


def test_target_id_alignment() -> None:
    values = np.zeros((2, 4))
    with pytest.raises(ValueError, match="aligned"):
        validate_target_prediction_matrices(["a", "b"], values, ["b", "a"], values)


def test_duplicate_target_rejection() -> None:
    values = np.zeros((2, 4))
    with pytest.raises(ValueError, match="duplicate"):
        validate_target_prediction_matrices(["a", "a"], values, ["a", "a"], values)


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_nan_inf_rejection(bad: float) -> None:
    targets = np.zeros((2, 4))
    predictions = np.zeros((2, 4))
    predictions[0, 0] = bad
    with pytest.raises(ValueError, match="NaN or Inf"):
        validate_target_prediction_matrices(["a", "b"], targets, ["a", "b"], predictions)


def test_training_response_identity_rejection() -> None:
    rng = np.random.default_rng(7)
    lower = np.asarray([0.5, 0.5, 5.0, 0.0])
    upper = np.asarray([3.0, 3.0, 25.0, 0.8])
    responses = rng.uniform(lower, upper, size=(120, 4))
    split, audit = split_physical_feature_indices(
        responses,
        mode="physical_cell_grouped",
        seed=11,
        validation_fraction=0.15,
        test_fraction=0.1,
        physical_cell_bins=4,
        physical_cell_lower=lower,
        physical_cell_upper=upper,
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        reconstruct_exact_training_response_cloud(
            responses,
            seed=11,
            validation_fraction=0.15,
            test_fraction=0.1,
            bins=4,
            lower=lower,
            upper=upper,
            expected_train_count=len(split["train"]),
            expected_train_index_sha256="0" * 64,
        )
    assert audit["split_index_sha256"]["train"] != "0" * 64


def test_support_distance_calculation() -> None:
    train = np.asarray([[0.5, 0.5, 5.0, 0.0], [3.0, 3.0, 25.0, 0.8]])
    targets = np.asarray([[0.5, 0.5, 5.0, 0.0], [1.75, 1.75, 15.0, 0.4]])
    distance = normalized_nearest_neighbor_distance(targets, train)
    assert distance[0] == pytest.approx(0.0)
    assert distance[1] == pytest.approx(1.0)


def test_existing_12p46_and_43p0_headline_recomputation_detects_semantic_mismatch() -> None:
    legacy = np.concatenate(
        [
            np.full(3440, 0.05),
            np.full(1120, 0.1246),
            np.full(3440, 0.30),
        ]
    )
    absolute_q = legacy + 0.02
    gate = existing_headline_gate(absolute_q, legacy)
    assert gate["legacy_reproduces_existing_display"] is True
    assert gate["legacy_q_shortfall_joint"]["rounded"] == {
        "median_percent": 12.46,
        "le_10_percent": 43.0,
    }
    assert gate["status"] == "MISMATCH"


def test_no_clobber_output_behavior(tmp_path: Path) -> None:
    output = tmp_path / "report"
    assert create_no_clobber_directory(output) == output.resolve()
    with pytest.raises(FileExistsError):
        create_no_clobber_directory(output)


def test_source_sha_binding(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("a\n1\n", encoding="utf-8")
    digest = sha256_file(source)
    assert require_sha256(source, digest) == digest
    source.write_text("a\n2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        require_sha256(source, digest)


def test_figure_source_csv_sha_binding(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("a\n1\n", encoding="utf-8")
    figure = tmp_path / "figure.png"
    figure.write_bytes(b"png")
    payload = figure_sidecar(
        figure_path=figure,
        source_files=[source],
        metadata={"denominator": 1},
    )
    validate_figure_sidecar_sources(payload)
    sidecar = tmp_path / "figure.png.json"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    source.write_text("a\n2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_figure_sidecar_sources(json.loads(sidecar.read_text(encoding="utf-8")))
