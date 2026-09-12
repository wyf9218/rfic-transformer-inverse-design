"""Only synthetic formal-backfill deltas; no historical or actual cohort runs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_received_landing_increment as subject


BASE = Path(__file__).resolve().parents[1] / "docs/research/eucap15_abc_increment_20260910/COVERAGE_BEFORE.csv"


def pin(name):
    return dict(path="/synthetic-backfill/" + name,
                sha256=hashlib.sha256(name.encode()).hexdigest(), bytes=len(name))


def row(name, split, formal=True):
    return dict(request_id=name, geometry_sha256=hashlib.sha256(name.encode()).hexdigest(),
                source="SYNTHETIC", status="FRESH_EMX_EXTRACTED", strict=True,
                core15_eligible=True, actual_response=[1.1, 1.2, 13.0, .4],
                assigned_development_split=split, original_result_accepted_flag=False,
                target_cell=[1, 2, 3], predicted_cell=None,
                result_pin=pin(name + "/RESULT.json"),
                formal_records=[dict(request_id=name, pin=pin(name + "/formal.json"))] if formal else [])


@pytest.fixture
def increment(tmp_path):
    doc = dict(feature_order=["Lp_nH", "Ls_nH", "Qmin", "K_abs"],
               rows=[row("new-train", "train"), row("new-pending", "train", False)],
               backfilled_prior_rows=[row("prior-train-1", "train"), row("prior-train-2", "train"),
                                      row("prior-test", "test")],
               observed_utc="2026-09-12T09:00:00Z", baseline_utc="2026-09-12T08:00:00Z",
               new_terminal=2, new_emx_completed=2, new_strict_range_unique=2,
               fresh_formal_this_increment=1, new_core_without_formal=1,
               prior_pending_formal_backfill=3, ledger_window_fresh_formal=4)
    counts = dict(received_terminal=2, emx_completed=2, strict_in_range=2,
                  formal_admitted=1, pending_formal=1, formal_train=1)
    backfills = dict(formal_admitted=3, formal_train=2, formal_validation=0, formal_test=1)
    sequence = 0

    def execute(document=doc, expected_backfills=backfills):
        nonlocal sequence
        sequence += 1
        path = tmp_path / f"received_{sequence:03d}.json"
        raw = json.dumps(document, allow_nan=False).encode()
        with path.open("xb") as stream:
            stream.write(raw)
        return subject.run(BASE, path, received_sha256=hashlib.sha256(raw).hexdigest(),
                           expected_counts=counts, expected_backfill_counts=expected_backfills,
                           caller_verified_sources=True)

    return doc, counts, backfills, execute


def test_prior_formal_train_backfills_have_separate_unchanged_baseline_diagnostics(increment):
    doc, counts, backfills, execute = increment
    original = deepcopy(doc)
    result = execute()
    for key, expected in counts.items():
        assert result["source_counts"][key] == expected
    assert result["by_source"]["SYNTHETIC"] == result["source_counts"]
    assert result["eligible_new_train_rows"] == 1
    assert len(result["rows"]) == 2
    assert result["rows"][1]["qualified_pending_formal"] is True
    assert result["backfill_counts"] == backfills
    assert "emx_completed" not in result["backfill_counts"]
    separate = result["backfill_train_landing_diagnostics"]
    assert separate["eligible_backfilled_train_rows"] == 2
    assert separate["distinct_train_landing_cells"] == 1
    assert separate["baseline_train_rows"] == result["baseline_train_rows"] == 3801
    assert "NOT_NEW_TERMINALS_EMX_ELIGIBILITY_OR_CUMULATIVE_COVERAGE" in separate["scope"]
    for old, new in zip(original["backfilled_prior_rows"], result["backfilled_prior_rows"]):
        assert all(new[key] == value for key, value in old.items())
        assert new["qualified_pending_formal"] is False
    heldout = result["backfilled_prior_rows"][2]
    assert heldout["train_reference_comparison_eligible"] is False
    assert heldout["frozen3801_train_cell_count"] is None
    assert heldout["train_cell_was_empty"] is None
    assert result["cumulative_coverage_gain"] is None
    assert result["cumulative_union_geometry_count"] is None
    assert doc == original


def test_identity_and_pin_reuse_across_new_and_backfill_cohorts_are_rejected(increment):
    doc, _, _, execute = increment
    for field in ("request_id", "geometry_sha256"):
        duplicate = deepcopy(doc)
        duplicate["backfilled_prior_rows"][0][field] = doc["rows"][0][field]
        with pytest.raises(ValueError, match="Duplicate source row " + field):
            execute(duplicate)
    for pin_key, label in (("result_pin", "RESULT"), ("formal_records", "Formal")):
        for field in ("path", "sha256"):
            duplicate = deepcopy(doc)
            prior = duplicate["backfilled_prior_rows"][0][pin_key]
            current = duplicate["rows"][0][pin_key]
            if pin_key == "formal_records":
                prior, current = prior[0]["pin"], current[0]["pin"]
            prior[field] = current[field]
            with pytest.raises(ValueError, match=label + ": duplicate record pin " + field):
                execute(duplicate)
    duplicate = deepcopy(doc)
    duplicate["backfilled_prior_rows"].append(deepcopy(duplicate["backfilled_prior_rows"][0]))
    with pytest.raises(ValueError, match="Duplicate source row request_id"):
        execute(duplicate)


def test_backfills_require_strict_range_exact_join_and_complete_count_envelopes(increment):
    doc, _, backfills, execute = increment
    malformed = [dict(strict=False, core15_eligible=False, formal_records=[]),
                 dict(actual_response=[2.1, 1.2, 13.0, .4], core15_eligible=False, formal_records=[]),
                 dict(formal_records=[]), dict(actual_response=[1.1, 1.2, "13", .4]),
                 dict(status="CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION"),
                 dict(assigned_development_split="UNKNOWN"), dict(result_pin=None)]
    for replacement in malformed:
        invalid = deepcopy(doc)
        invalid["backfilled_prior_rows"][0].update(replacement)
        with pytest.raises(ValueError):
            execute(invalid)
    for key, value in (("request_id", "unrelated-request"), ("assigned_development_split", "test"),
                       ("geometry_sha256", "0" * 64)):
        invalid = deepcopy(doc)
        invalid["backfilled_prior_rows"][0]["formal_records"][0][key] = value
        with pytest.raises(ValueError, match="Formal"):
            execute(invalid)
    with pytest.raises(ValueError, match="explicit backfill count envelope"):
        execute(expected_backfills=None)
    with pytest.raises(ValueError, match="incomplete count envelope"):
        execute(expected_backfills={"formal_admitted": 3})
    with pytest.raises(ValueError, match="Caller backfill envelope: formal_train"):
        execute(expected_backfills=dict(backfills, formal_train=3))
    for key in ("prior_pending_formal_backfill", "ledger_window_fresh_formal"):
        invalid = deepcopy(doc)
        invalid[key] += 1
        with pytest.raises(ValueError, match="Received formal window envelope: " + key):
            execute(invalid)
    omitted = deepcopy(doc)
    omitted.pop("backfilled_prior_rows")
    with pytest.raises(ValueError, match="Received formal window envelope: prior_pending_formal_backfill"):
        execute(omitted, expected_backfills=None)
