"""Synthetic-only regression checks for the research data boundary."""

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from research.broadband56_nn.data import FREQUENCY_HZ, PHYSICAL_COLUMNS, S_COLUMNS, _split_for_hash, prepare_data, sha256


FIELDS = ("primary_outer_width_um", "primary_outer_height_um", "secondary_outer_width_um", "secondary_outer_height_um", "line_width_um", "primary_terminal_y_span_um", "secondary_terminal_y_span_um", "offset_um", "primary_feed_extension_um", "secondary_feed_extension_um")
GATES = ("duplicate_status", "geometry_bounds_status", "analytical_status", "topology_status", "cadence_gds_status", "calibre_status", "emx_status", "s4p_status", "s_to_z_status", "feature_extraction_status")
CONTRACT = "a" * 64


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pin(path):
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def fixture_source(root: Path, n=30):
    root.mkdir()
    hashes = [hashlib.sha256(f"synthetic-geometry-{i}".encode()).hexdigest() for i in range(n)]
    geometry, long = [], []
    for i, digest in enumerate(hashes):
        geometry.append({"geometry_id": digest, "geometry_sha256": digest, "campaign_contract_fingerprint": CONTRACT, "accepted_sequence": i + 1, "campaign_phase": "TEST_ONLY", "acquisition_source": "SYNTHETIC", "calibre_blocking_violations": 0, **{f"geom__{name}": 1.0 + i * 0.1 + j * 0.01 for j, name in enumerate(FIELDS)}, **{name: "PASS" for name in GATES}})
        for j, frequency in enumerate(FREQUENCY_HZ):
            values = [i + 1.0, i + 2.0, i + 3.0, i + 4.0, i + 3.0, 0.1, 0.1]
            if j == 55:
                values[2] = float("nan")
            long.append({"geometry_id": digest, "geometry_sha256": digest, "campaign_contract_fingerprint": CONTRACT, "accepted_sequence": i + 1, "s4p_sha256": hashlib.sha256(f"synthetic-s4p-{i}".encode()).hexdigest(), "frequency_hz": int(frequency), **dict(zip(PHYSICAL_COLUMNS, values)), "broadband_descriptor_valid": "true" if j < 55 else "false", "strict_lumped_valid": "true" if j < 26 else "false", **{name: (i + 1) * 0.001 + j * 0.0001 + k * 0.00001 for k, name in enumerate(S_COLUMNS)}})
    write_csv(root / "geometry.csv", geometry)
    write_csv(root / "long.csv", long)
    bounds = {"contract_fingerprint_sha256": CONTRACT, "geometry_coverage_contract": {"field_order": list(FIELDS)}, "field_bounds_um": {name: [0.0, 100.0] for name in FIELDS}}
    write_json(root / "bounds.json", bounds)
    refresh_source(root, n)
    return root / "source.json", geometry, long


def refresh_source(root, n):
    files = {"accepted_geometries": pin(root / "geometry.csv"), "long_features": pin(root / "long.csv"), "geometry_bounds": pin(root / "bounds.json")}
    checkpoint = {"overall_status": "PASS", "decision": "USE_CHECKPOINT", "contract_fingerprint_sha256": CONTRACT, "expected_accepted": n, "inputs": dict(files)}
    products = {"overall_status": "PASS", "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS", "checks": {"synthetic_check": True}, "contract_fingerprint_sha256": CONTRACT, "counts": {"accepted_geometries": n, "geometry_frequency_rows": n * 56}, "outputs": {name: files[name] for name in ("accepted_geometries", "long_features")}}
    write_json(root / "checkpoint.json", checkpoint)
    write_json(root / "products.json", products)
    files.update(checkpoint_receipt=pin(root / "checkpoint.json"), raw_products_receipt=pin(root / "products.json"))
    write_json(root / "source.json", {"schema": "bb_source_manifest.v1", "contract_fingerprint_sha256": CONTRACT, "files": files, "port_contract": {"port_order": ["P001", "P002", "P003", "P004"], "reference_impedance_ohm": 50.0}})


