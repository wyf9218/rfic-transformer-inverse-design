"""Synthetic-only exact-10K intake tests; no producer or real CSV is accessed."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from research.broadband56_nn import snapshot_10k as target
from research.broadband56_nn.data import PHYSICAL_COLUMNS, S_COLUMNS, _split_for_hash, prepare_data, sha256
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import canonical_geometry_sha256
from tests.test_bb_data import CONTRACT, fixture_source, pin, refresh_source, write_csv, write_json


def published_source(root, n=30):
    source, geometry, long = fixture_source(root, n)
    remap = {}
    for row in geometry:
        digest = canonical_geometry_sha256({key.removeprefix("geom__"): value for key, value in row.items() if key.startswith("geom__")})
        remap[row["geometry_id"]] = digest
        row["geometry_id"] = row["geometry_sha256"] = digest
    for row in long:
        row["geometry_id"] = row["geometry_sha256"] = remap[row["geometry_id"]]
    write_csv(root / "geometry.csv", geometry)
    write_csv(root / "long.csv", long)
    publish(root, n)
    return source, geometry, long


def publish(root, n):
    """Synthetic producer-shaped publication, conspicuously not real evidence."""
    refresh_source(root, n)
    manifest = json.loads((root / "source.json").read_text())
    cp = json.loads((root / "checkpoint.json").read_text())
    raw = json.loads((root / "products.json").read_text())
    with (root / "long.csv").open(newline="") as handle:
        long = list(csv.DictReader(handle))
    by_id = {r["geometry_id"]: r for r in long}
    with (root / "geometry.csv").open(newline="") as handle:
        geometry = list(csv.DictReader(handle))
    index = [{"geometry_id": r["geometry_id"], "geometry_sha256": r["geometry_sha256"],
              "campaign_contract_fingerprint": CONTRACT,
              "s4p_sha256": by_id[r["geometry_id"]]["s4p_sha256"],
              "port_count": 4, "frequency_points": 56, "first_frequency_hz": 5_000_000_000,
              "last_frequency_hz": 60_000_000_000, "frequency_step_hz": 1_000_000_000,
              "emx_status": "PASS", "calibre_status": "PASS", "calibre_blocking_violations": 0}
             for r in geometry if r["geometry_id"] in by_id]
    write_csv(root / "index.csv", index)
    status = {"checkpoint_status": "CHECKPOINT_COMPLETE", "audit_mode": "checkpoint",
              "contract_fingerprint_sha256": CONTRACT, "accepted_geometries": n,
              "s4p_artifacts": n, "geometry_frequency_rows": n * 56}
    write_json(root / "status.json", status)
    cp.update(audit_mode="checkpoint", checks=[{"name": "SYNTHETIC_ONLY", "pass": True}],
              outputs={"checkpoint_status": pin(root / "status.json")})
    cp["inputs"]["artifact_index"] = pin(root / "index.csv")
    raw["checks"]["output_is_no_clobber"] = True
    raw["outputs"]["artifact_index"] = pin(root / "index.csv")
    write_json(root / "checkpoint.json", cp)
    write_json(root / "products.json", raw)
    manifest["files"].update(checkpoint_receipt=pin(root / "checkpoint.json"),
                             raw_products_receipt=pin(root / "products.json"),
                             checkpoint_status=pin(root / "status.json"), sparameter_index=pin(root / "index.csv"))
    write_json(root / "source.json", manifest)


def repin(root, key, filename):
    source = json.loads((root / "source.json").read_text())
    source["files"][key] = pin(root / filename)
    write_json(root / "source.json", source)


@pytest.fixture
def small_count(monkeypatch):
    # Same complete-56-row path, reduced private test fixture only. Public CLI
    # exposes no count override. The unpatched threshold is separately tested.
    monkeypatch.setattr(target, "EXACT_COUNT", 20)


def test_below_real_10000_threshold_waits_without_long_access(tmp_path, monkeypatch):
    source, _, _ = published_source(tmp_path / "source")
    assert target.EXACT_COUNT == 10000
    original = target._check_pin

    def receipt_only(record, base):
        assert not record["path"].endswith(("long.csv", "geometry.csv"))
        return original(record, base)

    monkeypatch.setattr(target, "_check_pin", receipt_only)
    result = target.freeze_selection(source, tmp_path / "wait")
    assert result["status"] == "WAITING_FOR_10K"
    assert result["selected_geometries"] == 0
    assert result["evidence"]["formally_accepted_geometries"] == 30
    assert not (tmp_path / "wait/SELECTION_MANIFEST.json").exists()
    assert not (tmp_path / "wait/dataset.npz").exists()


@pytest.mark.parametrize("count,ready", [(9999, False), (10000, True), (10065, True)])
def test_literal_10000_metadata_threshold_never_authorizes_training(tmp_path, count, ready):
    source, _, _ = published_source(tmp_path / "source")
    cp_path = source.parent / "checkpoint.json"
    cp = json.loads(cp_path.read_text())
    cp["expected_accepted"] = count  # Metadata-only synthetic threshold unit.
    write_json(cp_path, cp)
    repin(source.parent, "checkpoint_receipt", cp_path.name)
    result = target.inspect_source_manifest(source)
    assert (result["status"] == "COMMITTED_COUNT_READY_FULL_FREEZE_REQUIRED") is ready
    assert result["training_allowed"] is False
    assert result["data_materialized"] is False


def test_first_accepted_order_and_deterministic_manifest(tmp_path, small_count):
    source, geometry, _ = published_source(tmp_path / "source")
    a = target.freeze_selection(source, tmp_path / "a")
    b = target.freeze_selection(source, tmp_path / "b")
    assert a == b
    assert [r["geometry_id"] for r in a["rows"]] == [r["geometry_id"] for r in geometry[:20]]
    assert [r["source_accepted_sequence"] for r in a["rows"]] == list(range(1, 21))
    assert a["selected_frequency_rows"] == 20 * 56
    assert all(len(r["frequency_row_sha256"]) == 56 for r in a["rows"])
    assert (tmp_path / "a/SELECTION_MANIFEST.json").read_bytes() == (tmp_path / "b/SELECTION_MANIFEST.json").read_bytes()


def test_preparation_matches_existing_data_contract_and_preserves_masks(tmp_path, small_count):
    source, _, _ = published_source(tmp_path / "source")
    target.freeze_selection(source, tmp_path / "selection")
    selected = tmp_path / "selection/SELECTION_MANIFEST.json"
    manifest = target.prepare_selection(selected, tmp_path / "data", sha256(selected))
    # The existing data.py prepares the same first 20 synthetic geometries.
    prefix, _, _ = published_source(tmp_path / "prefix", 20)
    prepare_data(prefix, tmp_path / "existing")
    with np.load(tmp_path / "data/dataset.npz") as actual, np.load(tmp_path / "existing/dataset.npz") as expected:
        assert set(actual.files) == set(expected.files)
        for name in actual.files:
            np.testing.assert_equal(actual[name], expected[name])
        assert actual["s"].shape == (20, 56, 32)
        assert np.isnan(actual["physical_features"][:, -1, 2]).all()
        assert not actual["strict_lumped_valid"][:, 26:].any()
        assert actual["broadband_descriptor_valid"][:, 26:55].all()
    assert (tmp_path / "data/normalizer.json").read_bytes() == (tmp_path / "existing/normalizer.json").read_bytes()
    assert manifest["source_checkpoint_geometries"] == 30
    assert manifest["unique_geometries"] == 20
    for artifact in manifest["artifacts"].values():
        assert sha256(tmp_path / "data" / artifact["path"]) == artifact["sha256"]


def test_source_growth_keeps_first_selection_and_old_split(tmp_path, small_count):
    first, _, _ = published_source(tmp_path / "old_source", 25)
    target.freeze_selection(first, tmp_path / "old_selection")
    path = tmp_path / "old_selection/SELECTION_MANIFEST.json"
    target.prepare_selection(path, tmp_path / "old_data", sha256(path))
    larger, _, _ = published_source(tmp_path / "larger", 35)
    result = target.freeze_selection(larger, tmp_path / "new_selection", tmp_path / "old_data/splits.json")
    old = json.loads(path.read_text())
    assert result["selected_geometry_set_sha256"] == old["selected_geometry_set_sha256"]
    assert [(r["geometry_sha256"], r["split"]) for r in result["rows"]] == [(r["geometry_sha256"], r["split"]) for r in old["rows"]]


def test_train_only_scaler_ignores_holdout_values(tmp_path, small_count):
    source, _, rows = published_source(tmp_path / "source")
    target.freeze_selection(source, tmp_path / "selection_a")
    path = tmp_path / "selection_a/SELECTION_MANIFEST.json"
    target.prepare_selection(path, tmp_path / "a", sha256(path))
    for row in rows:
        if _split_for_hash(row["geometry_sha256"], 17) != 0:
            for key in (*S_COLUMNS, *PHYSICAL_COLUMNS):
                row[key] = 999.0
    write_csv(source.parent / "long.csv", rows)
    publish(source.parent, 30)
    target.freeze_selection(source, tmp_path / "selection_b")
    path = tmp_path / "selection_b/SELECTION_MANIFEST.json"
    target.prepare_selection(path, tmp_path / "b", sha256(path))
    assert (tmp_path / "a/normalizer.json").read_bytes() == (tmp_path / "b/normalizer.json").read_bytes()


@pytest.mark.parametrize("problem", ["duplicate_changed_row", "missing_frequency", "equivalent_geometry", "bad_mask", "wrong_s4p", "out_of_order"])
def test_row_and_equivalence_failures_preserved(tmp_path, small_count, problem):
    source, geometry, rows = published_source(tmp_path / "source")
    if problem == "duplicate_changed_row":
        rows.append({**rows[0], S_COLUMNS[0]: 123.0})
    elif problem == "missing_frequency":
        rows.pop()
    elif problem == "equivalent_geometry":
        for key in geometry[0]:
            if key.startswith("geom__"):
                geometry[1][key] = geometry[0][key]
    elif problem == "bad_mask":
        rows[0]["broadband_descriptor_valid"] = "false"
    elif problem == "wrong_s4p":
        rows[0]["s4p_sha256"] = "e" * 64
    else:
        geometry.reverse()
    write_csv(source.parent / "geometry.csv", geometry)
    write_csv(source.parent / "long.csv", rows)
    publish(source.parent, 30)
    with pytest.raises(ValueError):
        target.freeze_selection(source, tmp_path / "failed")
    assert (tmp_path / "failed/SELECTION_FAILED.json").exists()
    assert not (tmp_path / "failed/SELECTION_MANIFEST.json").exists()


@pytest.mark.parametrize("problem", ["no_commit", "status_mismatch", "checkpoint_failure", "stale_status_pin", "source_tamper"])
def test_published_identity_gate(tmp_path, small_count, problem):
    source, _, _ = published_source(tmp_path / "source")
    if problem == "no_commit":
        path = source.parent / "products.json"
        raw = json.loads(path.read_text()); raw["checks"]["output_is_no_clobber"] = False
        write_json(path, raw); repin(source.parent, "raw_products_receipt", path.name)
    elif problem in ("status_mismatch", "stale_status_pin"):
        path = source.parent / "status.json"
        status = json.loads(path.read_text()); status["accepted_geometries"] = 31
        write_json(path, status)
        if problem == "status_mismatch":
            repin(source.parent, "checkpoint_status", path.name)
    elif problem == "checkpoint_failure":
        path = source.parent / "checkpoint.json"
        cp = json.loads(path.read_text()); cp["checks"][0]["pass"] = False
        write_json(path, cp); repin(source.parent, "checkpoint_receipt", path.name)
    else:
        with (source.parent / "long.csv").open("a") as handle:
            handle.write("tamper\n")
    with pytest.raises(ValueError):
        target.freeze_selection(source, tmp_path / "failed")


def test_frozen_source_change_and_wrong_selection_sha_fail(tmp_path, small_count):
    source, _, _ = published_source(tmp_path / "source")
    target.freeze_selection(source, tmp_path / "selection")
    path = tmp_path / "selection/SELECTION_MANIFEST.json"
    with pytest.raises(ValueError, match="SHA"):
        target.prepare_selection(path, tmp_path / "wrong_sha", "0" * 64)
    with (source.parent / "long.csv").open("a") as handle:
        handle.write("changed\n")
    with pytest.raises(ValueError, match="SHA"):
        target.prepare_selection(path, tmp_path / "changed", sha256(path))
    assert (tmp_path / "changed/PREPARATION_FAILED.json").exists()


def test_no_clobber_and_no_previous_split_remap(tmp_path, small_count):
    source, _, _ = published_source(tmp_path / "source")
    target.freeze_selection(source, tmp_path / "selection")
    with pytest.raises(FileExistsError):
        target.freeze_selection(source, tmp_path / "selection")
    path = tmp_path / "selection/SELECTION_MANIFEST.json"
    target.prepare_selection(path, tmp_path / "data", sha256(path))
    with pytest.raises(FileExistsError):
        target.prepare_selection(path, tmp_path / "data", sha256(path))
    old = json.loads((tmp_path / "data/splits.json").read_text())
    digest = next(iter(old["by_geometry_sha256"]))
    old["by_geometry_sha256"][digest] = "test" if old["by_geometry_sha256"][digest] != "test" else "train"
    write_json(tmp_path / "remapped.json", old)
    with pytest.raises(ValueError, match="remaps"):
        target.freeze_selection(source, tmp_path / "remapped", tmp_path / "remapped.json")


def chain_source(root):
    """Small synthetic base12 + increments6/12: 30 committed, prefix20 selected."""
    from tests.test_broadband56_stage_progress import _receipt
    from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import CAMPAIGN_ID
    from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT as science
    root.mkdir()
    base, _, _ = published_source(root / "base", 12)
    all_source, geometry, long = published_source(root / "all", 30)
    for source in (base, all_source):
        folder = source.parent
        for filename in ("geometry.csv", "long.csv", "index.csv"):
            with (folder / filename).open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                row["campaign_contract_fingerprint"] = science
            write_csv(folder / filename, rows)
        for filename in ("bounds.json", "status.json"):
            document = json.loads((folder / filename).read_text())
            document.update(contract_fingerprint_sha256=science, campaign_id=CAMPAIGN_ID)
            write_json(folder / filename, document)
        cp, raw, descriptor = [json.loads((folder / name).read_text()) for name in ("checkpoint.json", "products.json", "source.json")]
        for document in (cp, raw, descriptor):
            document.update(contract_fingerprint_sha256=science, campaign_id=CAMPAIGN_ID)
        for role, filename in (("accepted_geometries", "geometry.csv"), ("long_features", "long.csv"), ("artifact_index", "index.csv")):
            cp["inputs"][role] = raw["outputs"][role] = pin(folder / filename)
        cp["inputs"]["geometry_bounds"] = pin(folder / "bounds.json")
        cp["outputs"]["checkpoint_status"] = pin(folder / "status.json")
        write_json(folder / "checkpoint.json", cp); write_json(folder / "products.json", raw)
        for role, old in descriptor["files"].items():
            descriptor["files"][role] = pin(folder / old["path"].rsplit("/", 1)[-1])
        write_json(source, descriptor)
    boundary, _ = _receipt(root / "boundary", attempt_index=7, before=6, accepted=6, raw=8,
                           prior_sha="d" * 64, stage="PHASE_A", cumulative_target=50000)
    items, previous = [], sha256(boundary)
    for i, (before, after) in enumerate(((12, 18), (18, 30)), start=8):
        path, receipt = _receipt(root / f"increment_{i}", attempt_index=i, before=before,
                                 accepted=after - before, raw=after - before, prior_sha=previous,
                                 stage="PHASE_A", cumulative_target=50000)
        chosen = set(r["geometry_id"] for r in geometry[before:after])
        mapping = (("geometry.csv", "accepted_geometry_increment"), ("long.csv", "long_features"), ("index.csv", "s4p_artifact_index"))
        for filename, role in mapping:
            with (all_source.parent / filename).open(newline="") as handle:
                rows = [r for r in csv.DictReader(handle) if r["geometry_id"] in chosen]
            if role == "s4p_artifact_index":
                for row in rows:
                    row["accepted_sequence"] = next(r["accepted_sequence"] for r in geometry if r["geometry_id"] == row["geometry_id"])
                    row["frequency_start_hz"] = row.pop("first_frequency_hz")
                    row["frequency_stop_hz"] = row.pop("last_frequency_hz")
                    row["qa_status"] = "PASS"
                    for key in ("emx_status", "calibre_status", "calibre_blocking_violations"):
                        row.pop(key)
            artifact = path.parent / f"{role}.csv"
            write_csv(artifact, rows)
            receipt["artifacts"][role] = pin(artifact)
        write_json(path, receipt)
        items.append({"receipt": pin(path), "files": receipt["artifacts"]})
        previous = sha256(path)
    base_descriptor = json.loads(base.read_text())
    handoff = {"schema": "b56_private_read_only_data_handoff.v1", "campaign_id": CAMPAIGN_ID,
               "contract_fingerprint_sha256": science,
               "checkpoint": {"count": 12, "receipt": base_descriptor["files"]["checkpoint_receipt"]},
               "increments": [{"pin": items[0]["receipt"], "receipt": json.loads(Path(items[0]["receipt"]["path"]).read_text())}]}
    write_json(root / "handoff.json", handoff)
    descriptor = {"schema": "bb_committed_increment_source.v1", "campaign_id": CAMPAIGN_ID,
                  "contract_fingerprint_sha256": science, "base_source_manifest": pin(base),
                  "checkpoint_status": base_descriptor["files"]["checkpoint_status"],
                  "boundary_progress_receipt": pin(boundary), "increments": items,
                  "committed_accepted": 30, "source_handoff": pin(root / "handoff.json"),
                  "port_contract": base_descriptor["port_contract"]}
    write_json(root / "chain.json", descriptor)
    return root / "chain.json"


def test_committed_increment_chain_prefix_and_localized_schema(tmp_path, small_count):
    source = chain_source(tmp_path / "chain")
    observation = target.inspect_source_manifest(source)
    assert observation["evidence"]["formally_accepted_geometries"] == 30
    assert observation["training_allowed"] is False
    manifest = target.freeze_selection(source, tmp_path / "selection")
    assert manifest["status"] == "READY_FOR_10K"
    assert [r["source_accepted_sequence"] for r in manifest["rows"]] == list(range(1, 21))
    assert manifest["evidence"]["base_geometries"] == 12
    selected = tmp_path / "selection/SELECTION_MANIFEST.json"
    result = target.prepare_selection(selected, tmp_path / "prepared", sha256(selected))
    assert result["source_checkpoint_geometries"] == 30
    assert result["unique_geometries"] == 20
    with np.load(tmp_path / "prepared/dataset.npz") as arrays:
        assert arrays["s"].shape == (20, 56, 32)
        assert arrays["geometry"].shape == (20, 10)


def test_chain_wait_does_not_require_copied_increment_artifacts(tmp_path):
    source = chain_source(tmp_path / "chain")
    descriptor = json.loads(source.read_text())
    for item in descriptor["increments"]:
        item["files"] = {}
    write_json(source, descriptor)
    result = target.freeze_selection(source, tmp_path / "wait")
    assert result["status"] == "WAITING_FOR_10K"
    assert result["evidence"]["formally_accepted_geometries"] == 30


@pytest.mark.parametrize("problem", ["prior_sha", "attempt_index", "accepted_before", "authorization", "wrong_file", "watermark", "wrong_boundary"])
def test_chain_rejects_broken_progress_or_localized_pins(tmp_path, small_count, problem):
    source = chain_source(tmp_path / "chain")
    descriptor = json.loads(source.read_text())
    second = descriptor["increments"][1]
    path = Path(second["receipt"]["path"])
    receipt = json.loads(path.read_text())
    if problem in ("prior_sha", "attempt_index", "accepted_before", "authorization"):
        key, value = {"prior_sha": ("prior_progress_receipt_sha256", "0" * 64), "attempt_index": ("attempt_index", 100),
                      "accepted_before": ("accepted_before", 17), "authorization": ("full_campaign_authorization_receipt_sha256", "0" * 64)}[problem]
        receipt[key] = value
        write_json(path, receipt)
        second["receipt"] = pin(path)
    elif problem == "wrong_file":
        second["files"]["long_features"]["sha256"] = "0" * 64
    elif problem == "watermark":
        descriptor["committed_accepted"] = 10000
    else:
        descriptor["boundary_progress_receipt"] = second["receipt"]
    write_json(source, descriptor)
    with pytest.raises(ValueError):
        target.freeze_selection(source, tmp_path / "failed")
    assert (tmp_path / "failed/SELECTION_FAILED.json").exists()


def test_complete_z_and_additional_source_flags_preserved(tmp_path, small_count):
    source, _, rows = published_source(tmp_path / "source")
    for row in rows:
        row.update({key: i * 0.25 for i, key in enumerate(target.Z_COLUMNS)})
        row["mutual_inductance_h"] = -1.25e-9
        row["passivity_status"] = "FAIL" if row["frequency_hz"] == 60_000_000_000 else "PASS"
        row["below_half_srf"] = "true" if row["strict_lumped_valid"] == "true" else "false"
    write_csv(source.parent / "long.csv", rows)
    publish(source.parent, 30)
    target.freeze_selection(source, tmp_path / "selection")
    selected = tmp_path / "selection/SELECTION_MANIFEST.json"
    target.prepare_selection(selected, tmp_path / "data", sha256(selected))
    with np.load(tmp_path / "data/dataset.npz") as data:
        assert data["z"].shape == (20, 56, 32)
        np.testing.assert_array_equal(data["source_extra_physical_columns"], ["mutual_inductance_h"])
        assert (data["source_extra_physical"] == -1.25e-9).all()
        metadata = json.loads(str(data["source_validity_schema_json"]))
        column = metadata["fields"].index("passivity_status")
        failed = metadata["categories"]["passivity_status"].index("FAIL")
        assert (data["source_validity_codes"][:, -1, column] == failed).all()
