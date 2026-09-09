"""Synthetic metadata-only coverage of the two frozen data receipt schemas."""
import json

import pytest

from research.broadband56_nn import eucap15_development_tables as tables


SCHEMAS = [
    ("eucap15_development_data_receipt.v1", "selected_geometries"),
    ("eucap15_formal_development_view_receipt.v1", "selected_formal_geometries"),
]


@pytest.mark.parametrize("schema,field", SCHEMAS)
def test_exact_supported_schema_count(schema, field):
    receipt = {"schema": schema, field: 9}
    before = dict(receipt)
    assert tables.selected_geometry_count(receipt) == 9
    assert receipt == before


@pytest.mark.parametrize("receipt", [
    {},
    {"selected_geometries": 9},
    {"schema": "unknown.v1", "selected_geometries": 9},
    {"schema": "eucap15_formal_development_view_receipt.v2", "selected_formal_geometries": 9},
])
def test_missing_or_unknown_schema_is_not_inferred(receipt):
    with pytest.raises(ValueError, match="unsupported data receipt schema"):
        tables.selected_geometry_count(receipt)


@pytest.mark.parametrize("schema,field", SCHEMAS)
def test_missing_or_wrong_count_field_is_rejected(schema, field):
    other = next(f for _, f in SCHEMAS if f != field)
    for receipt in ({"schema": schema}, {"schema": schema, other: 9}):
        with pytest.raises(ValueError, match="missing data receipt count"):
            tables.selected_geometry_count(receipt)


@pytest.mark.parametrize("schema,field", SCHEMAS)
@pytest.mark.parametrize("other_count", [9, 10])
def test_two_count_fields_are_ambiguous_even_when_equal(schema, field, other_count):
    other = next(f for _, f in SCHEMAS if f != field)
    with pytest.raises(ValueError, match="ambiguous data receipt count fields"):
        tables.selected_geometry_count({"schema": schema, field: 9, other: other_count})


@pytest.mark.parametrize("bad_count", [None, True, -1, 9.0, "9"])
def test_count_must_be_nonnegative_integer(bad_count):
    schema, field = SCHEMAS[1]
    with pytest.raises(ValueError, match="nonnegative integer required"):
        tables.selected_geometry_count({"schema": schema, field: bad_count})


def write_metadata(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return tables.pin(path)


@pytest.mark.parametrize("schema,field", SCHEMAS)
@pytest.mark.parametrize("mismatch", [False, True])
def test_build_preserves_denominator_gate_before_any_arm_or_output(tmp_path, schema, field, mismatch):
    # No model/array files exist, and no arms are supplied. A matching count must
    # pass the changed gate, then fail at the unchanged closed-baseline gate.
    dataset = {"path": "/synthetic/not-opened.npz", "sha256": "a" * 64, "bytes": 1}
    source = {"path": "/synthetic/not-opened.json", "sha256": "b" * 64, "bytes": 1}
    receipt = {"schema": schema, field: 8 if mismatch else 9,
               "dataset": dataset, "data_manifest": source,
               "split_counts": {"train": 5, "validation": 2, "test": 2}}
    protocol = {
        "schema": "eucap15_current_snapshot_development_protocol.v1",
        "experiment_class": "DEVELOPMENT_CURRENT_SNAPSHOT",
        "seeds": [17, 29, 43], "validation_rows": 2, "gradient_train_rows": 5,
        "test_rows": 2, "source_rows": 9, "dataset": dataset, "source_data": source,
        "data_receipt": write_metadata(tmp_path / "data_receipt.json", receipt),
        "shapes": {"3x256": [256, 256, 256]},
        "training_recipe": {"hidden_layers": [256, 256, 256], "seed": 17},
    }
    entry = {"schema": "eucap15_development_tables_input.v1",
             "protocol": write_metadata(tmp_path / "protocol.json", protocol), "arms": []}
    input_path = tmp_path / "input.json"
    write_metadata(input_path, entry)
    before = set(tmp_path.iterdir())
    expected = "protocol/data receipt denominator mismatch" if mismatch else "closed baseline evaluation required"
    with pytest.raises(ValueError, match=expected):
        tables._build(input_path, tmp_path, {})
    assert set(tmp_path.iterdir()) == before
