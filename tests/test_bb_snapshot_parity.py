"""Synthetic-only, production-NumPy-backed multi-shard physical parity."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from research.broadband56_nn.data import PHYSICAL_COLUMNS, S_COLUMNS
from research.broadband56_nn.io import save_json, sha256
from tests.test_bb_physics import PORTS, synthetic_s
from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as production
from rfic_transformer_inverse_design.sim.base import SParameterResult


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fixture_shards(tmp_path, monkeypatch, *, cumulative=False):
    """Build synthetic CSV labels with the real production NumPy extractor."""
    root = tmp_path / "data"
    root.mkdir()
    arrays, physical, broad, strict, all_rows = [], [], [], [], []
    for index in range(4):
        frequencies, s, channels = synthetic_s(crossing=bool(index % 2))
        monkeypatch.setattr(production, "load_touchstone", lambda _, f=frequencies, matrix=s: SParameterResult(f, matrix, 50.))
        reference = production.audit_exact56_s4p(Path("SYNTHETIC_NOT_READ.s4p"))
        rows = []
        for j, raw in enumerate(reference.rows):
            rows.append({"geometry_id": str(index), "frequency_hz": int(frequencies[j]),
                         **{name: raw[name] for name in PHYSICAL_COLUMNS},
                         "broadband_descriptor_valid": raw["broadband_descriptor_valid"],
                         "strict_lumped_valid": raw["strict_lumped_valid"],
                         **dict(zip(S_COLUMNS, channels[0, j].numpy()))})
        all_rows.append(rows)
        arrays.append(channels[0].numpy())
        physical.append([[float(row[name]) for name in PHYSICAL_COLUMNS] for row in rows])
        broad.append([row["broadband_descriptor_valid"] == "true" for row in rows])
        strict.append([row["strict_lumped_valid"] == "true" for row in rows])
    paths, sources = [], {}
    for role, subset in (([("long_features", all_rows)] if cumulative else
                           [("base/long_features", all_rows[:2]), ("increment/0/long_features", all_rows[2:])])):
        path = tmp_path / (role.replace("/", "_") + ".csv")
        _write_csv(path, [row for geometry in subset for row in geometry])
        paths.append(path)
        sources[role] = {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}
    source_manifest = tmp_path / "source_manifest.json"
    save_json(source_manifest, {"schema": "SYNTHETIC_SOURCE_ONLY"})
    selection = tmp_path / "selection.json"
    save_json(selection, {"status": "READY_FOR_10K", "evidence": {"source_files": sources}})
    np.savez(root / "dataset.npz", geometry_ids=np.arange(4).astype(str),
             frequency_hz=frequencies, s=np.asarray(arrays), physical_features=np.asarray(physical),
             broadband_descriptor_valid=np.asarray(broad), strict_lumped_valid=np.asarray(strict),
             split=np.array([0, 1, 0, 2], dtype=np.int8))
    manifest = {"schema": "bb_data_manifest.v1", "status": "PASS", "port_contract": PORTS,
                "source_manifest": {"path": str(source_manifest), "sha256": sha256(source_manifest)},
                "selection_manifest": {"path": str(selection), "sha256": sha256(selection)},
                "sources": sources, "artifacts": {"dataset.npz": {"sha256": sha256(root / "dataset.npz")}}}
    save_json(root / "data_manifest.json", manifest)
    contract = tmp_path / "contract.json"
    save_json(contract, {"port_contract": PORTS})
    return root, contract, paths, all_rows


def _refresh_sources(root, paths):
    """Explicitly refresh synthetic fixture pins after injecting source defects."""
    manifest_path = root / "data_manifest.json"
    metadata = json.loads(manifest_path.read_text())
    for pin in metadata["sources"].values():
        path = Path(pin["path"])
        pin.update(sha256=sha256(path), size_bytes=path.stat().st_size)
    selection_path = Path(metadata["selection_manifest"]["path"])
    selection = json.loads(selection_path.read_text())
    selection["evidence"]["source_files"] = metadata["sources"]
    selection_path.write_text(json.dumps(selection))
    metadata["selection_manifest"]["sha256"] = sha256(selection_path)
    manifest_path.write_text(json.dumps(metadata))


def test_each_shard_sampled_train_only_and_receipt_consumer_fields(tmp_path, monkeypatch):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, paths, _ = fixture_shards(tmp_path, monkeypatch)
    original_sources = {str(path): sha256(path) for path in paths}
    receipt = verify(data, contract, tmp_path / "parity.json", count=1)
    assert receipt["status"] == "PASS"
    assert receipt["schema"] == "bb_physical_extractor_parity.v1"
    assert receipt["geometry_ids"] == ["0", "2"]
    assert receipt["geometry_indices"] == [0, 2]
    assert receipt["frequency_rows_checked"] == 112
    assert len(receipt["production_csv_sources"]) == 2
    assert all(pin["training_geometries_checked"] == 1 for pin in receipt["production_csv_sources"])
    assert receipt["data_sha"] == sha256(data / "dataset.npz")
    assert receipt["contract_sha"] == sha256(contract)
    assert receipt["implementation_sources"]
    assert all(sha256(pin["path"]) == pin["sha256"] for pin in receipt["implementation_sources"])
    assert receipt["sealed_test_evaluated"] is False
    assert receipt["source_arrays_exact_match"] is True
    assert {str(path): sha256(path) for path in paths} == original_sources
    with pytest.raises(FileExistsError):
        verify(data, contract, tmp_path / "parity.json")


def test_cumulative_legacy_source_supported(tmp_path, monkeypatch):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, _, _ = fixture_shards(tmp_path, monkeypatch, cumulative=True)
    receipt = verify(data, contract, tmp_path / "parity.json", count=1)
    assert receipt["status"] == "PASS" and receipt["geometry_ids"] == ["0"]


def test_tampered_unselected_shard_fails_before_sampling(tmp_path, monkeypatch):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, paths, _ = fixture_shards(tmp_path, monkeypatch)
    with paths[1].open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="hash|SHA|size"):
        verify(data, contract, tmp_path / "parity.json", count=1)
    assert json.loads((tmp_path / "parity.json").read_text())["status"] == "FAIL"


@pytest.mark.parametrize("problem", ["duplicate", "missing", "s_channel", "physical", "flag", "invalid_flag"])
def test_selected_increment_rows_fail_closed(tmp_path, monkeypatch, problem):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, paths, all_rows = fixture_shards(tmp_path, monkeypatch)
    rows = [row.copy() for geometry in all_rows[2:] for row in geometry]
    if problem == "duplicate":
        rows.append(rows[0].copy())
    elif problem == "missing":
        rows.pop(0)
    elif problem == "s_channel":
        rows[0][S_COLUMNS[0]] += .01
    elif problem == "physical":
        rows[0][PHYSICAL_COLUMNS[0]] = float(rows[0][PHYSICAL_COLUMNS[0]]) + .1
    elif problem == "flag":
        rows[0]["strict_lumped_valid"] = "false"
    else:
        rows[0]["strict_lumped_valid"] = "not_a_boolean"
    _write_csv(paths[1], rows)
    _refresh_sources(data, paths)
    with pytest.raises((ValueError, AssertionError)):
        verify(data, contract, tmp_path / "parity.json", count=1)
    assert json.loads((tmp_path / "parity.json").read_text())["status"] == "FAIL"


def test_source_mutation_during_extraction_retains_fail_receipt(tmp_path, monkeypatch):
    from research.broadband56_nn import snapshot_parity
    data, contract, paths, _ = fixture_shards(tmp_path, monkeypatch)
    original = snapshot_parity.extract_physical
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        with paths[1].open("a") as stream:
            stream.write("\n")
        return value
    monkeypatch.setattr(snapshot_parity, "extract_physical", changed)
    with pytest.raises(ValueError, match="changed|hash|SHA|size"):
        snapshot_parity.verify(data, contract, tmp_path / "parity.json", count=1)
    assert json.loads((tmp_path / "parity.json").read_text())["status"] == "FAIL"


def test_shard_without_selected_training_geometries_is_hashed_not_evaluated(tmp_path, monkeypatch):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, _, _ = fixture_shards(tmp_path, monkeypatch)
    path = data / "dataset.npz"
    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    arrays["split"][2] = 1
    np.savez(path, **arrays)
    metadata_path = data / "data_manifest.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["artifacts"]["dataset.npz"]["sha256"] = sha256(path)
    metadata_path.write_text(json.dumps(metadata))
    receipt = verify(data, contract, tmp_path / "parity.json", count=1)
    assert receipt["status"] == "PASS" and receipt["geometry_ids"] == ["0"]
    assert receipt["production_csv_sources"][1]["training_geometries_checked"] == 0
    assert receipt["production_csv_sources"][1]["training_geometries_available"] == 0


def test_permutation_contract_mismatch_rejected(tmp_path, monkeypatch):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, _, _ = fixture_shards(tmp_path, monkeypatch)
    configuration = json.loads(contract.read_text())
    configuration["port_contract"]["internal_permutation"] = [0, 1, 2, 3]
    contract.write_text(json.dumps(configuration))
    with pytest.raises(ValueError, match="port contracts differ"):
        verify(data, contract, tmp_path / "parity.json")


def test_source_binding_change_without_refreeze_is_rejected(tmp_path, monkeypatch):
    from research.broadband56_nn.snapshot_parity import verify
    data, contract, paths, _ = fixture_shards(tmp_path, monkeypatch)
    metadata_path = data / "data_manifest.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["sources"]["increment/0/long_features"]["path"] = str(paths[0])
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="frozen selection"):
        verify(data, contract, tmp_path / "parity.json")
