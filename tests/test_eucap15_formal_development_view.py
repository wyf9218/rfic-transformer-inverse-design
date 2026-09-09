"""Small synthetic handoff only; no owner data/models/native programs."""
from collections import Counter
import csv
import hashlib
import json

import numpy as np
import pytest

from research.broadband56_nn import eucap15_formal_development_view as view
from research.broadband56_nn.bb00 import prepare_bb00
from research.broadband56_nn.data import _split_for_hash, SPLIT_NAMES, FREQUENCY_HZ
from research.broadband56_nn.training import Bundle
from rfic_transformer_inverse_design.synthesis.frozen_mlp import GEOMETRY_COLUMNS


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")


def table(path, rows, fields=None):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class Handoff:
    def __init__(self, root):
        self.root = root.resolve()
        self.root.mkdir()
        self.handoff = self.root / "handoff"
        self.handoff.mkdir()
        self.fields = [name.removeprefix("geom__") for name in GEOMETRY_COLUMNS]
        self.contract = dict(schema="bb_research_runtime_contract.v1", units="um", field_names=self.fields,
                             lower=[0.] * 10, upper=[100.] * 10,
                             port_contract={"ports": 4, "order": [0, 1, 3, 2], "z0": 50})
        # Exactly two train, one validation and one test are the retained old set.
        by_code = {0: [], 1: [], 2: []}
        number = 0
        while any(len(items) < 6 for items in by_code.values()):
            h = hashlib.sha256(f"synthetic-formal-{number}".encode()).hexdigest()
            by_code[_split_for_hash(h, 17)].append(h)
            number += 1
        old_hashes = by_code[0][:2] + by_code[1][:1] + by_code[2][:1]
        hashes = old_hashes + by_code[0][2:4] + by_code[1][1:3] + by_code[2][1:3]
        groups = ["FORMAL_BASE_20973"] * 9 + ["STAGE238_INCREMENT"]
        hashes += [hashlib.sha256(label.encode()).hexdigest() for label in ("history", "original64")]
        groups += ["HISTORICAL_RESEARCH", "ORIGINAL64_VALIDATION"]
        self.join = [dict(geometry_sha256=h, subset_row=str(i), source_row=str(40+i),
                          geometry_id=f"g{i}", split=SPLIT_NAMES[_split_for_hash(h, 17)], core_q10_20="True")
                     for i, h in enumerate(old_hashes)]
        old_counts = dict(Counter(row["split"] for row in self.join))
        self.old = dict(schema="bb_splits.v1", seed=17, contract_fingerprint_sha256="f"*64,
                        requested_fractions=[.6, .2, .2], counts=old_counts, method="bb56-split-v1 60/20/20",
                        by_geometry_sha256={r["geometry_sha256"]:r["split"] for r in self.join},
                        geometry_id_to_sha256={r["geometry_id"]:r["geometry_sha256"] for r in self.join})
        self.receipt = dict(schema="eucap15_core_membership_handoff.v1", synthetic_fixture=True,
            status="PASS_MEMBERSHIP_AND_LABEL_SOURCE_FREEZE", scope="EXISTING_AUDITED_6700_BEFORE_NEW_ACQUISITION",
            new_acquisition_members_included=0, counts_by_source=dict(Counter(groups)), core_members=len(groups),
            old3018_joined=len(old_hashes), old3018_split_counts=old_counts, cross_source_duplicates=0,
            parent_prototype_status="UNKNOWN_NO_MAPPING_IN_CONSUMED_RECORDS", checks={k:True for k in (
                "all_source_hashes_consumed_verified", "member_count_matches_prior_audit", "source_labels_copied_not_reextracted",
                "exact_geometry_hash_join", "old_holdout_roles_preserved")})
        self.contracts = dict(geometry_order=self.fields, geometry_units="um",
            geometry_bounds={"lower":self.contract["lower"],"upper":self.contract["upper"]},
            port_contract=self.contract["port_contract"], label_frequency_hz=view.FREQUENCY,
            full_frequency_hz=FREQUENCY_HZ.tolist(), target_columns=list(view.Y_COLUMNS),
            scientific_fingerprint="f"*64, units=["nH","nH","dimensionless","dimensionless"])
        self.members, self.evidence = [], []
        for i, (h, group) in enumerate(zip(hashes, groups)):
            q = 9.5 if i == 0 else 21. if i == 1 else 10.+i/4
            signed = -.25-i/100
            labels = dict(lp_nh=str(.6+i/50), ls_nh=str(.7+i/50), qp=str(q), qs=str(q+2),
                          qmin=str(q), signed_k=str(signed), k_abs=str(abs(signed)))
            geometry = {"geom__"+f:str(5+i+j/10) for j,f in enumerate(self.fields)}
            source_pin = dict(path=f"/synthetic_native/source_{group}.csv", sha256="a"*64, bytes=700)
            raw = dict(geometry_sha256=h, geometry_id=f"g{i}", accepted_sequence=str(i+1),
                frequency_hz=str(view.FREQUENCY), strict_lumped_valid="true", below_half_srf="true",
                broadband_descriptor_valid="true", srf_status="FOUND_ABOVE_30GHZ", **labels, **geometry)
            row = dict(geometry_sha256=h, geometry_id=f"g{i}", source_group=group,
                production_accepted_sequence=str(i+1), frequency_hz=str(view.FREQUENCY), **labels,
                strict_lumped_valid="true", below_half_srf="true", srf_status=raw["srf_status"],
                core_eligible="True", q10_20_supported=str(10 <= q <= 20),
                old_3018_split=self.join[i]["split"] if i < 4 else "NOT_IN_OLD_3018",
                old_3018_subset_row=str(i) if i<4 else "", old_3018_source_row=str(40+i) if i<4 else "",
                parent_geometry_sha256="UNKNOWN", prototype_id="UNKNOWN", label_source_path=source_pin["path"],
                label_source_sha256=source_pin["sha256"], label_source_row_1based_including_header=str(i+2), **geometry)
            self.members.append(row)
            self.evidence.append(dict(geometry_sha256=h, source_group=group, source_record=raw,
                label_source={"original_identity":source_pin}, full_frequency_hz=FREQUENCY_HZ.tolist()))

    def freeze(self):
        dump(self.root/"contract.json", self.contract)
        dump(self.root/"splits.json", self.old)
        self.contracts["development_contract"] = view.pin(self.root/"contract.json")
        dump(self.handoff/"RECEIPT.json", self.receipt)
        dump(self.handoff/"CONTRACT_PINS.json", self.contracts)
        table(self.handoff/"CORE_MEMBERS_6700.csv", self.members)
        table(self.handoff/"OLD3018_MEMBERSHIP_JOIN.csv", self.join)
        table(self.handoff/"CROSS_SOURCE_DUPLICATES.csv", [], ["geometry_sha256","first_source","second_source"])
        (self.handoff/"MEMBER_EVIDENCE.jsonl").write_text("".join(json.dumps(row,allow_nan=False)+"\n" for row in self.evidence))
        dump(self.handoff/"MANIFEST.json", dict(schema="private_data_handoff_manifest.v1",
            artifacts=[view.pin(p) for p in sorted(self.handoff.iterdir()) if p.name != "MANIFEST.json"]))
        return (view.pin(self.handoff/"MANIFEST.json"), view.pin(self.root/"splits.json"), view.pin(self.root/"contract.json"))


