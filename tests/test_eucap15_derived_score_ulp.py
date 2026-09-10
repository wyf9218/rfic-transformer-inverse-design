"""Narrow derived-score roundoff regression; synthetic chains, no native work."""
import importlib.util
import math
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_selected_evidence as evidence

_path = Path(__file__).with_name("test_eucap15_development128_evidence.py")
_spec = importlib.util.spec_from_file_location("_score_ulp_synthetic128", _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


@pytest.mark.parametrize("value", [None, 0.0, 1.0, 0.15276803310865003,
                                   math.nextafter(0.0, math.inf)])
def test_exact_null_zero_and_positive_float(value):
    assert evidence._same_response_score(value, value)


@pytest.mark.parametrize("direction", [-math.inf, math.inf])
@pytest.mark.parametrize("expected", [1.0, 0.15276803310865003, 1e-200, 1e200])
def test_adjacent_only_not_two_steps_including_binade(expected, direction):
    adjacent = math.nextafter(expected, direction)
    second = math.nextafter(adjacent, direction)
    assert evidence._same_response_score(adjacent, expected)
    assert not evidence._same_response_score(second, expected)


@pytest.mark.parametrize("saved,expected", [
    (True, 1.0), (1, 1.0), (0, 0.0), ("1.0", 1.0), ([], 1.0),
    (None, 0.0), (0.0, None), (float("nan"), 1.0), (math.inf, 1.0),
    (-math.inf, 1.0), (-1.0, 1.0), (-0.0, 0.0), (0.0, -0.0),
    (math.inf, math.inf), (1.0, float("nan")), (1.0, 1),
    (math.nextafter(0.0, math.inf), 0.0),
    (0.0, math.nextafter(0.0, math.inf)),
])
def test_domain_type_and_zero_null_boundaries_fail_closed(saved, expected):
    assert not evidence._same_response_score(saved, expected)


@pytest.mark.parametrize("direction", [-math.inf, math.inf])
@pytest.mark.parametrize("strict", [True, False])
def test_full128_chain_accepts_one_step_without_changing_physics(tmp_path, monkeypatch, direction, strict):
    c = _fixture.make_chain(tmp_path, monkeypatch, below_half_srf=strict)
    original = c.read("feature")
    expected = original["normalized_response_score"]
    saved = math.nextafter(expected, direction)
    c.edit("feature", lambda v: v.update(normalized_response_score=saved))
    before = {k: p.read_bytes() for k, p in c.paths.items()}
    result = c.inspect()
    assert result["status"] == ("STRICT_VALID" if strict else "EMX_INVALID")
    assert result["actual"] == original["actual_fresh_emx"]
    assert result["strict_joint_hit"] is strict
    assert result["derived_score_check"] == {
        "policy": "DERIVED_SQRT_BINARY64_ADJACENT_ONE_STEP_ZERO_NULL_EXACT_V1",
        "saved": saved, "recomputed": expected, "comparison": "ONE_ULP"}
    assert before == {k: p.read_bytes() for k, p in c.paths.items()}


@pytest.mark.parametrize("direction", [-math.inf, math.inf])
def test_reclosed_chain_more_than_one_step_rejected(tmp_path, monkeypatch, direction):
    c = _fixture.make_chain(tmp_path, monkeypatch)
    expected = c.read("feature")["normalized_response_score"]
    bad = math.nextafter(math.nextafter(expected, direction), direction)
    c.edit("feature", lambda v: v.update(normalized_response_score=bad))
    with pytest.raises(evidence.SelectedEvidenceError, match="normalized_response_score"):
        c.inspect()


@pytest.mark.parametrize("field", ["actual_fresh_emx", "target", "proxy_self", "score_scale",
    "absolute_hit_tolerances", "emx_minus_target", "emx_minus_proxy",
    "target_relative_signed_percent", "target_relative_absolute_percent"])
def test_one_step_never_tolerated_in_other_numerical_fields(tmp_path, monkeypatch, field):
    c = _fixture.make_chain(tmp_path, monkeypatch)
    def drift(value):
        value[field][0] = math.nextafter(value[field][0], math.inf)
    c.edit("feature", drift)
    with pytest.raises(evidence.SelectedEvidenceError, match=field):
        c.inspect()


@pytest.mark.parametrize("field,bad", [
    ("q_proxy", math.nextafter(13.0, math.inf)),
    ("q_requested", math.nextafter(13.0, math.inf)),
    ("candidate_id", "different-candidate"), ("strict_lumped_valid", 1),
    ("strict_joint_hit", 1), ("joint_response_hit", False),
    ("valid_for_strict_comparison", False), ("within_tolerance", [1, 1, 1, 1]),
])
def test_identity_q_flags_and_hit_decisions_remain_exact(tmp_path, monkeypatch, field, bad):
    c = _fixture.make_chain(tmp_path, monkeypatch)
    c.edit("feature", lambda v: v.update({field: bad}))
    with pytest.raises(evidence.SelectedEvidenceError, match=field):
        c.inspect()


def test_missing_score_not_an_implicit_null(tmp_path, monkeypatch):
    c = _fixture.make_chain(tmp_path, monkeypatch, actual=[float("nan"), 1.48, 13.2, .52])
    c.edit("feature", lambda v: v.pop("normalized_response_score"))
    with pytest.raises(evidence.SelectedEvidenceError, match="normalized_response_score"):
        c.inspect()


def test_nonfinite_original_stays_null_invalid_not_score_zero(tmp_path, monkeypatch):
    c = _fixture.make_chain(tmp_path, monkeypatch, actual=[float("nan"), 1.48, 13.2, .52])
    result = c.inspect()
    assert result["status"] == "EMX_INVALID"
    assert result["strict_joint_hit"] is False
    assert result["derived_score_check"]["saved"] is None
    assert result["derived_score_check"]["comparison"] == "EXACT"
