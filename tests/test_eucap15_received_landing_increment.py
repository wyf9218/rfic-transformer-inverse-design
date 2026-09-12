"""New synthetic increments only; neither the old24 nor the actual96 is executed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_received_landing_increment as subject


BASE = Path(__file__).resolve().parents[1] / "docs/research/eucap15_abc_increment_20260910/COVERAGE_BEFORE.csv"


def pin(name):
    return dict(path="/synthetic/" + name, sha256=hashlib.sha256(name.encode()).hexdigest(),
                bytes=len(name))


def row(name, split, formal=True):
    return dict(request_id=name, geometry_sha256=hashlib.sha256(name.encode()).hexdigest(),
                source="SYNTHETIC", status="FRESH_EMX_EXTRACTED", strict=True,
                core15_eligible=True, actual_response=[1.1, 1.2, 13.0, .4],
                assigned_development_split=split, original_result_accepted_flag=False,
                result_pin=pin(name + "/RESULT.json"),
                formal_records=[dict(request_id=name, pin=pin(name + "/formal.json"))] if formal else [])


@pytest.fixture
def increment(tmp_path):
    rows = [row("train", "train"), row("validation", "validation"), row("test", "test"),
            row("pending-train", "train", False), row("pending-test", "test", False)]
    failure = row("failure", "train", False)
    failure.update(status="CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION", strict=None,
                   core15_eligible=None, actual_response=None)
    rows.append(failure)
    doc = dict(feature_order=["Lp_nH", "Ls_nH", "Qmin", "K_abs"], rows=rows,
               observed_utc="2026-09-12T00:00:00Z", baseline_utc="2026-09-11T00:00:00Z",
               new_terminal=6, new_emx_completed=5, new_strict_range_unique=5,
               fresh_formal_this_increment=3, new_core_without_formal=2)
    expected = dict(received_terminal=6, emx_completed=5, strict_in_range=5,
                    formal_admitted=3, pending_formal=2, formal_train=1)

    def execute(document=doc, counts=expected, verified=True):
        path = tmp_path / "received.json"
        raw = json.dumps(document, allow_nan=False).encode()
        path.write_bytes(raw)
        return subject.run(BASE, path, received_sha256=hashlib.sha256(raw).hexdigest(),
                           expected_counts=counts, caller_verified_sources=verified)

    return doc, expected, execute


def test_pending_formal_and_original_holdouts_never_enter_train_coverage(increment):
    doc, expected, execute = increment
    original = deepcopy(doc)
    result = execute()
    assert result["eligible_new_train_rows"] == 1
    assert result["source_counts"]["pending_formal_train"] == 1
    assert result["source_counts"]["pending_formal_test"] == 1
    assert result["source_counts"]["formal_validation"] == 1
    assert result["source_counts"]["formal_test"] == 1
    assert [r["request_id"] for r in result["rows"] if r["train_reference_comparison_eligible"]] == ["train"]
    for r in result["rows"][1:]:
        assert r["frozen3801_train_cell_count"] is None
        assert r["train_cell_was_empty"] is None
    assert result["rows"][-1]["core15_eligible"] is None
    assert result["rows"][3]["formal_records"] == []
    assert result["rows"][3]["qualified_pending_formal"] is True
    assert result["cumulative_coverage_gain"] is None
    assert result["cumulative_union_geometry_count"] is None
    assert "CALLER_VERIFIED" in result["source_reliance"]
    assert doc == original
    with pytest.raises(ValueError, match="Caller must have verified"):
        execute(verified=False)
    with pytest.raises(ValueError, match="Caller envelope: formal_train"):
        execute(counts=dict(expected, formal_train=2))


def test_mismatched_formal_join_and_reused_record_identity_are_rejected(increment):
    doc, _, execute = increment
    mismatch = deepcopy(doc)
    mismatch["rows"][0]["formal_records"][0]["request_id"] = "other-request"
    with pytest.raises(ValueError, match="Formal request_id"):
        execute(mismatch)
    for field in ("path", "sha256"):
        duplicate = deepcopy(doc)
        duplicate["rows"][1]["formal_records"][0]["pin"][field] = doc["rows"][0]["formal_records"][0]["pin"][field]
        with pytest.raises(ValueError, match="Formal: duplicate record pin " + field):
            execute(duplicate)
    source_mismatch = deepcopy(doc)
    source_mismatch["rows"][0]["formal_records"][0]["assigned_development_split"] = "test"
    with pytest.raises(ValueError, match="Formal source row mismatch"):
        execute(source_mismatch)


def test_supplied_target_and_prediction_cells_are_preserved_without_inferred_errors(increment):
    doc, _, execute = increment
    doc["rows"][0].update(target_cell=[1, 2, 3], predicted_cell=[4, 5, 6])
    doc["rows"][3].update(target_cell=[7, 6, 5], predicted_cell=None)
    result = execute()
    for old, new in zip(doc["rows"], result["rows"]):
        assert new["target_cell"] == old.get("target_cell")
        assert new["predicted_cell"] == old.get("predicted_cell")
        assert new["requested_target_error"] is None
        assert new["assigned_development_split"] == old["assigned_development_split"]
        assert new["original_result_accepted_flag"] is False
    assert result["rows"][0]["target_prediction_availability"] == "SOURCE_FIELDS_PRESERVED_NOT_RECONSTRUCTED"
    assert result["rows"][3]["target_cell"] == [7, 6, 5]
    assert result["rows"][3]["train_reference_comparison_eligible"] is False
