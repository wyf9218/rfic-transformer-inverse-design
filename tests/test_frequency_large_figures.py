"""Synthetic chart-contract tests; no model inference, private data or EMX."""
import json

import numpy as np
import pytest

from research.broadband56_nn.frequency_large_figures import (
    FEATURES, _frozen_edges, _group_arrays, fixed_attainment, normalized_matrix,
    target_cells, render_large_figures,
)


def test_fixed_denominator_retains_nonfinite_failures():
    x, y = fixed_attainment([.1, .5, float("nan"), 2], 5)
    assert x.tolist() == [.1, .5, 2]
    assert y.tolist() == [.2, .4, .6]


def test_attainment_rejects_denominator_shrink_and_negative():
    with pytest.raises(ValueError): fixed_attainment([1, 2], 1)
    with pytest.raises(ValueError): fixed_attainment([1], 0)
    with pytest.raises(ValueError): fixed_attainment([-1], 1)


def _group(panel="A", source="EXISTING_EMX_FORWARD", stage="reference", frequency=15, value=.25):
    return dict(panel=panel, evaluation_source=source, geometry_stage=stage, frequency_ghz=frequency,
                label_mode="STRICT_LUMPED", features={f: {"MAE_normalized": value} for f in FEATURES})


def test_heatmap_missing_not_zero_and_sources_not_mixed():
    groups = [_group(value=0), _group("B", "SELF_PROXY", "grid", value=9)]
    m = normalized_matrix(groups, "A", "EXISTING_EMX_FORWARD", "reference", "STRICT_LUMPED")
    assert m.shape == (4, 56)
    assert np.all(m[:, 10] == 0)
    assert np.isnan(m[:, :10]).all() and np.isnan(m[:, 11:]).all()


def test_duplicate_frequency_model_refused():
    with pytest.raises(ValueError):
        normalized_matrix([_group(), _group()], "A", "EXISTING_EMX_FORWARD", "reference", "STRICT_LUMPED")


def test_target_cells_keep_failures_sparse_outside_and_upper_boundary():
    result = target_cells([0, .5, 1, 2], [0, .5, 1, 2], [.1, np.nan, 2, 4], [True, False, False, False], [0, .5, 1], [0, .5, 1])
    assert result["N_requested"] == 4 and result["N_in_window"] == 3
    assert result["N_outside_window_or_nonfinite"] == 1
    corner = next(c for c in result["cells"] if c["x_index"] == c["y_index"] == 1)
    assert corner["N_requested"] == 2 and corner["N_finite"] == 1
    assert corner["mean_r_max_available"] == 2
    assert corner["hit_rate_fixed_denominator"] == 0 and corner["sparse_unreliable"]
    empty = next(c for c in result["cells"] if c["x_index"] != c["y_index"])
    assert empty["hit_rate_fixed_denominator"] is None


def test_target_cells_reject_degenerate_or_nonordered_edges():
    with pytest.raises(ValueError): target_cells([1], [1], [1], [True], [1, 1], [0, 2])
    with pytest.raises(ValueError): target_cells([1], [1], [1], [True], [0, 2], [2, 0])


def test_train_window_edges_must_be_predeclared():
    with pytest.raises(ValueError): _frozen_edges({})
    edges = {f: [0, 1] for f in FEATURES}
    assert _frozen_edges({"target_space_bin_edges": edges}) == edges


def _records():
    return [{"target_id": "synthetic-only", "feature": feature, "reference_or_target": "1", "predicted": "1.1",
             "error_abs": ".1", "error_over_tolerance": str(value), "prediction_finite": "True"}
            for feature, value in zip(FEATURES, [.1, .1, .1, 1.1])]


def test_all_four_and_not_rms_hit():
    a = _group_arrays(_records(), 1)
    assert a["r_max"].tolist() == [1.1]
    assert a["hit"].tolist() == [False]


def test_physical_invalid_response_cannot_be_hit_but_error_is_retained():
    records = _records()
    for row in records: row["error_over_tolerance"] = ".1"
    records[0]["prediction_physical_valid"] = "False"
    a = _group_arrays(records, 1)
    assert a["hit"].tolist() == [False]
    assert a["finite_four"].tolist() == [True]
    assert a["r_max"].tolist() == [.1]


