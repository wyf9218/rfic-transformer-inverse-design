"""Join existing audited membership to one frozen reference's gradient ledger.

This is a metadata derivation, not another checkpoint load or data preparation.
It does not certify parent-family independence, other models, or FINAL eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

from .eucap15_formal_development_view import checked, csv_rows, document, pin, require, write_csv
from .data import _write_json
from .io import utc_now


REFERENCE_SHA = "76bffaa70c4af9740f586273cd1cdda335f92be8a0b23d535920c35319f97ccc"
VERIFIER_SHA = "b46e1aa844e0b39101118c66ddcdd802801f8c5ea1009fd2dfae1553c294f2e7"
SPLITS = ("train", "validation", "test")


def unique(rows):
    result = {row["geometry_sha256"]: row for row in rows}
    require(len(result) == len(rows), "duplicate geometry in input membership")
    return result


def flag(value):
    require(value in ("True", "False"), "invalid audited membership boolean")
    return value == "True"


def derive(reference, membership, members, view_splits):
    """Pure join; source identity and prior audit checks are the CLI's duty."""
    old, core = unique(membership), unique(members)
    require(reference["source_snapshot_geometries"] == len(old), "source membership count differs")
    require(reference["reference_label"] == "FORMAL10K_REFERENCE", "wrong reference scope")
    require(reference["status"] == "REFERENCE_LOADED_NOT_FINAL", "reference load not proven")
    eligible = Counter()
    for row in old.values():
        require(row["split"] in SPLITS, "invalid old split")
        strict, finite = flag(row["strict_lumped_valid"]), flag(row["four_labels_finite"])
        mask = strict and finite
        require(flag(row["old_strict_eligible"]) == mask, "eligibility mask differs")
        if mask:
            eligible[row["split"]] += 1
    require({s: eligible[s] for s in SPLITS} == {
        s: reference["eligible_rows"][s]["eligible_geometries"] for s in SPLITS
    }, "eligible membership differs from checkpoint audit")
    for role in ("forward", "inverse"):
        for choice in ("best", "last"):
            require(reference["roles"][role][choice]["unique_gradient_geometries"] == eligible["train"],
                    "not every eligible train geometry was seen; exact row exposure cannot be inferred")
    require(set(view_splits) <= set(core), "development view has unknown members")
    output = []
    for h, row in sorted(core.items()):
        prev = old.get(h)
        split = prev["split"] if prev else "NOT_IN_REFERENCE_SNAPSHOT"
        is_eligible = flag(prev["old_strict_eligible"]) if prev else False
        seen = bool(prev and split == "train" and is_eligible)
        reason = ("AUDITED_ALL_ELIGIBLE_TRAIN_SEEN" if seen else
                  "AUDITED_NON_TRAIN_SPLIT" if prev and split != "train" else
                  "AUDITED_TRAIN_INELIGIBLE" if prev else "OUTSIDE_AUDITED_SOURCE_SNAPSHOT")
        current = view_splits.get(h, "EXCLUDED_FROM_DEVELOPMENT6329")
        require(current in (*SPLITS, "EXCLUDED_FROM_DEVELOPMENT6329"), "invalid view split")
        output.append({
            "geometry_sha256": h, "source_group": row["source_group"],
            "reference_model_id": reference["model_id"],
            "reference_source_split": split, "reference_strict_eligible": is_eligible,
            "reference_gradient_seen_f_best_last_i_best_last": seen,
            "reference_exposure_evidence": reason, "current_development_split": current,
            "parent_geometry_sha256": row["parent_geometry_sha256"],
            "prototype_id": row["prototype_id"],
            "all_historical_model_exposure": "UNKNOWN_NOT_AUDITED",
            "FINAL_independence": "NOT_CERTIFIED",
        })
    counts = Counter((r["reference_exposure_evidence"], r["current_development_split"]) for r in output)
    cross = [{"reference_exposure_evidence": a, "current_development_split": b, "n": n}
             for (a, b), n in sorted(counts.items())]
    conflicts = [r["geometry_sha256"] for r in output if
                 r["reference_gradient_seen_f_best_last_i_best_last"] and
                 r["current_development_split"] in ("validation", "test")]
    return output, cross, conflicts


