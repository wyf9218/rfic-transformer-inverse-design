"""Synthetic metadata-only tests for the original256 closed-delta driver.

No native chain parser, models, real records, or driver.run invocation. The
only patched scientific constant is the exact prior-ID digest for this fixture.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_acquisition_delta as subject
from research.broadband56_nn.eucap15_selected_evidence import SelectedEvidenceError


def fake_pin(name):
    raw = name.encode()
    return dict(path="/synthetic/" + name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


@pytest.fixture
def frame(monkeypatch):
    proposals = [dict(request_id=f"proposal-{i:03}", candidate_id=f"candidate-{i:03}-q17",
                      q_proxy=17, global_order=i,
                      local_dispatch_eligible=not (48 <= i <= 100),
                      arm="COVERAGE_DIRECTED" if i % 2 else "GEOMETRY_DOE_CONTROL",
                      arm_order=(i + 1) // 2) for i in range(1, 257)]
    batch = {"rows": {p["candidate_id"]: deepcopy(p) for p in proposals}}
    old_prefix = {"rows": [dict(request_id=p["request_id"], candidate_id=p["candidate_id"],
                                 result=fake_pin(p["request_id"] + "/RESULT.json"))
                            for p in proposals[:32]]}
    old_fixed = {"items": [dict(request_id=p["request_id"], candidate_id=p["candidate_id"],
                                  original_result=fake_pin(p["request_id"] + "/RESULT.json"))
                             for p in proposals[32:45]]}
    old_rows = old_prefix["rows"] + old_fixed["items"]
    raw = ("\n".join(sorted(r["request_id"] for r in old_rows)) + "\n").encode()
    monkeypatch.setattr(subject, "OLD_IDS_SHA", hashlib.sha256(raw).hexdigest())
    observed = []
    for p in proposals:
        order = p["global_order"]
        if order <= 46:
            status = "FRESH_EMX_EXTRACTED"
        elif order == 47:
            status = "CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION"
        elif order <= 100:
            status = "FROZEN_PROPOSAL_HELD_NO_REPLACEMENT"
        else:
            observed.append(dict(job=deepcopy(p), result={"status": "ABSENT_AT_READ"}))
            continue
        document = {k: p[k] for k in ("request_id", "candidate_id", "q_proxy")}
        document["status"] = status
        observed.append(dict(job=deepcopy(p), result=dict(status="STABLE_JSON", document=document,
            pin=fake_pin(p["request_id"] + "/RESULT.json"))))
    items = []
    for row in observed[45:47]:
        item = {k: row["job"][k] for k in ("request_id", "candidate_id", "q_proxy")}
        item.update(original_result=deepcopy(row["result"]["pin"]),
                    original_status=row["result"]["document"]["status"])
        items.append(item)
    context = dict(selection_rule=subject.RULE, original_denominator=256,
                   excluded_already_delivered=45, retained_original_holds=53,
                   pending_at_snapshot=156, snapshot_utc="2026-09-10T00:00:00+00:00",
                   new_terminal_count=2, no_replacement=True)
    exclusions = dict(original_denominator=256, excluded_count=45, excluded={
        r["request_id"]: {"result": deepcopy(r.get("result", r.get("original_result")))}
        for r in old_rows})
    counts = Counter(r["result"]["document"]["status"] if r["result"]["status"] == "STABLE_JSON"
                     else "ABSENT_AT_READ" for r in observed)
    docs = dict(old_prefix=old_prefix, old_fixed=old_fixed, exclusions=exclusions,
                export={"context": deepcopy(context)},
                request={"payload": {"context": deepcopy(context), "items": deepcopy(items)}},
                closed={"items": deepcopy(items), "count": 2},
                observation={"groups": {"original256": {"rows": observed, "counts": dict(counts)}}})
    return docs, batch


def freeze(docs, batch):
    return subject.freeze_delta(docs["closed"]["items"], docs["old_prefix"], docs["old_fixed"],
        subject.unique_pairs(docs["request"]["payload"]["items"]),
        subject.unique_pairs(docs["old_prefix"]["rows"] + docs["old_fixed"]["items"]), batch)


def test_complete_new_closed_success_and_failure_are_bound_without_mutation(frame):
    docs, batch = frame
    before = deepcopy((docs, batch))
    expected = subject.unique_pairs(docs["old_prefix"]["rows"] + docs["old_fixed"]["items"])
    assert len(expected) == 45
    assert freeze(docs, batch) == expected
    assert subject.bind_owner_snapshot(docs, batch) == expected
    assert (docs, batch) == before


def test_omitted_new_failure_rejected_even_when_frozen_whitelist_also_omits_it(frame):
    docs, batch = frame
    docs["closed"]["items"].pop()
    docs["closed"]["count"] = 1
    docs["request"]["payload"]["items"].pop()
    with pytest.raises(SelectedEvidenceError, match="not all and only"):
        subject.bind_owner_snapshot(docs, batch)


def test_q_drift_in_both_whitelist_and_closed_item_rejected(frame):
    docs, batch = frame
    docs["closed"]["items"][0]["q_proxy"] = 18
    docs["request"]["payload"]["items"][0]["q_proxy"] = 18
    with pytest.raises(SelectedEvidenceError, match="not all and only"):
        subject.bind_owner_snapshot(docs, batch)


def test_old45_intersection_rejected(frame):
    docs, batch = frame
    old = deepcopy(docs["old_prefix"]["rows"][0])
    docs["closed"]["items"] = [old]
    docs["request"]["payload"]["items"] = [deepcopy(old)]
    with pytest.raises(SelectedEvidenceError, match="old physics repeated"):
        freeze(docs, batch)


def test_reverse_original_order_rejected(frame):
    docs, batch = frame
    docs["closed"]["items"].reverse()
    docs["request"]["payload"]["items"].reverse()
    with pytest.raises(SelectedEvidenceError, match="not original global order"):
        freeze(docs, batch)


def test_foreign_candidate_rejected(frame):
    docs, batch = frame
    for items in (docs["closed"]["items"], docs["request"]["payload"]["items"]):
        items[0].update(request_id="foreign-request", candidate_id="foreign-candidate-q17")
    with pytest.raises(SelectedEvidenceError, match="foreign proposal"):
        freeze(docs, batch)


def test_owner_exclusion_result_pin_must_be_identical(frame):
    docs, batch = frame
    key = docs["old_prefix"]["rows"][0]["request_id"]
    docs["exclusions"]["excluded"][key]["result"]["bytes"] += 1
    with pytest.raises(SelectedEvidenceError, match="old result pin changed"):
        subject.bind_owner_snapshot(docs, batch)


def test_unknown_outer_snapshot_read_state_cannot_be_pending(frame):
    docs, batch = frame
    docs["observation"]["groups"]["original256"]["rows"][-1]["result"]["status"] = "READ_ERROR"
    with pytest.raises(SelectedEvidenceError, match="unknown snapshot state"):
        subject.bind_owner_snapshot(docs, batch)


def test_unknown_stable_terminal_rejected_even_with_matching_snapshot_counts(frame):
    docs, batch = frame
    group = docs["observation"]["groups"]["original256"]
    group["rows"][47]["result"]["document"]["status"] = "UNRECOGNIZED_TERMINAL"
    group["counts"]["FROZEN_PROPOSAL_HELD_NO_REPLACEMENT"] -= 1
    group["counts"]["UNRECOGNIZED_TERMINAL"] = 1
    for context in (docs["export"]["context"], docs["request"]["payload"]["context"]):
        context["retained_original_holds"] -= 1
    with pytest.raises(SelectedEvidenceError, match="unknown|unsupported|unrecognized"):
        subject.bind_owner_snapshot(docs, batch)


def result_row(source, state, *, target=None, actual=None, error=None, strict=False, hit=None):
    return dict(source=source, state=state, target=target, actual=actual,
                emx_minus_target=error, strict_valid=strict, strict_joint_hit=hit,
                core_eligible=strict)


def test_summary_retains_targeted_failure_denominator_and_doe_null_targets():
    target = [1.0, 1.0, 10.0, .5]
    rows = [
        result_row("SPARSE_TARGETED", "STRICT_VALID", target=target,
                   actual=[1.01, 1.02, 10.1, .51], error=[.01, .02, .1, .01], strict=True, hit=True),
        result_row("SPARSE_TARGETED", "EMX_INVALID", target=target,
                   actual=[1.1, 1.2, 11.0, .6], error=[.1, .2, 1.0, .1], hit=False),
        result_row("SPARSE_TARGETED", "GDS_FAIL", target=target),
        result_row("GEOMETRY_DOE", "STRICT_VALID", actual=[1.0, 1.0, 10.0, .5], strict=True),
        result_row("EXPLORATION", "SOLVER_FAIL"),
    ]
    before = deepcopy(rows)
    summary = subject.summarize_rows(rows, dict(retained_original_holds=53,
        pending_at_snapshot=153, snapshot_utc="2026-09-10T00:00:00+00:00"))
    assert rows == before
    assert summary["original_proposals"] == 256
    assert summary["new_closed_delta"] == 5
    assert summary["targeted_original_delta_denominator"] == 3
    assert summary["targeted_strict_valid"] == summary["targeted_strict_joint_hits"] == 1
    assert summary["targeted_strict_joint_hit_rate"] == pytest.approx(1 / 3)
    assert summary["state_counts"]["GDS_FAIL"] == summary["state_counts"]["SOLVER_FAIL"] == 1
    assert all(m["original_targeted_delta_denominator"] == 3 and m["strict_numeric_denominator"] == 1
               for m in summary["physical_feature_metrics"])
    assert [m["mae"] for m in summary["physical_feature_metrics"]] == pytest.approx([.01, .02, .1, .01])
    assert summary["equal_budget_comparison"] is summary["coverage_gain"] is None
    assert rows[2]["actual"] is rows[2]["strict_joint_hit"] is None
    assert rows[3]["target"] is rows[3]["emx_minus_target"] is None


def test_summary_without_strict_targeted_rows_preserves_undefined_errors():
    rows = [result_row("SPARSE_TARGETED", "FEATURE_FAIL", target=[1, 1, 10, .5]),
            result_row("SPARSE_TARGETED", "DRC_FAIL", target=[1, 1, 10, .5]),
            result_row("GEOMETRY_DOE", "GDS_FAIL")]
    summary = subject.summarize_rows(rows, dict(retained_original_holds=53,
        pending_at_snapshot=155, snapshot_utc="2026-09-10T00:00:00+00:00"))
    assert summary["targeted_original_delta_denominator"] == 2
    assert summary["targeted_strict_joint_hit_rate"] == 0
    assert summary["new_closed_delta"] == 3
    for metric in summary["physical_feature_metrics"]:
        assert metric["strict_numeric_denominator"] == 0
        assert metric["mae"] is metric["rmse"] is metric["target_relative_mape_percent"] is None
    assert all(row["actual"] is None and row["strict_joint_hit"] is None for row in rows)


def test_combine_evidence_reuses_identical_pin_but_rejects_conflicting_bytes():
    original = fake_pin("source.json")
    entry = dict(original=original, resolved={**original, "path": "/synthetic/mirror/source.json"})
    union = {original["path"]: deepcopy(entry)}
    subject.combine_evidence(union, {original["path"]: deepcopy(entry)})
    assert union == {original["path"]: entry}
    conflicting = deepcopy(entry)
    conflicting["resolved"]["bytes"] += 1
    with pytest.raises(SelectedEvidenceError, match="conflicting evidence pin"):
        subject.combine_evidence(union, {original["path"]: conflicting})
    assert union == {original["path"]: entry}


# Narrow follow-up for the newly added snapshot/source-index gates. These cases
# run separately with -k added_; the original twelve as-tested bytes are archived.
@pytest.fixture
def strict_frame(frame):
    docs, batch = frame
    for proposal in batch["rows"].values():
        proposal["local_dispatch_eligible"] = not (48 <= proposal["global_order"] <= 100)
    return docs, batch


def index_fixture():
    export_root = Path("/synthetic/export")
    source = fake_pin("native/artifact.json")
    entry = dict(source=source, relative_path="files/artifact.json",
                 local_path=str(export_root / "files/artifact.json"))
    return entry, {source["path"]: entry["local_path"]}, export_root


def test_added_source_index_canonical_inside_export_root_passes():
    entry, paths, root = index_fixture()
    before = deepcopy((entry, paths))
    assert subject.bind_source_index_entry(entry, paths, root) == (entry["source"]["path"], entry["source"])
    assert (entry, paths) == before


def test_added_source_index_absolute_relative_path_rejected():
    entry, paths, root = index_fixture()
    entry["relative_path"] = entry["local_path"]
    with pytest.raises(SelectedEvidenceError, match="absolute relative path"):
        subject.bind_source_index_entry(entry, paths, root)


def test_added_source_index_parent_relative_path_rejected():
    entry, paths, root = index_fixture()
    entry["relative_path"] = "../artifact.json"
    with pytest.raises(SelectedEvidenceError, match="noncanonical"):
        subject.bind_source_index_entry(entry, paths, root)


def test_added_source_index_local_outside_export_root_rejected():
    entry, paths, root = index_fixture()
    entry["local_path"] = "/synthetic/other/files/artifact.json"
    paths[entry["source"]["path"]] = entry["local_path"]
    with pytest.raises(SelectedEvidenceError, match="local mirror escapes export root"):
        subject.bind_source_index_entry(entry, paths, root)


def test_added_snapshot_previous45_and_frozen_holds_bind_successfully(strict_frame):
    docs, batch = strict_frame
    before = deepcopy((docs, batch))
    assert len(subject.bind_owner_snapshot(docs, batch)) == 45
    assert (docs, batch) == before


def test_added_snapshot_previous45_result_pin_drift_rejected(strict_frame):
    docs, batch = strict_frame
    row = docs["observation"]["groups"]["original256"]["rows"][0]
    row["result"]["pin"]["sha256"] = "a" * 64
    with pytest.raises(SelectedEvidenceError, match="previously delivered result changed"):
        subject.bind_owner_snapshot(docs, batch)


def test_added_snapshot_previous45_cannot_be_absent(strict_frame):
    docs, batch = strict_frame
    docs["observation"]["groups"]["original256"]["rows"][0]["result"] = {"status": "ABSENT_AT_READ"}
    with pytest.raises(SelectedEvidenceError, match="previously delivered result changed"):
        subject.bind_owner_snapshot(docs, batch)


def test_added_snapshot_frozen_eligible_cannot_be_claimed_as_hold(strict_frame):
    docs, batch = strict_frame
    row = docs["observation"]["groups"]["original256"]["rows"][47]
    batch["rows"][row["job"]["candidate_id"]]["local_dispatch_eligible"] = True
    row["job"]["local_dispatch_eligible"] = True
    with pytest.raises(SelectedEvidenceError, match="held state conflicts with frozen eligibility"):
        subject.bind_owner_snapshot(docs, batch)