@pytest.fixture
def handoff(tmp_path):
    return Handoff(tmp_path / "synthetic")


def test_prepare_bundle_exact15_all_q_old_roles_and_exclusions(handoff):
    pins = handoff.freeze()
    source_before = {p: p.read_bytes() for p in handoff.root.rglob("*") if p.is_file()}
    out = handoff.root/"view"
    receipt = view.prepare(*pins, out, synthetic=True)
    assert receipt["scope"] == "SYNTHETIC_TEST_ONLY"
    assert receipt["selected_formal_geometries"] == 10
    assert receipt["split_counts"] == {"train":4, "validation":3, "test":3}
    assert receipt["old_membership_retained"] == {"train":2,"validation":1,"test":1}
    assert receipt["q_below10_retained"] == receipt["q_above20_retained"] == 1
    assert receipt["excluded_source_counts"] == {"HISTORICAL_RESEARCH":1,"ORIGINAL64_VALIDATION":1}
    assert all(receipt[k] == 0 for k in ("training_calls","model_loads","native_calls","test_evaluation_calls","old_npz_reads","original56_source_reads"))
    assert all(p.read_bytes() == content for p, content in source_before.items())
    bundle = Bundle(out)
    assert bundle.arrays["geometry"].shape == (10,10)
    assert bundle.arrays["y"].shape == (10,1,4)
    assert bundle.arrays["physical_features"].shape == (10,1,7)
    assert bundle.arrays["frequency_hz"].tolist() == [15_000_000_000]
    assert bundle.arrays["y_valid"].all() and bundle.arrays["strict_lumped_valid"].all()
    assert "s" not in bundle.arrays and bundle.norm["s_mean"] is None and bundle.norm["s_scale"] is None
    assert bundle.norm["capability"]["s_parameters"] == "NOT_INCLUDED_NOT_SUPPORTED"
    assert bundle.manifest["frequency_rows"] == 10
    assert bundle.manifest["FINAL_status"] == "NOT_FINAL"
    for i in range(10):
        row = handoff.members[i]
        assert bundle.arrays["geometry_sha256"][i] == row["geometry_sha256"]
        assert bundle.arrays["geometry"][i].tolist() == [float(row["geom__"+f]) for f in handoff.fields]
        assert bundle.arrays["y"][i,0].tolist() == [float(row[k]) for k in view.Y_COLUMNS]
    norm, train, val, fi, exposure = prepare_bb00(bundle,handoff.contract,view.SPANS,frequency_ghz=15)
    assert fi == 0 and len(train)==4 and len(val)==3 and exposure["test"]["eligible_geometries"]==3
    np.testing.assert_allclose(norm["y_mean"],bundle.arrays["y"][train,0].mean(0),rtol=0,atol=0)
    np.testing.assert_allclose(bundle.norm["y_mean"],norm["y_mean"],rtol=0,atol=0)
    assert not np.array_equal(norm["y_mean"],bundle.arrays["y"][:,0].mean(0))
    splits = view.document(out/"splits.json")
    assert all(splits["by_geometry_sha256"][h] == role for h,role in handoff.old["by_geometry_sha256"].items())
    for line in (out/"SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert view.sha256(out/name)==digest
    with pytest.raises(FileExistsError): view.prepare(*pins,out,synthetic=True)
    assert not (out/"PREPARATION_FAILED.json").exists()


@pytest.mark.parametrize("attack", ["qmin","abs_k","strict","descriptor","frequency","order","ports",
    "old_split","old_missing","old_member_role","group_count","duplicate_hash","geometry","label_pin","q_flag","full56"])
def test_bad_handoff_fails_closed_retains_failure(handoff, attack):
    row, ev = handoff.members[0], handoff.evidence[0]
    if attack == "qmin": row["qmin"] = ev["source_record"]["qmin"] = "9.6"
    elif attack == "abs_k": row["k_abs"] = ev["source_record"]["k_abs"] = ".3"
    elif attack == "strict": row["strict_lumped_valid"] = "false"
    elif attack == "descriptor": ev["source_record"]["broadband_descriptor_valid"] = "false"
    elif attack == "frequency": row["frequency_hz"] = "14000000000"
    elif attack == "order": handoff.contracts["geometry_order"] = list(reversed(handoff.fields))
    elif attack == "ports": handoff.contracts["port_contract"] = {"ports":2}
    elif attack == "old_split": handoff.join[0]["split"] = "test"
    elif attack == "old_missing": handoff.join.pop()
    elif attack == "old_member_role": row["old_3018_split"] = "test"
    elif attack == "group_count": handoff.receipt["counts_by_source"]["FORMAL_BASE_20973"] += 1
    elif attack == "duplicate_hash": handoff.members[1]["geometry_sha256"] = row["geometry_sha256"]
    elif attack == "geometry": row["geom__"+handoff.fields[0]] = "6"
    elif attack == "label_pin": row["label_source_sha256"] = "b"*64
    elif attack == "q_flag": row["q10_20_supported"] = "True"
    elif attack == "full56": ev["full_frequency_hz"] = [15_000_000_000]
    pins = handoff.freeze()
    out = handoff.root / "failed"
    with pytest.raises(ValueError): view.prepare(*pins,out,synthetic=True)
    assert view.document(out/"PREPARATION_FAILED.json")["status"] == "FAIL_PRESERVED"
    assert not (out/"DATA_RECEIPT.json").exists()


def test_synthetic_cannot_use_real_mode(handoff):
    pins = handoff.freeze()
    with pytest.raises(ValueError,match="synthetic handoff cannot"): view.prepare(*pins,handoff.root/"rejected")


def test_real_fixed_counts_cannot_be_replaced_by_fixture_counts(handoff):
    handoff.receipt.pop("synthetic_fixture")
    pins = handoff.freeze()
    with pytest.raises(ValueError,match="source counts differ"): view.prepare(*pins,handoff.root/"rejected")


def test_consumed_pin_change_is_rejected(handoff):
    pins = handoff.freeze()
    p = handoff.handoff / "CORE_MEMBERS_6700.csv"
    p.write_bytes(p.read_bytes().replace(b"9.5",b"9.6",1))
    with pytest.raises(ValueError): view.prepare(*pins,handoff.root/"rejected",synthetic=True)


def test_json_duplicate_and_nonfinite_rejected(tmp_path):
    path = tmp_path/"bad.json"
    path.write_text('{"strict":true,"strict":false}')
    with pytest.raises(ValueError,match="duplicate JSON"):view.document(path)
    path.write_text('{"label":NaN}')
    with pytest.raises(ValueError,match="nonfinite JSON"):view.document(path)


def test_path_symlink_not_followed(handoff):
    pins = list(handoff.freeze())
    link = handoff.root/"contract_link.json"
    link.symlink_to(handoff.root/"contract.json")
    pins[2] = {**pins[2],"path":str(link)}
    with pytest.raises(ValueError,match="non-symlink"):view.prepare(*pins,handoff.root/"rejected",synthetic=True)


def test_nonfinite_original_label_rejected(handoff):
    handoff.members[0]["qp"] = handoff.evidence[0]["source_record"]["qp"] = "nan"
    with pytest.raises(ValueError,match="nonfinite"):view.prepare(*handoff.freeze(),handoff.root/"rejected",synthetic=True)
