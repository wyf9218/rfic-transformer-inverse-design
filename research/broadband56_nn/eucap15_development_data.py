"""Materialize the audited, all-Q 15 GHz core domain without changing raw data.

One immutable source snapshot; original geometry order/hash split and every
56-frequency array are retained. This is DEVELOPMENT_CURRENT_SNAPSHOT, not a
newly generated 10K dataset, a model evaluation, or a fresh-EMX experiment.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .data import FREQUENCY_HZ, SPLIT_NAMES, _check_pin, _fit_scale, _write_json, sha256
from .io import utc_now


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def checked(item):
    return _check_pin({**item, "size_bytes": item.get("bytes", item.get("size_bytes"))}, Path("/"))


def document(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, records):
    require(bool(records), "empty evidence table")
    with Path(path).open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def truth(value):
    require(value in ("True", "False"), "noncanonical audit boolean")
    return value == "True"


def same_array(left, right):
    return left.dtype == right.dtype and left.shape == right.shape and np.array_equal(
        left, right, equal_nan=True) if left.dtype.kind in "fc" else (
        left.dtype == right.dtype and left.shape == right.shape and np.array_equal(left, right))


def materialize(audit_manifest, expected_manifest_sha256, out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        return _materialize(Path(audit_manifest).resolve(), expected_manifest_sha256, out)
    except Exception as exc:
        _write_json(out / "PREPARATION_FAILED.json", {
            "status": "FAIL", "error": repr(exc), "created_utc": utc_now(),
            "source_modified": False, "training_calls": 0, "physical_calls": 0})
        raise


def _materialize(audit_path, expected_sha, out):
    require(sha256(audit_path) == expected_sha, "audit manifest SHA mismatch")
    audit = document(audit_path)
    require(audit["schema"] == "eucap15_domain_audit_manifest.v1", "wrong audit schema")
    audit_pins = {Path(p["path"]).name: p for p in audit["artifacts"]}
    require(len(audit_pins) == len(audit["artifacts"]), "duplicate audit artifact names")
    names = ("eligibility_15ghz.json", "eligibility_membership.csv", "source_validity_profile.csv",
             "train_core_labels.csv", "PROFILE_AND_MASK_PROVENANCE.json", "INPUT_FREEZE.json")
    paths = {name: checked(audit_pins[name]) for name in names}
    eligibility, profile, freeze = (document(paths[name]) for name in
        ("eligibility_15ghz.json", "PROFILE_AND_MASK_PROVENANCE.json", "INPUT_FREEZE.json"))
    require(eligibility["schema"] == "eucap15_reference_domain_eligibility.v1" and
            eligibility["status"] == "PASS_SINGLE_FROZEN_SNAPSHOT_DOMAIN_AUDIT", "audit not qualified")
    sources = {"audit_manifest": pin(audit_path), **{name: audit_pins[name] for name in names}}
    for name, key in (("dataset.npz", "source_dataset"), ("data_manifest.json", "source_manifest"),
                      ("splits.json", "source_split")):
        require(eligibility[key] == freeze["data"][name], "audit/freeze source identity differs")
        sources[name] = eligibility[key]
    source_paths = {name: checked(sources[name]) for name in ("dataset.npz", "data_manifest.json", "splits.json")}
    require(eligibility["reference"] == freeze["reference"], "reference pins disagree")
    sources["reference"] = eligibility["reference"]
    reference = document(checked(sources["reference"]))
    sources["geometry_contract"] = reference["geometry_contract_file"]
    contract_path = checked(sources["geometry_contract"])
    contract = document(contract_path)
    manifest, splits = document(source_paths["data_manifest.json"]), document(source_paths["splits.json"])
    require(manifest["schema"] == "bb_data_manifest.v1" and manifest["status"] == "PASS", "source data not PASS")
    require(reference["data"]["dataset.npz"] == sources["dataset.npz"], "reference dataset differs")
    require(manifest["artifacts"]["dataset.npz"]["sha256"] == sources["dataset.npz"]["sha256"], "source manifest dataset differs")
    require(contract["field_names"] == manifest["geometry_field_order"] == reference["geometry_fields"], "field order mismatch")
    census = rows(paths["eligibility_membership.csv"])
    count = len(census)
    require(count == manifest["unique_geometries"], "census/source count mismatch")
    require([int(r["source_row"]) for r in census] == list(range(count)), "census missing/reordered rows")
    require(len({r["geometry_sha256"] for r in census}) == count, "duplicate census hash")
    census_counts = {}
    for split in ("all", *SPLIT_NAMES):
        selected = [r for r in census if split == "all" or r["split"] == split]
        census_counts[split] = {"source": len(selected), "core_all_q": sum(truth(r["core_domain"]) for r in selected),
            "core_q10_20": sum(truth(r["core_domain_q10_20"]) for r in selected)}
    print(json.dumps({"phase": "CENSUS_ROWS_COUNTED", "counts": census_counts}), flush=True)
    before = source_paths["dataset.npz"].stat()
    with np.load(source_paths["dataset.npz"], allow_pickle=False) as archive:
        original = {key: archive[key] for key in archive.files}
    require(set(original) == set(profile["available_array_names"]), "source array set changed")
    require(np.array_equal(original["frequency_hz"], FREQUENCY_HZ), "not exact original 56 frequencies")
    at = int(np.flatnonzero(original["frequency_hz"] == 15e9)[0])
    require(at == profile["frequency_index"], "15GHz source index mismatch")
    y, physical = original["y"][:, at], original["physical_features"][:, at]
    strict, descriptor = original["strict_lumped_valid"][:, at], original["broadband_descriptor_valid"][:, at]
    group, hashes, ids = original["split"], original["geometry_sha256"].tolist(), original["geometry_ids"].tolist()
    require(y.shape == (count, 4) and physical.shape == (count, 7), "wrong physical dimensions")
    require(strict.dtype == bool and descriptor.dtype == bool and set(group.tolist()) == {0, 1, 2}, "wrong masks/split")
    require(len(set(hashes)) == len(set(ids)) == count and np.isfinite(original["geometry"]).all(), "invalid geometry identities")
    require(len(np.unique(original["geometry"], axis=0)) == count, "duplicate source geometry coordinates")
    require(np.array_equal(y, physical[:, [0, 1, 4, 6]], equal_nan=True), "physical mapping differs")
    q_expected = np.where(np.isfinite(physical[:, 2:4]).all(1), np.minimum(physical[:, 2], physical[:, 3]), np.nan)
    require(np.array_equal(y[:, 2], q_expected, equal_nan=True) and
            np.array_equal(y[:, 3], np.abs(physical[:, 5]), equal_nan=True), "Qmin/absK derivation differs")
    require(np.array_equal(original["y_valid"][:, at], strict[:, None] & np.isfinite(y)), "strict target mask differs")
    require(np.array_equal(original["physical_valid"][:, at], descriptor[:, None] & np.isfinite(physical)), "descriptor physical mask differs")
    schema = json.loads(str(original["source_validity_schema_json"]))
    require(schema == profile["source_validity_schema"], "validity provenance schema differs")
    codes = original["source_validity_codes"][:, at]
    actual_profile, flags = [], {}
    for j, field in enumerate(schema["fields"]):
        categories = schema["categories"][field]
        require(np.all((codes[:, j] >= 0) & (codes[:, j] < len(categories))), "invalid category code")
        flags[field] = np.asarray(categories)[codes[:, j]]
        for category in categories:
            mask = flags[field] == category
            actual_profile.append({"field": field, "category": category, "N": str(int(mask.sum())),
                **{f"N_{name}": str(int((mask & (group == i)).sum())) for i, name in enumerate(SPLIT_NAMES)}})
    require(actual_profile == rows(paths["source_validity_profile.csv"]), "saved validity census differs")
    below = flags["below_half_srf"] == "true"
    require(np.array_equal(strict, descriptor & below), "strict/SRF relationship differs")
    finite = np.isfinite(y).all(1)
    eligible = strict & finite
    core = eligible & ((y[:, [0, 1, 3]] >= [.5, .5, .2]) & (y[:, [0, 1, 3]] <= [2., 2., .85])).all(1)
    core_q = core & (y[:, 2] >= 10) & (y[:, 2] <= 20)
    masks = {"descriptor": descriptor, "strict_lumped_valid": strict, "below_half_srf": below,
             "four_labels_finite": finite, "old_strict_eligible": eligible,
             "core_domain": core, "core_domain_q10_20": core_q}
    for i, row in enumerate(census):
        require(row["geometry_sha256"] == hashes[i] and row["split"] == SPLIT_NAMES[int(group[i])], "census identity mismatch")
        require(all(truth(row[key]) == bool(mask[i]) for key, mask in masks.items()), "census row mask differs")
        require(splits["by_geometry_sha256"][hashes[i]] == row["split"] and
                splits["geometry_id_to_sha256"][ids[i]] == hashes[i], "original geometry split changed")
    indices = np.flatnonzero(core)
    require(len(indices) == census_counts["all"]["core_all_q"] == 3018, "expected audited core3018 does not reconcile")
    train_indices = np.flatnonzero(core & (group == 0))
    train_rows = rows(paths["train_core_labels.csv"])
    require([int(r["source_row"]) for r in train_rows] == train_indices.tolist(), "train-core row identities differ")
    for row, i in zip(train_rows, train_indices):
        require(row["geometry_sha256"] == hashes[i] and int(row["frequency_hz"]) == 15000000000, "train label identity differs")
        require(all(float(row[key]) == float(physical[i, j]) for j, key in enumerate(profile["physical_columns"])), "train labels differ")
    count_table = []
    for name in ("all", *SPLIT_NAMES):
        part = np.ones(count, bool) if name == "all" else group == SPLIT_NAMES.index(name)
        row = {"split": name, "source_geometries": int(part.sum()), "original_strict_eligible": int((part & eligible).sum()),
               "new_core_all_q": int((part & core).sum()), "new_core_q10_20": int((part & core_q).sum()),
               "core_q_below10_retained": int((part & core & (y[:, 2] < 10)).sum()),
               "core_q_above20_retained": int((part & core & (y[:, 2] > 20)).sum())}
        old = eligibility["counts"][name]
        for key, prior in (("source_geometries", "N_source_geometries"), ("original_strict_eligible", "N_old_strict_eligible"),
                           ("new_core_all_q", "N_strict_core_domain"), ("new_core_q10_20", "N_strict_core_domain_q10_20"),
                           ("core_q_below10_retained", "N_core_q_below10"), ("core_q_above20_retained", "N_core_q_above20")):
            require(row[key] == old[prior], "prior eligibility count disagrees with rows: " + key)
        count_table.append(row)
    arrays, array_mapping = {}, {}
    metadata_names = {"frequency_hz", "source_extra_physical_columns", "source_validity_schema_json"}
    for key, value in original.items():
        if key in metadata_names:
            arrays[key] = value.copy()
        else:
            require(value.ndim >= 1 and value.shape[0] == count, "unrecognized array row semantics: " + key)
            arrays[key] = value[indices].copy()
        array_mapping[key] = {"source_shape": list(value.shape), "output_shape": list(arrays[key].shape),
            "dtype": str(value.dtype), "operation": "unchanged_metadata" if key in metadata_names else "original_rows_in_order_all_frequencies",
            "output_values_sha256": hashlib.sha256(arrays[key].tobytes(order="C")).hexdigest()}
    split_counts = {name: int((arrays["split"] == i).sum()) for i, name in enumerate(SPLIT_NAMES)}
    train = arrays["split"] == 0
    fields = contract["field_names"]
    s_mean, s_scale = _fit_scale(arrays["s"][train], arrays["s_valid"][train])
    y_mean, y_scale = _fit_scale(arrays["y"][train], arrays["y_valid"][train])
    norm = {"schema": "bb_normalizer.v1", "fit_split": "train", "training_geometries": split_counts["train"], "scale_floor": 1e-6,
        "g_min": arrays["geometry"][train].min(0).tolist(), "g_max": arrays["geometry"][train].max(0).tolist(),
        "s_mean": s_mean, "s_scale": s_scale, "y_mean": y_mean, "y_scale": y_scale,
        "field_names": fields, "geometry_fields": fields, "s_columns": manifest["s_channels"],
        "y_columns": manifest["target_columns"], "y_units": ["nH", "nH", "dimensionless", "dimensionless"],
        "contract_bounds_um": {"lower": contract["lower"], "upper": contract["upper"]},
        "physical_mask_domain": "unchanged strict_lumped_valid AND finite_feature",
        "bb00_normalizer": "independently recomputed by prepare_bb00 at requested15GHz from this train subset"}
    subset_splits = {key: splits[key] for key in ("schema", "seed", "contract_fingerprint_sha256", "method", "requested_fractions")}
    subset_splits.update(counts=split_counts, previous_splits=sources["splits.json"],
        by_geometry_sha256={hashes[i]: SPLIT_NAMES[int(group[i])] for i in indices},
        geometry_id_to_sha256={ids[i]: hashes[i] for i in indices},
        ids={name: [ids[i] for i in indices if group[i] == j] for j, name in enumerate(SPLIT_NAMES)})
    with (out / "dataset.npz").open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    _write_json(out / "normalizer.json", norm)
    _write_json(out / "splits.json", subset_splits)
    with (out / "contract.json").open("xb") as stream:
        stream.write(contract_path.read_bytes())
    write_csv(out / "CORRECTED_COUNTS.csv", count_table)
    _write_json(out / "CORRECTED_COUNTS.json", {"scope": "DEVELOPMENT_CURRENT_SNAPSHOT", "counts": count_table,
        "display_correction": "3018 total is not 3018 gradient-training rows; original labels and census unchanged",
        "selection": "strict15 AND finite four labels AND closed joint Lp/Ls[.5,2],absK[.2,.85]; ALL Q"})
    selected_rows = [{"subset_row": j, "source_row": int(i), "geometry_id": ids[i], "geometry_sha256": hashes[i],
        "split": SPLIT_NAMES[int(group[i])], "core_q10_20": bool(core_q[i])} for j, i in enumerate(indices)]
    write_csv(out / "SOURCE_ROWS.csv", selected_rows)
    _write_json(out / "ARRAY_MAPPING.json", array_mapping)
    _write_json(out / "SOURCE_MANIFEST.json", {"schema": "eucap15_development_sources.v1", "sources": sources,
        "implementation": pin(__file__), "helpers": {name: pin(Path(__file__).parent / name) for name in ("data.py", "bb00.py", "training.py", "io.py")}})
    artifacts = {p.name: {"path": p.name, "sha256": sha256(p), "size_bytes": p.stat().st_size}
                 for p in sorted(out.iterdir()) if p.is_file()}
    keep = ("contract_fingerprint_sha256", "geometry_dim", "geometry_fields", "geometry_field_order", "geometry_bounds",
            "geometry_units", "frequency_hz", "s_channels", "physical_columns", "target_columns", "port_contract")
    result = {key: manifest[key] for key in keep}
    result.update(schema="bb_data_manifest.v1", status="PASS", evidence="DEVELOPMENT_CURRENT_SNAPSHOT",
        source_manifest=sources["data_manifest.json"], source_dataset=sources["dataset.npz"],
        unique_geometries=len(indices), frequency_rows=len(indices) * 56, split_counts=split_counts,
        normalizer_fit_split="train", test_usage="Original frozen test membership retained; no fitted scaling or model selection from test",
        physical_validity="Every original 56-point label/domain/validity array preserved without clipping", artifacts=artifacts)
    _write_json(out / "data_manifest.json", result)
    # Targeted integrity only: load the output arrays, never any trained model.
    from .training import Bundle
    from .bb00 import prepare_bb00
    bundle = Bundle(out)
    require(set(bundle.arrays) == set(arrays) and all(same_array(bundle.arrays[k], arrays[k]) for k in arrays), "NPZ round-trip not exact")
    bb_norm, bb_train, bb_val, fi, exposure = prepare_bb00(bundle, contract, (2.5, 2.5, 20., .8))
    require(fi == at and np.array_equal(bb_train, np.flatnonzero(train)) and np.array_equal(bb_val, np.flatnonzero(arrays["split"] == 1)), "BB00 eligibility changed")
    require(bb_norm["y_mean"] == arrays["y"][train, at].mean(0).tolist(), "BB00 train-only mean differs")
    after = source_paths["dataset.npz"].stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns), "source changed during preparation")
    for item in sources.values():
        checked(item)
    _write_json(out / "BB00_NORMALIZER_15GHZ.json", bb_norm)
    receipt = {"schema": "eucap15_development_data_receipt.v1", "status": "PASS_MATERIALIZED_AND_BUNDLE_VERIFIED",
        "scope": "DEVELOPMENT_CURRENT_SNAPSHOT", "data_root": str(out), "data_manifest": pin(out / "data_manifest.json"),
        "dataset": pin(out / "dataset.npz"), "contract": pin(out / "contract.json"), "source_manifest": pin(out / "SOURCE_MANIFEST.json"),
        "source_snapshot_geometries": count, "selected_geometries": len(indices), "split_counts": split_counts,
        "corrected_counts": count_table, "geometry_fields": fields, "eligible_rows": exposure,
        "checks": {"census_rows_npz_and_receipt_agree": True, "source_validity_profile_exact": True,
            "train_core_labels_exact": True, "all_arrays_roundtrip_exact": True, "original_hash_split_preserved": True,
            "source_bytes_unchanged": True, "bb00_train_only_normalizer_verified": True, "all_q_tails_retained": True},
        "training_calls": 0, "model_loads": 0, "new_physical_calls": 0,
        "REAL_EMX_VALIDATION": "NOT_RUN_NEW_GEOMETRIES", "created_utc": utc_now()}
    _write_json(out / "DATA_RECEIPT.json", receipt)
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as stream:
        for path in sorted(out.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256(path)}  {path.name}\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.audit_manifest, args.expected_manifest_sha256, args.out), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
