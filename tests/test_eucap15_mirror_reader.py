"""Exact-path mirror tests. All native-shaped bytes are synthetic, never EMX.

Reuse the frozen old fixture, not its test suite, and compare the unchanged
no-map result with the original validator. No model, dataset or native call.
"""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_selected_evidence as evidence


ORIGINAL_ROOT = Path(__file__).resolve().parents[2] / "frequency-indexed-mlp-20260908"
ORIGINAL_SOURCE = ORIGINAL_ROOT / "research/broadband56_nn/eucap15_selected_evidence.py"
FIXTURE_SOURCE = ORIGINAL_ROOT / "tests/test_eucap15_selected_evidence.py"


def _load(name, path, expected_sha):
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture_module = _load("_frozen_selected_synthetic_fixture", FIXTURE_SOURCE,
    "9d884386c1dd42edf091e7e1642c61f34d9e02de4e61620520f5565e3a8bad5b")
original = _load("research.broadband56_nn._original_selected_mirror_test", ORIGINAL_SOURCE,
    "80e9fd7578fe141615d99e6fc33e3a38f6e8bf801add5f5eef6d14121189e8dc")
SyntheticEvidence = fixture_module.SyntheticEvidence


def mirror(chain, destination):
    """Flat names deliberately differ from the native directory structure."""
    destination.mkdir()
    mapping = {}
    for index, path in enumerate(chain.paths.values()):
        target = destination / f"artifact_{index:03d}.opaque"
        target.write_bytes(path.read_bytes())
        mapping[str(path)] = str(target)
    return mapping


def test_no_map_preserves_original_result_and_bytes(tmp_path):
    chain = SyntheticEvidence(tmp_path / "original")
    before = {str(p): p.read_bytes() for p in chain.paths.values()}
    entry = chain.entry()
    expected = original.inspect_features(entry, chain.item, chain.ctx)
    result = evidence.inspect_features(entry, chain.item, chain.ctx)
    assert {k: v for k, v in result.items() if k != "resolution_evidence"} == expected
    assert {r["original"]["path"] for r in result["resolution_evidence"]} == set(before)
    assert all(r["original"] == r["resolved"] for r in result["resolution_evidence"])
    assert before == {str(p): p.read_bytes() for p in chain.paths.values()}


@pytest.mark.parametrize("below_half_srf", [True, False])
def test_full_mirror_without_original_files_keeps_physics_and_identity(tmp_path, below_half_srf):
    chain = SyntheticEvidence(tmp_path / "original", below_half_srf=below_half_srf)
    entry = chain.entry()
    expected = original.inspect_features(entry, chain.item, chain.ctx)
    mapping = mirror(chain, tmp_path / "mirror")
    frozen_ctx = deepcopy(chain.ctx)
    chain.root.rename(tmp_path / "synthetic_unavailable")
    assert not chain.root.exists()
    result = evidence.inspect_features(entry, chain.item, chain.ctx, physical_path_map=mapping)
    assert {k: v for k, v in result.items() if k != "resolution_evidence"} == expected
    assert chain.ctx.__dict__ == frozen_ctx.__dict__
    assert {p["path"] for p in result["evidence_pins"]} == set(mapping)
    for resolution in result["resolution_evidence"]:
        source, resolved = resolution["original"], resolution["resolved"]
        assert resolved["path"] == mapping[source["path"]]
        assert (resolved["sha256"], resolved["bytes"]) == (source["sha256"], source["bytes"])
    assert result["status"] == ("STRICT_VALID" if below_half_srf else "EMX_INVALID")
    assert result["strict_joint_hit"] is below_half_srf


@pytest.mark.parametrize("change", ["same_size_different_sha", "different_size"])
def test_mirror_sha_and_size_mismatch_fail_closed(tmp_path, change):
    chain = SyntheticEvidence(tmp_path / "original")
    mapping = mirror(chain, tmp_path / "mirror")
    target = Path(mapping[str(chain.paths["gds"])])
    raw = target.read_bytes()
    target.write_bytes(bytes([raw[0] ^ 1]) + raw[1:] if change.startswith("same_size") else raw[:-1])
    with pytest.raises(evidence.SelectedEvidenceError, match="artifact SHA/size mismatch"):
        evidence.inspect_features(chain.entry(), chain.item, chain.ctx, physical_path_map=mapping)


