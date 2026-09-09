"""Join physical row ordering is irrelevant; subset identity remains unique.

Only these two new synthetic cases run. The earlier fixture is imported, not
collected or executed as a test suite. No owner dataset or model is loaded.
"""
import pytest

from research.broadband56_nn import eucap15_formal_development_view as view
from tests.test_eucap15_formal_development_view import Handoff


def test_shuffled_join_preserves_every_old_identity_and_role(tmp_path):
    fixture = Handoff(tmp_path / "synthetic")
    original_join = {row["geometry_sha256"]: dict(row) for row in fixture.join}
    fixture.join = [fixture.join[i] for i in (2, 0, 3, 1)]
    pins = fixture.freeze()
    out = fixture.root / "view"
    receipt = view.prepare(*pins, out, synthetic=True)
    assert receipt["status"] == "PASS_PREPARED_BUNDLE_BB00"
    assert receipt["old_membership_retained"] == {"train":2,"validation":1,"test":1}
    result = view.document(out / "splits.json")
    sources = {row["geometry_sha256"]: row for row in view.csv_rows(out / "SOURCE_ROWS.csv")}
    for digest, before in original_join.items():
        assert result["by_geometry_sha256"][digest] == before["split"]
        assert result["geometry_id_to_sha256"][before["geometry_id"]] == digest
        assert sources[digest]["old_3018_subset_row"] == before["subset_row"]
        assert sources[digest]["old_3018_source_row"] == before["source_row"]
        assert sources[digest]["old_3018_split"] == before["split"]


def test_duplicate_subset_index_rejected_even_when_member_claim_matches(tmp_path):
    fixture = Handoff(tmp_path / "synthetic")
    fixture.join[1]["subset_row"] = fixture.join[0]["subset_row"]
    fixture.members[1]["old_3018_subset_row"] = fixture.join[0]["subset_row"]
    out = fixture.root / "failed"
    with pytest.raises(ValueError, match="old subset indices must be a complete unique permutation"):
        view.prepare(*fixture.freeze(), out, synthetic=True)
    assert view.document(out / "PREPARATION_FAILED.json")["status"] == "FAIL_PRESERVED"
    assert not (out / "dataset.npz").exists()
    assert not (out / "DATA_RECEIPT.json").exists()