def test_geometry_grouping_full_s_and_train_only_scaling(tmp_path):
    source, _, _ = fixture_source(tmp_path / "source")
    out = tmp_path / "prepared"
    manifest = prepare_data(source, out)
    data = np.load(out / "dataset.npz", allow_pickle=False)
    assert data["s"].shape == (30, 56, 32)
    assert data["geometry"].shape == (30, 10)
    assert data["y"].shape == data["y_valid"].shape == (30, 56, 4)
    assert data["s_valid"].all()
    assert not data["y_valid"][:, -1].any()
    assert not data["y_valid"][:, 26:].any()
    assert data["physical_valid"][:, 26:55].all()
    assert np.isnan(data["physical_features"][:, -1, 2]).all()
    np.testing.assert_array_equal(data["frequency_hz"], FREQUENCY_HZ)
    normalizer = json.loads((out / "normalizer.json").read_text())
    train = data["split"] == 0
    np.testing.assert_allclose(normalizer["s_mean"], data["s"][train].mean((0, 1)))
    np.testing.assert_allclose(normalizer["g_max"], data["geometry"][train].max(0))
    assert manifest["split_counts"]["train"] == int(train.sum())
    for artifact in manifest["artifacts"].values():
        assert sha256(out / artifact["path"]) == artifact["sha256"]
    splits = json.loads((out / "splits.json").read_text())
    groups = [set(splits["ids"][name]) for name in ("train", "validation", "test")]
    assert not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])


def test_growth_preserves_old_groups(tmp_path):
    source, _, _ = fixture_source(tmp_path / "source", 30)
    prepare_data(source, tmp_path / "old")
    larger, _, _ = fixture_source(tmp_path / "larger", 40)
    prepare_data(larger, tmp_path / "new", previous_splits=tmp_path / "old/splits.json")
    old = json.loads((tmp_path / "old/splits.json").read_text())
    new = json.loads((tmp_path / "new/splits.json").read_text())
    assert all(new["by_geometry_sha256"][key] == value for key, value in old["by_geometry_sha256"].items())
    assert _split_for_hash("a" * 64, 17) == _split_for_hash("a" * 64, 17)


def test_no_clobber(tmp_path):
    source, _, _ = fixture_source(tmp_path / "source")
    out = tmp_path / "existing"
    out.mkdir()
    sentinel = out / "old.txt"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        prepare_data(source, out)
    assert sentinel.read_text() == "preserve"


def test_tampered_input_rejected_and_failure_preserved(tmp_path):
    source, _, _ = fixture_source(tmp_path / "source")
    path = source.parent / "geometry.csv"
    with path.open("a") as handle:
        handle.write("tampered\n")
    out = tmp_path / "failed"
    with pytest.raises(ValueError, match="SHA/size"):
        prepare_data(source, out)
    assert (out / "PREPARATION_FAILED.json").exists()
    assert not (out / "dataset.npz").exists()


@pytest.mark.parametrize("problem", ["missing", "duplicate", "off_grid", "nan_s", "wrong_identity", "wrong_contract"])
def test_invalid_fullband_source_rejected(tmp_path, problem):
    source, _, rows = fixture_source(tmp_path / "source")
    if problem == "missing":
        rows.pop()
    elif problem == "duplicate":
        rows.append(dict(rows[0]))
    elif problem == "off_grid":
        rows[0]["frequency_hz"] += 500_000_000
    elif problem == "nan_s":
        rows[0][S_COLUMNS[0]] = "nan"
    elif problem == "wrong_identity":
        rows[0]["geometry_sha256"] = "b" * 64
    else:
        rows[0]["campaign_contract_fingerprint"] = "b" * 64
    write_csv(source.parent / "long.csv", rows)
    refresh_source(source.parent, 30)
    with pytest.raises(ValueError):
        prepare_data(source, tmp_path / "failed")


def test_normalizer_unaffected_by_validation_test_labels(tmp_path):
    source, _, rows = fixture_source(tmp_path / "source")
    prepare_data(source, tmp_path / "base")
    for row in rows:
        if _split_for_hash(row["geometry_sha256"], 17) != 0:
            for name in (*S_COLUMNS, *PHYSICAL_COLUMNS):
                row[name] = 999.0
    write_csv(source.parent / "long.csv", rows)
    refresh_source(source.parent, 30)
    prepare_data(source, tmp_path / "changed")
    assert (tmp_path / "base/normalizer.json").read_bytes() == (tmp_path / "changed/normalizer.json").read_bytes()