@pytest.mark.parametrize("missing", ["manifest", "records", "csv", "gds"])
def test_missing_exact_mapping_never_falls_back_to_readable_original(tmp_path, missing):
    chain = SyntheticEvidence(tmp_path / "original")
    mapping = mirror(chain, tmp_path / "mirror")
    del mapping[str(chain.paths[missing])]
    assert chain.paths[missing].is_file()
    with pytest.raises(evidence.SelectedEvidenceError, match="missing exact original path"):
        evidence.inspect_features(chain.entry(), chain.item, chain.ctx, physical_path_map=mapping)


@pytest.mark.parametrize("symlink_kind", ["file", "parent"])
def test_mirror_symlink_file_or_parent_rejected(tmp_path, symlink_kind):
    chain = SyntheticEvidence(tmp_path / "original")
    mapping = mirror(chain, tmp_path / "mirror")
    actual = Path(mapping[str(chain.paths["gds"])])
    link = tmp_path / "synthetic_link"
    if symlink_kind == "file":
        link.symlink_to(actual)
        mapping[str(chain.paths["gds"])] = str(link)
    else:
        link.symlink_to(actual.parent, target_is_directory=True)
        mapping[str(chain.paths["gds"])] = str(link / actual.name)
    with pytest.raises(evidence.SelectedEvidenceError, match="symlink artifact forbidden"):
        evidence.inspect_features(chain.entry(), chain.item, chain.ctx, physical_path_map=mapping)


def test_mirror_does_not_repair_invalid_original_native_structure(tmp_path):
    chain = SyntheticEvidence(tmp_path / "original")
    mapping = mirror(chain, tmp_path / "mirror")
    entry = chain.entry()
    old = entry["feature_manifest"]["path"]
    wrong = str(tmp_path / "wrong_original_tree" / "not_features" / "MANIFEST.json")
    entry["feature_manifest"]["path"] = wrong
    mapping[wrong] = mapping[old]
    with pytest.raises(evidence.SelectedEvidenceError, match="native closed features/MANIFEST"):
        evidence.inspect_features(entry, chain.item, chain.ctx, physical_path_map=mapping)


def test_frozen_source_relocation_precedes_physical_resolution(tmp_path):
    chain = SyntheticEvidence(tmp_path / "original")
    mac_source = str(tmp_path / "synthetic_mac_source" / "eleven_records.jsonl")
    chain.edit("pilot", lambda doc: doc["requests"][0]["source_records"].update(path=mac_source))
    chain.ctx.manifest_pin = chain.p("pilot")
    chain.ctx.manifest = chain.read("pilot")
    chain.item = deepcopy(chain.ctx.manifest["requests"][0])
    chain.ctx.path_map = {mac_source: str(chain.paths["records"])}
    entry = chain.entry()
    mapping = mirror(chain, tmp_path / "mirror")
    chain.root.rename(tmp_path / "synthetic_unavailable")
    result = evidence.inspect_features(entry, chain.item, chain.ctx, physical_path_map=mapping)
    assert result["status"] == "STRICT_VALID"
    assert mac_source not in {p["path"] for p in result["evidence_pins"]}
    assert str(chain.paths["records"]) in {p["path"] for p in result["evidence_pins"]}
    assert chain.item["source_records"]["path"] == mac_source


def test_unused_mapping_entries_are_not_resolved_or_validated(tmp_path):
    chain = SyntheticEvidence(tmp_path / "original")
    mapping = mirror(chain, tmp_path / "mirror")
    # Unconsumed entries do not belong to this candidate's evidence closure.
    # Actual consumed paths still pass every canonical/symlink/SHA/size check.
    mapping["unused noncanonical key"] = "unused noncanonical value"
    result = evidence.inspect_features(chain.entry(), chain.item, chain.ctx, physical_path_map=mapping)
    assert result["status"] == "STRICT_VALID"
    assert "unused noncanonical key" not in {r["original"]["path"] for r in result["resolution_evidence"]}
