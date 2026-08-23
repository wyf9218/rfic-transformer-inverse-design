import numpy as np

from rfic_transformer_inverse_design.model_splitting import split_physical_feature_indices


def test_physical_cell_split_is_deterministic_and_fingerprinted():
    rng = np.random.default_rng(20260711)
    lower = np.asarray([0.5, 0.5, 5.0, 0.0])
    upper = np.asarray([3.0, 3.0, 25.0, 0.8])
    x = rng.uniform(lower, upper, size=(640, 4))
    kwargs = {
        "mode": "physical_cell_grouped",
        "seed": 91,
        "validation_fraction": 0.15,
        "test_fraction": 0.10,
        "physical_cell_bins": 4,
        "physical_cell_lower": lower,
        "physical_cell_upper": upper,
    }

    split_a, audit_a = split_physical_feature_indices(x, **kwargs)
    split_b, audit_b = split_physical_feature_indices(x, **kwargs)
    _, audit_c = split_physical_feature_indices(x, **{**kwargs, "seed": 92})

    assert audit_a["split_fingerprint_sha256"] == audit_b["split_fingerprint_sha256"]
    assert audit_a["split_fingerprint_sha256"] != audit_c["split_fingerprint_sha256"]
    assert audit_a["physical_cell_overlap_count"] == 0
    assert audit_a["all_rows_assigned_once"] is True
    assert audit_a["physical_cell_partition_method"] == "seeded_sha256_threshold_by_cell_id"
    assert audit_a["physical_cell_partition_stable_for_existing_cells"] is True
    assert len(audit_a["physical_cell_partition_fingerprint_sha256"]) == 64
    for name in ("train", "validation", "test"):
        np.testing.assert_array_equal(split_a[name], split_b[name])
        assert audit_a["split_index_sha256"][name] == audit_b["split_index_sha256"][name]


def test_physical_cell_assignment_does_not_change_when_rows_are_added():
    rng = np.random.default_rng(123)
    lower = np.asarray([0.5, 0.5, 5.0, 0.0])
    upper = np.asarray([3.0, 3.0, 25.0, 0.8])
    base = rng.uniform(lower, upper, size=(4000, 4))
    extra = rng.uniform(lower, upper, size=(2000, 4))
    kwargs = {
        "mode": "physical_cell_grouped",
        "seed": 20260711,
        "validation_fraction": 0.15,
        "test_fraction": 0.10,
        "physical_cell_bins": 4,
        "physical_cell_lower": lower,
        "physical_cell_upper": upper,
    }

    _, first = split_physical_feature_indices(base, **kwargs)
    _, second = split_physical_feature_indices(np.vstack([base, extra]), **kwargs)

    first_assignment = {
        cell: split_name
        for split_name, cells in first["cell_ids"].items()
        for cell in cells
    }
    second_assignment = {
        cell: split_name
        for split_name, cells in second["cell_ids"].items()
        for cell in cells
    }
    common = set(first_assignment) & set(second_assignment)
    assert common
    assert all(first_assignment[cell] == second_assignment[cell] for cell in common)