def test_source_invalid_record_cannot_be_hit():
    records = _records()
    for row in records: row["error_over_tolerance"] = ".1"
    records[0]["source_label_valid"] = "False"
    assert _group_arrays(records, 1)["hit"].tolist() == [False]


def test_duplicate_missing_feature_or_target_denominator_rejected():
    with pytest.raises(ValueError): _group_arrays(_records() + [_records()[0]], 1)
    with pytest.raises(ValueError): _group_arrays(_records()[:-1], 1)
    with pytest.raises(ValueError): _group_arrays(_records(), 2)


def test_nonfinite_status_cannot_hide_finite_error():
    records = _records()
    records[0]["prediction_finite"] = "False"
    with pytest.raises(ValueError): _group_arrays(records, 1)


def test_renderer_missing_sources_fails_without_output(tmp_path):
    with pytest.raises(FileNotFoundError): render_large_figures(tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_synthetic_export_honors_source_pins_and_no_model_calls(tmp_path, monkeypatch):
    import csv
    import matplotlib.figure
    import research.broadband56_nn.frequency_large_figures as module
    root = tmp_path / "synthetic"; root.mkdir()
    (root / "CONFIGURATION_FREEZE.json").write_text(json.dumps({"target_space_bin_edges": {f: [0, 1] for f in FEATURES}, "data_evidence": "SYNTHETIC_TEST_ONLY"}))
    group = _group(frequency="15"); group.update(N_requested=1, N_completed=1, N_finite=1, N_pending=0, N_failed=0, ALL_FOUR_HIT_count=0, ALL_FOUR_HIT_rate=0)
    for f in FEATURES:
        group["features"][f].update(MAE_available=.1, P50_abs=.1, P90_abs=.1, P95_abs=.1)
    (root / "summary.json").write_text(json.dumps({"groups": [group], "real_emx_validation": "NOT_RUN"}))
    prediction = _records()
    for row in prediction: row.update({k: group[k] for k in module.GROUP_KEYS})
    status = [{"frequency_ghz": f, "label_mode": "STRICT_LUMPED", "model_status": "TRAINED_PARTIAL" if f == 15 else "NOT_TRAINED",
               "n_accepted_geometries": 1, "n_descriptor_valid": 1, "n_strict_valid": 1, "n_train_eligible": 0, "n_val_eligible": 0, "n_test_eligible": 1} for f in range(5, 61)]
    for name, data in (("prediction_records.csv", prediction), ("frequency_status.csv", status),
                       ("target_manifest.csv", [{"panel": "A", "target_id": "synthetic-only"}])):
        with (root / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, data[0].keys()); writer.writeheader(); writer.writerows(data)
    (root / "prediction_manifest.json").write_text(json.dumps({"schema": "frequency_large_prediction_manifest.v1",
        "artifacts": {name: module.pin(root / name) for name in ("prediction_records.csv", "summary.json", "frequency_status.csv")},
        "frozen_inputs": module.pin(root / "CONFIGURATION_FREEZE.json"), "target_manifest": module.pin(root / "target_manifest.csv")}))
    # Save mock image bytes to exercise source/data/manifest bookkeeping cheaply.
    def draw_then_save_stub(self, path, **kwargs):
        self.canvas.draw()  # Exercise real text/unit transforms without exporting images.
        path.write_bytes(b"SYNTHETIC_RENDER_STUB")
    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", draw_then_save_stub)
    result = render_large_figures(root, tmp_path / "figures")
    assert result["chart_count"] == 7
    assert result["figure_exports"] == 21
    assert result["status"] == "EXPORTED_PENDING_VISUAL_QA"
    assert result["sources"]["prediction_records.csv"]["sha256"] == module.sha256(root / "prediction_records.csv")
    matrix_data = next((tmp_path / "figures").glob("*_normalized_mae.json"))
    values = json.loads(matrix_data.read_text())["data"]["values"]
    assert values[0][0] is None and values[0][10] == .25
    with pytest.raises(FileExistsError): render_large_figures(root, tmp_path / "figures")
    (root / "target_manifest.csv").write_text("tampered\n")
    with pytest.raises(ValueError, match="identity mismatch"): render_large_figures(root, tmp_path / "tampered_figures")
    assert not (tmp_path / "tampered_figures").exists()
