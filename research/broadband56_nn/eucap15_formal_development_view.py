"""Frozen formal-source 15 GHz development view, never a FINAL/S-spectrum set.

Use only the two authorized formal source groups in an owner membership handoff.
The owner has already audited the original physical source chains. This adapter
checks the transferred row/identity bindings but does not reopen native sources,
old NPZs, 2GB frequency tables, weights or S4P. No model or native work occurs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import re

import numpy as np

from .data import (FREQUENCY_HZ, PHYSICAL_COLUMNS, SPLIT_NAMES, Y_COLUMNS,
                   _check_pin, _fit_scale, _split_for_hash, _write_json, sha256)
from .io import utc_now


COUNTS = {"FORMAL_BASE_20973": 6281, "STAGE238_INCREMENT": 48,
          "HISTORICAL_RESEARCH": 348, "ORIGINAL64_VALIDATION": 23}
INCLUDED = ("FORMAL_BASE_20973", "STAGE238_INCREMENT")
OLD_COUNTS = {"train": 1804, "validation": 595, "test": 619}
FREQUENCY = 15_000_000_000
SPANS = (2.5, 2.5, 20., .8)


def require(value, message):
    if not value:
        raise ValueError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON constant: " + value)


def document(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                      parse_constant=_constant)


def pin(path):
    path = Path(path).absolute()
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)


def checked(item):
    require(isinstance(item, dict) and {"path", "sha256", "bytes"} <= item.keys(), "full local pin required")
    p = Path(item["path"])
    require(p.is_absolute() and ".." not in p.parts and not any(q.is_symlink() for q in (p, *p.parents)),
            "exact non-symlink absolute input path required")
    require(type(item["bytes"]) is int and item["bytes"] > 0, "positive pin byte count required")
    return _check_pin({**item, "size_bytes": item["bytes"]}, Path("/"))


def csv_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None and len(set(reader.fieldnames)) == len(reader.fieldnames), "duplicate/missing CSV header")
        result = list(reader)
    require(all(None not in row and None not in row.values() for row in result), "ragged CSV")
    return result


def write_csv(path, rows, fields):
    with Path(path).open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def flag(value):
    require(value in ("true", "false", "True", "False"), "unrecognized source boolean")
    return value in ("true", "True")


def finite(value):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid source numeric value") from exc
    require(math.isfinite(number), "nonfinite source label/geometry")
    return number


def prepare(handoff_manifest_pin, old_splits_pin, contract_pin, out, *, synthetic=False):
    """Create a no-clobber 15GHz-only Bundle for BB00/Frequency Tandem.

    Three explicit local pins use {path,sha256,bytes}. All real counts are fixed;
    synthetic=True accepts only a handoff explicitly marked synthetic_fixture
    and emits SYNTHETIC_TEST_ONLY, never a research data claim. The function
    prepares/round-trips metadata and arrays only; it never starts training.
    """
    out = Path(out).absolute()
    require(".." not in out.parts and not any(p.is_symlink() for p in (out, *out.parents)), "unsafe output path")
    out.mkdir(parents=True, exist_ok=False)
    try:
        return _prepare(handoff_manifest_pin, old_splits_pin, contract_pin, out, synthetic)
    except Exception as exc:
        _write_json(out / "PREPARATION_FAILED.json", dict(status="FAIL_PRESERVED", error=repr(exc),
            created_utc=utc_now(), training_calls=0, native_calls=0, source_modified=False))
        raise


def _prepare(handoff_pin, old_pin, contract_pin, out, synthetic):
    manifest_path, split_path, contract_path = (checked(p) for p in (handoff_pin, old_pin, contract_pin))
    manifest, old, contract = (document(p) for p in (manifest_path, split_path, contract_path))
    require(manifest["schema"] == "private_data_handoff_manifest.v1", "handoff schema differs")
    artifacts = {}
    for p in manifest["artifacts"]:
        path = Path(p["path"])
        require(path.parent == manifest_path.parent and path.name not in artifacts, "handoff artifact path/identity differs")
        artifacts[path.name] = p
    needed = ("RECEIPT.json", "CONTRACT_PINS.json", "CORE_MEMBERS_6700.csv", "MEMBER_EVIDENCE.jsonl",
              "OLD3018_MEMBERSHIP_JOIN.csv", "CROSS_SOURCE_DUPLICATES.csv")
    require(set(needed) <= artifacts.keys(), "handoff artifacts incomplete")
    paths = {name: checked(artifacts[name]) for name in needed}
    receipt, contracts = document(paths["RECEIPT.json"]), document(paths["CONTRACT_PINS.json"])
    require(receipt["schema"] == "eucap15_core_membership_handoff.v1" and
            receipt["status"] == "PASS_MEMBERSHIP_AND_LABEL_SOURCE_FREEZE", "owner membership handoff not PASS")
    require(receipt["scope"] == "EXISTING_AUDITED_6700_BEFORE_NEW_ACQUISITION" and
            receipt["new_acquisition_members_included"] == 0, "wrong source generation scope")
    if synthetic:
        require(receipt.get("synthetic_fixture") is True, "synthetic override requires an explicitly synthetic handoff")
        expected = receipt["counts_by_source"]
        require(set(expected) == set(COUNTS) and all(type(n) is int and n > 0 for n in expected.values()), "synthetic group counts invalid")
        expected_old = receipt["old3018_split_counts"]
    else:
        require(not receipt.get("synthetic_fixture", False), "synthetic handoff cannot become real data")
        expected, expected_old = COUNTS, OLD_COUNTS
    require(receipt["counts_by_source"] == expected and receipt["core_members"] == sum(expected.values()), "frozen source counts differ")
    require(receipt["old3018_split_counts"] == expected_old and receipt["old3018_joined"] == sum(expected_old.values()), "old membership counts differ")
    require(receipt["cross_source_duplicates"] == 0 and not csv_rows(paths["CROSS_SOURCE_DUPLICATES.csv"]), "cross-source duplicates must not be silently removed")
    require(all(receipt["checks"].get(k) is True for k in
                ("all_source_hashes_consumed_verified", "member_count_matches_prior_audit", "source_labels_copied_not_reextracted", "exact_geometry_hash_join", "old_holdout_roles_preserved")), "owner source-level audit incomplete")
    require(contracts["development_contract"] == contract_pin, "old runtime contract pin differs from owner contract")
    require(contract["schema"] == "bb_research_runtime_contract.v1" and contract["units"] == contracts["geometry_units"] == "um", "runtime contract/units differ")
    fields = contract["field_names"]
    require(len(fields) == len(set(fields)) == 10 and fields == contracts["geometry_order"], "exact 10D order differs; no implicit IO conversion")
    require(contract["lower"] == contracts["geometry_bounds"]["lower"] and contract["upper"] == contracts["geometry_bounds"]["upper"], "geometry bounds differ")
    lower, upper = np.asarray(contract["lower"], float), np.asarray(contract["upper"], float)
    require(lower.shape == upper.shape == (10,) and np.isfinite(lower).all() and np.isfinite(upper).all() and (lower < upper).all(), "bad contract bounds")
    require(contract["port_contract"] == contracts["port_contract"], "port contract differs")
    require(contracts["label_frequency_hz"] == FREQUENCY and contracts["full_frequency_hz"] == FREQUENCY_HZ.tolist() and contracts["target_columns"] == list(Y_COLUMNS), "source label/frequency contract differs")
    require(old["schema"] == "bb_splits.v1" and type(old["seed"]) is int and old["seed"] == 17 and
            old["contract_fingerprint_sha256"] == contracts["scientific_fingerprint"], "old split contract/seed differs")
    require(old["requested_fractions"] == [.6, .2, .2] and old["counts"] == expected_old, "old split counts/fractions differ")
    old_map = old["by_geometry_sha256"]
    require(len(old_map) == sum(expected_old.values()) and dict(Counter(old_map.values())) == expected_old, "old split identity counts differ")
    join = csv_rows(paths["OLD3018_MEMBERSHIP_JOIN.csv"])
    require(len(join) == len(old_map) and len({r["geometry_sha256"] for r in join}) == len(join), "old join missing/duplicate geometry")
    require(sorted(int(r["subset_row"]) for r in join) == list(range(len(join))), "old subset indices must be a complete unique permutation")
    join_map = {r["geometry_sha256"]: r for r in join}
    require(set(join_map) == set(old_map), "old3018 not fully joined")
    for digest, row in join_map.items():
        require(row["split"] == old_map[digest] == SPLIT_NAMES[_split_for_hash(digest, 17)], "old membership changed; cannot reassign")
        require(old["geometry_id_to_sha256"].get(row["geometry_id"]) == digest, "old geometry ID differs")
    members = csv_rows(paths["CORE_MEMBERS_6700.csv"])
    require(len(members) == receipt["core_members"] and dict(Counter(r["source_group"] for r in members)) == expected, "actual member rows do not reconcile")
    member_map = {r["geometry_sha256"]: r for r in members}
    require(len(member_map) == len(members) and all(re.fullmatch('[0-9a-f]{64}', h) for h in member_map), "duplicate/malformed member hashes")
    require(all(member_map.get(h, {}).get("source_group") in INCLUDED for h in old_map), "old3018 not all in the selected formal view")
    evidence = {}
    with paths["MEMBER_EVIDENCE.jsonl"].open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            record = json.loads(line, object_pairs_hook=_pairs, parse_constant=_constant)
            h = record["geometry_sha256"]
            require(h in member_map and h not in evidence and record["source_group"] == member_map[h]["source_group"], "member evidence identity/group mismatch")
            evidence[h] = (line_number, record)
    require(set(evidence) == set(member_map), "member evidence incomplete")
    selected, excluded, geometries, labels, physicals, codes, ids, hashes, descriptors, srfs = ([] for _ in range(10))
    for member_line, row in enumerate(members, 2):
        h = row["geometry_sha256"]
        if row["source_group"] not in INCLUDED:
            excluded.append({**row, "exclusion_reason": "HISTORICAL_RESEARCH_NOT_SELECTED" if row["source_group"] == "HISTORICAL_RESEARCH" else "ORIGINAL64_VALIDATION_NOT_TRAINING_DATA"})
            continue
        line_number, ev = evidence[h]
        raw, label_pin = ev["source_record"], ev["label_source"]["original_identity"]
        require(label_pin["path"] == row["label_source_path"] and label_pin["sha256"] == row["label_source_sha256"] and type(label_pin["bytes"]) is int and label_pin["bytes"] > 0, "original label source pin differs")
        require(int(row["label_source_row_1based_including_header"]) >= 2, "formal source row missing")
        require(ev["full_frequency_hz"] == contracts["full_frequency_hz"], "owner full56 evidence reference differs")
        require(raw["geometry_sha256"] == h and raw["geometry_id"] == row["geometry_id"] and
                raw["accepted_sequence"] == row["production_accepted_sequence"], "source record geometry/accepted sequence differs")
        require(int(row["frequency_hz"]) == FREQUENCY and
                ("frequency_hz" not in raw or int(raw["frequency_hz"]) == FREQUENCY), "not exact15GHz")
        g = [finite(row["geom__" + field]) for field in fields]
        require(all(a == finite(raw["geom__" + f]) for a, f in zip(g, fields)), "original geometry values changed")
        require(np.all(np.asarray(g) >= lower) and np.all(np.asarray(g) <= upper), "geometry outside unchanged contract bounds")
        physical = [finite(row[k]) for k in PHYSICAL_COLUMNS]
        require(all(value == finite(raw[k]) for value, k in zip(physical, PHYSICAL_COLUMNS)), "original15 physical labels changed")
        require(physical[4] == min(physical[2], physical[3]) and physical[6] == abs(physical[5]), "Qmin/absK derived labels differ")
        target = [physical[j] for j in (0, 1, 4, 6)]
        require(flag(row["strict_lumped_valid"]) and flag(raw["strict_lumped_valid"]) and
                flag(row["below_half_srf"]) and flag(raw["below_half_srf"]) and flag(raw["broadband_descriptor_valid"]), "formal strict/SRF/descriptor source flags differ")
        require(flag(row["core_eligible"]) and .5 <= target[0] <= 2 and .5 <= target[1] <= 2 and .2 <= target[3] <= .85, "formal source not strict core; do not drop/reselect rows")
        require(flag(row["q10_20_supported"]) == (10 <= target[2] <= 20), "Q intersection flag differs")
        split = SPLIT_NAMES[_split_for_hash(h, 17)]
        if h in old_map:
            jr = join_map[h]
            require(row["old_3018_split"] == jr["split"] == split and
                    row["old_3018_subset_row"] == jr["subset_row"] and row["old_3018_source_row"] == jr["source_row"], "old member row linkage changed")
        else:
            require(row["old_3018_split"] == "NOT_IN_OLD_3018" and not row["old_3018_subset_row"] and not row["old_3018_source_row"], "unexpected old membership claim")
        selected.append({**row, "view_row": len(selected), "assigned_development_split": split,
            "handoff_member_line": member_line, "handoff_evidence_line": line_number,
            "label_source_bytes": label_pin["bytes"], "label_evidence_policy": "OWNER_AUDITED_SOURCE_ROW_NOT_NATIVE_REVALIDATED"})
        geometries.append(g); labels.append(target); physicals.append(physical); codes.append(SPLIT_NAMES.index(split))
        ids.append(row["geometry_id"]); hashes.append(h); descriptors.append(True); srfs.append(row["srf_status"])
    require(len(selected) == sum(expected[g] for g in INCLUDED) and len(set(ids)) == len(selected), "formal selection/ID count differs")
    require(len({tuple(g) for g in geometries}) == len(geometries), "duplicate formal geometry vectors")
    arrays = dict(geometry=np.asarray(geometries, np.float64), y=np.asarray(labels, np.float64)[:, None, :],
        physical_features=np.asarray(physicals, np.float64)[:, None, :], split=np.asarray(codes, np.int8),
        geometry_ids=np.asarray(ids), geometry_sha256=np.asarray(hashes), frequency_hz=np.asarray([FREQUENCY], np.int64),
        strict_lumped_valid=np.ones((len(selected), 1), bool), broadband_descriptor_valid=np.asarray(descriptors, bool)[:, None],
        below_half_srf=np.ones((len(selected), 1), bool), srf_status=np.asarray(srfs)[:, None])
    arrays["y_valid"] = arrays["strict_lumped_valid"][..., None] & np.isfinite(arrays["y"])
    arrays["physical_valid"] = arrays["broadband_descriptor_valid"][..., None] & np.isfinite(arrays["physical_features"])
    counts = {name: int((arrays["split"] == i).sum()) for i, name in enumerate(SPLIT_NAMES)}
    train = arrays["split"] == 0
    require(counts["train"] >= 2 and counts["validation"] > 0, "insufficient development train/validation")
    ymean, yscale = _fit_scale(arrays["y"][train], arrays["y_valid"][train])
    scope = "SYNTHETIC_TEST_ONLY" if synthetic else "DEVELOPMENT_CURRENT_SNAPSHOT"
    capability = dict(supported="BB00_FREQUENCY_TANDEM_ONLY", available_frequency_hz=[FREQUENCY],
        broadband="NOT_SUPPORTED", s_parameters="NOT_INCLUDED_NOT_SUPPORTED", other_55_frequencies="NOT_INCLUDED_NOT_FABRICATED",
        generic_bundle_batch_with_s="NOT_SUPPORTED_USE_BB00_FREQUENCY_ENTRYPOINT")
    norm = dict(schema="bb_normalizer.v1", fit_split="train", training_geometries=counts["train"], scale_floor=1e-6,
        g_min=arrays["geometry"][train].min(0).tolist(), g_max=arrays["geometry"][train].max(0).tolist(),
        y_mean=ymean, y_scale=yscale, s_mean=None, s_scale=None, s_columns=[], s_status="NOT_SUPPORTED_NO_S_DATA",
        field_names=fields, geometry_fields=fields, y_columns=list(Y_COLUMNS), y_units=contracts["units"],
        contract_bounds_um=dict(lower=contract["lower"], upper=contract["upper"]), capability=capability)
    splits = dict(schema="bb_splits.v1", seed=17, method=old["method"], requested_fractions=[.6,.2,.2],
        contract_fingerprint_sha256=old["contract_fingerprint_sha256"], counts=counts, previous_splits=old_pin,
        by_geometry_sha256={h:SPLIT_NAMES[c] for h,c in zip(hashes,codes)}, geometry_id_to_sha256=dict(zip(ids,hashes)),
        ids={name:[identity for identity,c in zip(ids,codes) if c==i] for i,name in enumerate(SPLIT_NAMES)},
        old_membership_retained=expected_old, parent_prototype_status=receipt["parent_prototype_status"],
        family_independence="NOT_CERTIFIED", final_independence="NOT_CERTIFIED_DEVELOPMENT_ONLY")
    with (out/"dataset.npz").open("xb") as stream: np.savez_compressed(stream, **arrays)
    _write_json(out/"normalizer.json", norm); _write_json(out/"splits.json", splits)
    with (out/"contract.json").open("xb") as stream: stream.write(contract_path.read_bytes())
    write_csv(out/"SOURCE_ROWS.csv", selected, list(selected[0]))
    write_csv(out/"EXCLUSIONS.csv", excluded, list(excluded[0]))
    _write_json(out/"SOURCE_MANIFEST.json", dict(schema="eucap15_formal_view_sources.v1", scope=scope,
        handoff_manifest=handoff_pin, consumed_handoff_artifacts={n:artifacts[n] for n in needed},
        previous_splits=old_pin, runtime_contract=contract_pin, implementation=pin(__file__),
        source_audit_policy="REUSE_OWNER_AUDITED_PHYSICAL_CHAINS; no raw56/S4P or original native source reopened",
        source_contract=contracts, parent_prototype_status=receipt["parent_prototype_status"]))
    artifact_pins = {p.name:dict(path=p.name,sha256=sha256(p),size_bytes=p.stat().st_size) for p in sorted(out.iterdir()) if p.is_file()}
    data_manifest = dict(schema="bb_data_manifest.v1", status="PASS", evidence=scope, source_manifest=handoff_pin,
        contract_fingerprint_sha256=contracts["scientific_fingerprint"], unique_geometries=len(selected), frequency_rows=len(selected),
        geometry_dim=10, geometry_fields=fields, geometry_field_order=fields, geometry_units="um",
        geometry_bounds={f:[lo,hi] for f,lo,hi in zip(fields,contract["lower"],contract["upper"])},
        frequency_hz=[FREQUENCY], target_columns=list(Y_COLUMNS), physical_columns=list(PHYSICAL_COLUMNS), s_channels=[],
        port_contract=contracts["port_contract"], split_counts=counts, normalizer_fit_split="train", capability=capability,
        original_source_counts=expected, selected_source_counts={g:expected[g] for g in INCLUDED},
        excluded_source_counts={g:n for g,n in expected.items() if g not in INCLUDED},
        physical_validity="Original owner-audited strict/descriptor/half-SRF flags and exact15 labels; no physical re-extraction",
        test_usage="Membership and source-label preparation only; no test predictions/evaluation/model selection or test-derived normalization",
        parent_prototype_status=receipt["parent_prototype_status"], family_independence="NOT_CERTIFIED", FINAL_status="NOT_FINAL",
        artifacts=artifact_pins)
    _write_json(out/"data_manifest.json", data_manifest)
    from .training import Bundle
    from .bb00 import prepare_bb00
    bundle=Bundle(out)
    require(set(bundle.arrays)==set(arrays) and all(np.array_equal(bundle.arrays[k],v) for k,v in arrays.items()), "Bundle array round-trip differs")
    bb_norm, bb_train, bb_val, fi, exposure=prepare_bb00(bundle,contract,SPANS,frequency_ghz=15)
    require(fi==0 and np.array_equal(bb_train,np.flatnonzero(train)) and
            np.array_equal(bb_val,np.flatnonzero(arrays["split"]==1)), "BB00 exact15 eligible split differs")
    _write_json(out/"BB00_NORMALIZER_15GHZ.json", bb_norm)
    result=dict(schema="eucap15_formal_development_view_receipt.v1",status="PASS_PREPARED_BUNDLE_BB00",scope=scope,
        created_utc=utc_now(), data_root=str(out), data_manifest=pin(out/"data_manifest.json"),dataset=pin(out/"dataset.npz"),
        contract=pin(out/"contract.json"),bb00_normalizer=pin(out/"BB00_NORMALIZER_15GHZ.json"),source_manifest=pin(out/"SOURCE_MANIFEST.json"),
        original_owner_members=len(members), selected_formal_geometries=len(selected), split_counts=counts,
        selected_source_counts=data_manifest["selected_source_counts"],excluded_source_counts=data_manifest["excluded_source_counts"],
        old_membership_retained=expected_old,eligible_rows=exposure,
        q_scope="ALL_Q_NO_CUT",q_below10_retained=int((arrays["y"][:,0,2]<10).sum()),q_above20_retained=int((arrays["y"][:,0,2]>20).sum()),
        capability=capability,parent_prototype_status=receipt["parent_prototype_status"],family_independence="NOT_CERTIFIED",FINAL_status="NOT_FINAL",
        training_calls=0,model_loads=0,native_calls=0,test_evaluation_calls=0,old_npz_reads=0,
        original56_source_reads=0,normalizer_fit="VALID_TRAIN_ONLY",all_old_hashes_joined_and_split_unchanged=True,
        physical_evidence="OWNER_SOURCE_LEVEL_AUDIT_REUSED_NOT_NEW_NATIVE_QA")
    _write_json(out/"DATA_RECEIPT.json",result)
    with (out/"SHA256SUMS").open("x",encoding="utf-8") as stream:
        for p in sorted(out.iterdir()):
            if p.is_file() and p.name!="SHA256SUMS":stream.write(f"{sha256(p)}  {p.name}\n")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-manifest-pin",required=True,help="JSON object path/sha256/bytes")
    parser.add_argument("--old-splits-pin",required=True,help="JSON object path/sha256/bytes")
    parser.add_argument("--contract-pin",required=True,help="JSON object path/sha256/bytes")
    parser.add_argument("--out",required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(json.loads(args.handoff_manifest_pin),json.loads(args.old_splits_pin),
                             json.loads(args.contract_pin),args.out),allow_nan=False))


if __name__=="__main__": main()