def run(config_pin, out):
    out = Path(out).absolute()
    require(".." not in out.parts and not any(p.is_symlink() for p in (out, *out.parents)), "unsafe output")
    config_path = checked(config_pin)
    config = document(config_path)
    require(config["schema"] == "eucap15_reference_exposure_inputs.v1", "wrong config")
    inputs = {"input_config": config_pin, **config["inputs"]}
    paths = {k: checked(v) for k, v in inputs.items()}
    require(inputs["reference"]["sha256"] == REFERENCE_SHA and
            inputs["reference_verifier"]["sha256"] == VERIFIER_SHA, "audited reference or verifier changed")
    require(inputs["reference_inputs"]["sha256"] == "ae0287256c622a9bb92fa240df230f4f1fdd3a563cb2666ebe1141728b434633",
            "prior reference source freeze changed")
    prior_inputs = document(paths["reference_inputs"])
    require(prior_inputs["source_pins"]["research/broadband56_nn/bb00.py"] == inputs["reference_training_source"],
            "training source no longer bound to prior checkpoint audit")
    ref, load_receipt, load_delivery, domain_manifest, owner_manifest, view_receipt = (
        document(paths[k]) for k in ("reference", "load_receipt", "load_delivery", "domain_manifest", "owner_manifest", "view_receipt"))
    require(load_receipt["status"] == "PASS_REFERENCE_IDENTITY_AND_BOUNDED_INFERENCE" and
            inputs["reference"] in load_receipt["references"], "reference not bound by load receipt")
    require(load_delivery["run_receipt"]["sha256"] == inputs["load_receipt"]["sha256"] and
            load_delivery["script"]["sha256"] == VERIFIER_SHA and
            load_delivery["formal_reference"]["sha256"] == REFERENCE_SHA, "delivery chain differs")
    require(load_delivery["status"] == "COMPLETE_SCOPED_REFERENCE_LOAD_NOT_FINAL_MODEL", "load delivery not complete")

    def artifact(manifest, name):
        items = [p for p in manifest["artifacts"] if Path(p["path"]).name == name]
        require(len(items) == 1, "missing/duplicate manifest artifact: " + name)
        item = items[0]
        inputs[name] = item
        return checked(item)

    membership_path = artifact(domain_manifest, "eligibility_membership.csv")
    domain = document(artifact(domain_manifest, "eligibility_15ghz.json"))
    require(domain["reference"] == inputs["reference"] and
            domain["source_dataset"] == ref["data"]["dataset.npz"] and
            domain["source_split"] == ref["data"]["splits.json"], "domain/reference identities differ")
    require(domain["status"] == "PASS_SINGLE_FROZEN_SNAPSHOT_DOMAIN_AUDIT", "domain audit not PASS")
    core_path = artifact(owner_manifest, "CORE_MEMBERS_6700.csv")
    owner = document(artifact(owner_manifest, "RECEIPT.json"))
    require(owner["status"] == "PASS_MEMBERSHIP_AND_LABEL_SOURCE_FREEZE", "owner membership not frozen")
    vm = document(checked(view_receipt["data_manifest"]))
    inputs["view_manifest"] = view_receipt["data_manifest"]
    split_item = vm["artifacts"]["splits.json"]
    split_pin = {"path": str(Path(view_receipt["data_root"]) / split_item["path"]),
                 "sha256": split_item["sha256"], "bytes": split_item["size_bytes"]}
    inputs["view_splits"] = split_pin
    view_splits = document(checked(split_pin))["by_geometry_sha256"]
    require(view_receipt["scope"] == "DEVELOPMENT_CURRENT_SNAPSHOT" and
            len(view_splits) == view_receipt["selected_formal_geometries"], "wrong development view")
    source_manifest = document(checked(view_receipt["source_manifest"]))
    inputs["view_source_manifest"] = view_receipt["source_manifest"]
    require(source_manifest["handoff_manifest"] == inputs["owner_manifest"], "view is not bound to owner handoff")
    members = csv_rows(core_path)
    require(len(members) == owner["core_members"], "owner member count differs")
    out.mkdir(parents=True, exist_ok=False)
    try:
        rows, cross, conflicts = derive(ref, csv_rows(membership_path), members, view_splits)
        write_csv(out / "REFERENCE_EXPOSURE_BY_GEOMETRY.csv", rows, list(rows[0]))
        write_csv(out / "REFERENCE_EXPOSURE_CROSSTAB.csv", cross, list(cross[0]))
        # Check only consumed frozen metadata; do not reopen datasets, weights or native results.
        for item in inputs.values():
            checked(item)
        summary = dict(schema="eucap15_reference_exposure.v1", created_utc=utc_now(),
            status="COMPLETE_SCOPED_METADATA_DERIVATION", model_id=ref["model_id"],
            reference_checkpoint_pins={r: {c: ref["roles"][r][c]["checkpoint"] for c in ("best", "last")}
                                       for r in ("forward", "inverse")},
            n_known_core_members=len(rows), n_development_view=len(view_splits),
            reference_gradient_seen=sum(r["reference_gradient_seen_f_best_last_i_best_last"] for r in rows),
            reference_gradient_overlap_current_val_test=len(conflicts), conflict_geometry_sha256=conflicts,
            cross_tab=cross,
            proof="Pinned trainer persists sorted(set) seen indices; prior checkpoint audit verified seen is a subset of eligible train; equal cardinalities establish equality. Join audited eligibility hashes, not architecture or row counts alone.",
            evidence="DERIVED_FROM_PRIOR_CHECKPOINT_LOAD_AND_MASK_AUDITS_NOT_NEW_LOAD",
            limits=["Only this exact reference and its recorded run gradient ledger, not all historical models or inherited exposures.",
                    "Non-gradient validation membership is not proof that a sample was never used in model selection.",
                    "Exact-hash nonoverlap does not certify near-duplicate parent-family independence.",
                    "No certification of FINAL data/model or statistically independent physical testing."],
            all_historical_gradient_exposure="NOT_VERIFIED", family_independence="NOT_CERTIFIED",
            source_npz_reopened=0, checkpoint_loads=0, training_updates=0, native_calls=0,
            test_label_values_used=0, numeric_label_parses=0,
            metadata_csv_label_fields_mechanically_parsed=True,
            source_modified=False, input_pins=inputs,
            script=pin(__file__), command=sys.argv, python=sys.executable)
        _write_json(out / "SUMMARY.json", summary)
        artifacts = [pin(out / name) for name in ("REFERENCE_EXPOSURE_BY_GEOMETRY.csv", "REFERENCE_EXPOSURE_CROSSTAB.csv", "SUMMARY.json")]
        _write_json(out / "MANIFEST.json", {"schema": "eucap15_reference_exposure_manifest.v1", "artifacts": artifacts})
        artifacts.append(pin(out / "MANIFEST.json"))
        with (out / "SHA256SUMS").open("x") as handle:
            handle.writelines(p["sha256"] + "  " + Path(p["path"]).name + "\n" for p in artifacts)
        return summary
    except Exception as exc:
        _write_json(out / "FAILURE.json", {"status": "FAIL_PRESERVED", "error": repr(exc), "input_pins": inputs})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    item = pin(args.input)
    require(item["sha256"] == args.input_sha256, "input freeze SHA differs")
    result = run(item, args.out)
    print(result["status"], result["reference_gradient_seen"], result["reference_gradient_overlap_current_val_test"])


if __name__ == "__main__":
    main()
