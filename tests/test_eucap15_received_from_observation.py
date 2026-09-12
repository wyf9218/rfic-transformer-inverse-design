"""Five focused synthetic tests of the new OBS adapter, not historical reruns."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_received_from_observation as adapter
from research.broadband56_nn import eucap15_received_landing_increment as landing

BASE = Path(__file__).resolve().parents[1] / "docs/research/eucap15_abc_increment_20260910/COVERAGE_BEFORE.csv"


def pin(name, raw=None):
    raw = name.encode() if raw is None else raw
    return dict(path="/synthetic-observation/" + name,
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def entry(name, split="train", fresh=True):
    geometry_sha = hashlib.sha256(name.encode()).hexdigest()
    proposal = dict(request_id=name, candidate_id=name, source="SYNTHETIC", arm="TEST",
                    canonical_geometry_sha256=geometry_sha, assigned_development_split=split,
                    geometry_fields=["x_um", "y_um"], geometry=[1.0, 2.0], geometry_units="um",
                    seed=123, recipe_sha256="a"*64, target_cell=None, predicted_cell=None,
                    feature_order=["Lp_nH", "Ls_nH", "Q_min", "K_abs"], opaque_provenance={"keep": True})
    value = dict(schema=adapter.RESULT_SCHEMA, request_id=name, candidate_id=name,
                 source="SYNTHETIC", arm="TEST", candidate_geometry_identity_sha256=geometry_sha,
                 status="FRESH_EMX_EXTRACTED" if fresh else "ANALYTIC_FAIL_NOT_DISPATCHED",
                 original_proposal=proposal, valid_for_strict_comparison=True if fresh else None,
                 core15_eligible=True if fresh else None, production_accepted=False,
                 actual_response=[1.1, 1.2, 13.0, .4] if fresh else None)
    return dict(pin=pin(name+"/RESULT.json"), value=value)


def header(name):
    return dict(schema=adapter.FORMAL_SCHEMA, request_id=name, pin=pin(name+"/formal.json"),
                utc="2026-09-12T10:30:00Z")


def observation():
    return dict(utc="2026-09-12T11:00:00Z", baseline_utc="2026-09-12T10:00:00Z",
                results=[entry("new-train"), entry("new-pending", "validation"), entry("failed", fresh=False)],
                formal_increment=[header("new-train")], new_core=2, formal_added=1,
                new_result_counts={"FRESH_EMX_EXTRACTED": 2, "ANALYTIC_FAIL_NOT_DISPATCHED": 1},
                result_counts={"FRESH_EMX_EXTRACTED": 999})


def convert(obs, priors=(), verified=True):
    return adapter.convert_observation(obs, source_pin=pin("OBS.json"), prior_received=priors,
                                       caller_verified_sources=verified)


def test_new_window_preserves_raw_fields_pending_zero_and_source_acknowledgement():
    obs = observation()
    obs["results"][2]["value"].pop("core15_eligible")
    original = deepcopy(obs)
    result = convert(obs)
    assert result["new_terminal"] == 3 and result["new_emx_completed"] == 2
    assert result["fresh_formal_this_increment"] == 1 and result["new_core_without_formal"] == 1
    assert result["feature_order"] == adapter.FEATURES
    assert result["rows"][1]["formal_records"] == []
    assert result["rows"][2]["actual_response"] is None
    assert result["rows"][2]["core15_eligible"] is None
    assert result["rows"][2]["source_missing_flag_fields"] == ["core15_eligible"]
    for source, row in zip(obs["results"], result["rows"]):
        assert all(row[key] == value for key, value in source["value"].items())
        assert row["production_accepted"] is row["original_result_accepted_flag"] is False
    assert obs == original
    with pytest.raises(ValueError, match="Caller must verify"):
        convert(obs, verified=False)
    empty = dict(obs, results=[], formal_increment=[], new_result_counts={}, new_core=0, formal_added=0)
    assert convert(empty)["new_terminal"] == 0


def test_pinned_prior_pending_backfill_and_existing_landing_integration(tmp_path):
    prior_obs = dict(observation(), utc="2026-09-12T09:00:00Z", baseline_utc="2026-09-12T08:00:00Z",
                     results=[entry("prior-val", "validation")], formal_increment=[], new_core=1,
                     formal_added=0, new_result_counts={"FRESH_EMX_EXTRACTED": 1})
    prior_doc = convert(prior_obs)
    raw = json.dumps(prior_doc).encode()
    prior_path = tmp_path / "prior.json"
    prior_path.write_bytes(raw)
    prior_pin = pin("prior.json", raw)
    prior_pin["path"] = str(prior_path)
    supplied = dict(pin=prior_pin, raw=raw)
    obs = observation()
    obs["formal_increment"].append(header("prior-val"))
    obs["formal_added"] = 2
    result = convert(obs, [supplied])
    assert result["new_terminal"] == 3 and result["prior_pending_formal_backfill"] == 1
    assert result["ledger_window_fresh_formal"] == 2
    assert result["backfilled_prior_rows"][0]["original_formal_records"] == []
    assert prior_doc["rows"][0]["formal_records"] == []
    with pytest.raises(ValueError, match="SHA/size mismatch"):
        convert(obs, [dict(supplied, raw=raw+b" ")])
    path = tmp_path / "obs.json"
    raw_obs = json.dumps(obs).encode()
    path.write_bytes(raw_obs)
    output = landing.run(BASE, path, received_sha256=hashlib.sha256(raw_obs).hexdigest(),
                         expected_counts=dict(received_terminal=3, emx_completed=2, strict_in_range=2,
                                              formal_admitted=1, pending_formal=1, formal_train=1),
                         expected_backfill_counts=dict(formal_admitted=1, formal_train=0,
                                                       formal_validation=1, formal_test=0),
                         caller_verified_sources=True, observation=True,
                         prior_received=[(prior_path, prior_pin["sha256"])])
    assert output["eligible_new_train_rows"] == 1
    assert output["backfill_counts"]["formal_validation"] == 1
    assert output["backfill_train_landing_diagnostics"]["eligible_backfilled_train_rows"] == 0


def test_duplicate_missing_unmatched_and_conflicting_formal_headers_fail():
    obs = observation()
    duplicate = deepcopy(obs)
    duplicate["formal_increment"].append(deepcopy(duplicate["formal_increment"][0]))
    duplicate["formal_added"] = 2
    with pytest.raises(ValueError, match="Duplicate/missing formal"):
        convert(duplicate)
    missing = dict(obs, formal_increment=[])
    with pytest.raises(ValueError, match="formal_added/header"):
        convert(missing)
    unmatched = dict(obs, formal_increment=[header("unseen")])
    with pytest.raises(ValueError, match="explicit pinned prior"):
        convert(unmatched)
    conflict = deepcopy(obs)
    conflict["formal_increment"][0]["assigned_development_split"] = "test"
    with pytest.raises(ValueError, match="identity conflict"):
        convert(conflict)


def test_window_count_mismatch_never_uses_whole_batch_counts():
    for field, value in (("new_result_counts", {"FRESH_EMX_EXTRACTED": 999}),
                         ("new_core", 3), ("formal_added", 2)):
        bad = deepcopy(observation())
        bad[field] = value
        with pytest.raises(ValueError, match=field):
            convert(bad)
    missing = observation()
    missing.pop("new_result_counts")
    with pytest.raises(ValueError, match="whole-batch counts forbidden"):
        convert(missing)


def test_duplicate_rows_and_geometry_actual_column_conflicts_fail():
    obs = observation()
    duplicate = deepcopy(obs)
    duplicate["results"][1] = deepcopy(duplicate["results"][0])
    with pytest.raises(ValueError, match="Duplicate new request"):
        convert(duplicate)
    for key, value in (("geometry", [1.0]), ("geometry_fields", ["y_um", "x_um"]),
                       ("feature_order", ["Lp_nH", "Ls_nH", "K_abs", "Qmin"])):
        bad = deepcopy(obs)
        bad["results"][1]["value"]["original_proposal"][key] = value
        with pytest.raises(ValueError, match="conflict"):
            convert(bad)
    bad = deepcopy(obs)
    bad["results"][0]["value"]["actual_response"] = [1.0, 2.0, 3.0]
    with pytest.raises(ValueError, match="column/value conflict"):
        convert(bad)
