"""Small synthetic data-quality tests; no model, optimizer or remote access."""
import csv
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from research.broadband56_nn.data import FREQUENCY_HZ, Y_COLUMNS, SPLIT_NAMES, _split_for_hash
from research.broadband56_nn.frequency_profile import (
    frequency_mask, profile_bundle, profile_prepared_data, load_profile_bundle)
from research.broadband56_nn.io import save_json, sha256


@pytest.fixture
def bundle():
    hashes = np.array([hashlib.sha256(f"synthetic-{i}".encode()).hexdigest() for i in range(60)])
    split = np.array([_split_for_hash(h, 17) for h in hashes], dtype=np.int8)
    y = np.broadcast_to(np.arange(60, dtype=float)[:, None, None] + np.arange(4)[None, None, :] + 1,
                        (60, 56, 4)).copy()
    strict = np.ones((60, 56), dtype=bool)
    strict[:, 26:] = False
    arrays = {"geometry_ids": np.array([f"g{i}" for i in range(60)]), "geometry_sha256": hashes,
              "geometry": np.arange(600, dtype=float).reshape(60, 10), "frequency_hz": FREQUENCY_HZ.copy(),
              "split": split, "y": y, "y_valid": np.broadcast_to(strict[..., None], y.shape).copy(),
              "strict_lumped_valid": strict, "broadband_descriptor_valid": np.ones_like(strict)}
    counts = {name: int((split == i).sum()) for i, name in enumerate(SPLIT_NAMES)}
    fields = [f"geometry_{i}" for i in range(10)]
    manifest = {"schema": "bb_data_manifest.v1", "status": "PASS", "unique_geometries": 60,
                "geometry_dim": 10, "geometry_fields": fields, "geometry_units": "um",
                "target_columns": list(Y_COLUMNS), "split_counts": counts,
                "port_contract": {"port_order": ["P001", "P002", "P003", "P004"], "reference_impedance_ohm": 50.0},
                "contract_fingerprint_sha256": "a" * 64, "sources": {}}
    splits = {"seed": 17, "method": "sha256 bb56-split-v1:seed:canonical_geometry_sha256 first64bits thresholds 0.6/0.8",
              "by_geometry_sha256": {h: SPLIT_NAMES[int(s)] for h, s in zip(hashes, split)}}
    return SimpleNamespace(arrays=arrays, manifest=manifest, splits=splits)


def test_exact_56_slots_modes_and_high_frequency_gaps(bundle):
    result = profile_bundle(bundle)
    assert [row["frequency_ghz"] for row in result["rows"]] == list(range(5, 61))
    assert result["snapshot_role"] == "DEVELOPMENT_LT10K"
    for row in result["rows"][26:]:
        strict = row["label_modes"]["STRICT_LUMPED"]
        assert strict["status"] == "NO_STRICT_LABELS"
        assert strict["eligible_count"] == 0
        assert strict["train_distribution"]["qmin"]["p50"] is None
        assert row["label_modes"]["POINTWISE_DESCRIPTOR_EXPERIMENTAL"]["eligible_count"] == 60


def test_finite_domain_and_mask_exclusions_are_disjoint(bundle):
    arrays = bundle.arrays
    arrays["y"][0, 10, 0] = np.nan
    arrays["strict_lumped_valid"][1, 10] = False
    arrays["y_valid"][2, 10, 2] = False
    row = profile_bundle(bundle)["rows"][10]
    assert row["label_modes"]["STRICT_LUMPED"]["eligible_count"] == 57
    assert row["label_modes"]["POINTWISE_DESCRIPTOR_EXPERIMENTAL"]["eligible_count"] == 59
    reasons = {key: 0 for key in ("domain_invalid", "domain_valid_nonfinite_target", "domain_valid_finite_but_target_mask_invalid")}
    for group in row["label_modes"]["STRICT_LUMPED"]["splits"].values():
        assert group["eligible"] + sum(group["exclusion_reasons_disjoint"].values()) == group["total"]
        for key, count in group["exclusion_reasons_disjoint"].items():
            reasons[key] += count
    assert set(reasons.values()) == {1}


def test_train_statistics_and_joint_coverage_never_fit_test_values(bundle):
    original = profile_bundle(bundle)["rows"][10]["label_modes"]["STRICT_LUMPED"]
    bundle.arrays["y"][bundle.arrays["split"] != 0] = 1e30
    result = profile_bundle(bundle)["rows"][10]["label_modes"]["STRICT_LUMPED"]
    assert result["train_distribution"] == original["train_distribution"]
    assert result["train_joint_coverage"] == original["train_joint_coverage"]
    train = bundle.arrays["y"][bundle.arrays["split"] == 0, 10]
    assert result["train_distribution"]["lp_nh"]["p5"] == np.percentile(train[:, 0], 5)
    assert sum(row["count"] for row in result["train_joint_coverage"]["cell_counts"]) == len(train)


@pytest.mark.parametrize("frequency", [15.0, True, 4, 61])
def test_no_implicit_frequency_rounding(bundle, frequency):
    with pytest.raises(ValueError):
        frequency_mask(bundle, frequency)


def test_unknown_mode_is_not_silently_strict(bundle):
    with pytest.raises(ValueError):
        frequency_mask(bundle, 15, "descriptor")


@pytest.mark.parametrize("change", ["split", "duplicate", "frequency", "target_order", "strict_exceeds_descriptor"])
def test_invalid_snapshot_contract_rejected(bundle, change):
    if change == "split":
        bundle.arrays["split"][0] = (bundle.arrays["split"][0] + 1) % 3
    elif change == "duplicate":
        bundle.arrays["geometry_sha256"][1] = bundle.arrays["geometry_sha256"][0]
    elif change == "frequency":
        bundle.arrays["frequency_hz"][0] = 6_000_000_000
    elif change == "target_order":
        bundle.manifest["target_columns"].reverse()
    else:
        bundle.arrays["broadband_descriptor_valid"][0, 10] = False
    with pytest.raises(ValueError):
        profile_bundle(bundle)


def _write_prepared(bundle, path):
    path.mkdir()
    np.savez_compressed(path / "dataset.npz", **bundle.arrays)
    save_json(path / "normalizer.json", {"synthetic": True})
    save_json(path / "splits.json", bundle.splits)
    save_json(path / "geometry_provenance.json", {"synthetic": True})
    save_json(path / "source_manifest.json", {"synthetic": True})
    bundle.manifest["source_manifest"] = {"path": str(path / "source_manifest.json"), "sha256": sha256(path / "source_manifest.json")}
    bundle.manifest["artifacts"] = {name: {"path": name, "sha256": sha256(path / name)} for name in
                                    ("dataset.npz", "normalizer.json", "splits.json", "geometry_provenance.json")}
    save_json(path / "data_manifest.json", bundle.manifest)
    return path


def test_no_clobber_outputs_and_exact_csv_json_equivalence(bundle, tmp_path):
    data = _write_prepared(bundle, tmp_path / "data")
    original = {p.name: p.read_bytes() for p in data.iterdir()}
    out = tmp_path / "profile"
    receipt = profile_prepared_data(data, out)
    assert receipt["model_training"] is False
    rows = list(csv.DictReader((out / "frequency_data_profile.csv").open()))
    document = json.loads((out / "frequency_data_profile.json").read_text())
    assert len(rows) == len(document["rows"]) == 56
    assert int(rows[10]["strict_eligible_count"]) == document["rows"][10]["label_modes"]["STRICT_LUMPED"]["eligible_count"]
    for line in (out / "SHA256SUMS.txt").read_text().splitlines():
        expected, name = line.split("  ")
        assert sha256(out / name) == expected
    with pytest.raises(FileExistsError):
        profile_prepared_data(data, out)
    assert {p.name: p.read_bytes() for p in data.iterdir()} == original


def test_profile_never_writes_inside_prepared_data(bundle, tmp_path):
    data = _write_prepared(bundle, tmp_path / "data")
    with pytest.raises(ValueError):
        profile_prepared_data(data, data / "profile")
    assert not (data / "profile").exists()


def test_changed_prepared_artifact_fails_and_retains_evidence(bundle, tmp_path):
    data = _write_prepared(bundle, tmp_path / "data")
    (data / "normalizer.json").write_text("{}")
    out = tmp_path / "failed_profile"
    with pytest.raises(ValueError):
        profile_prepared_data(data, out)
    assert json.loads((out / "PROFILE_FAILED.json").read_text())["status"] == "FAIL"
    assert not (out / "frequency_data_profile.json").exists()
