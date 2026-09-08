"""Focused synthetic tests for a standalone visual-only correction."""
import copy
from unittest.mock import patch

import pytest

from research.broadband56_nn import frequency_physical_selection_figures_v2 as v2


def sample():
    return {"source_pin": {"sha256": "a" * 64}, "selection": [
        dict(dataset_scope=v2.base.FORMAL, status="AVAILABLE_DESCRIPTIVE", frequency_ghz=14,
             model_id="synthetic", target_source="HELDOUT_TRIPLE_AUDIT", label_mode="STRICT_LUMPED",
             protocol_sha256="b" * 64, strategy=s[0], feature=f, N_common_complete_requests=1,
             n_request_groups=1, ci_status="NOT_ESTIMABLE", mae=0 if s[0] == "q_emx" else .01)
        for s in v2.STRATEGIES for f in v2.base.FEATURES]}


def test_zero_and_tiny_marks_not_clipped_and_ci_explicit():
    data = sample()
    data["selection"][-4]["mae"] = .00015192110946715687
    before = copy.deepcopy(data)
    fig = v2.make_figure(data, v2.base.FORMAL)
    assert data == before
    assert len(fig.axes) == 4
    for ax in fig.axes:
        assert ax.get_ylim()[0] == 0
        assert all(not marks.get_clip_on() for marks in ax.collections)
        assert all("R=1" in t.get_text() and "CI: NOT_ESTIMABLE" in t.get_text()
                   for t in ax.get_xticklabels())
    assert any("R=1: CI NOT_ESTIMABLE" in t.get_text() for t in fig.texts)
    v2.base.plot_style().close(fig)


@pytest.mark.parametrize("change,match", [
    (lambda rows: rows.append(dict(rows[0])), "exact three-strategy"),
    (lambda rows: rows.pop(), "exact three-strategy"),
    (lambda rows: rows[0].update(model_id="other"), "common context"),
    (lambda rows: [r.update(ci_status="PASS") for r in rows], "R1 CI"),
    (lambda rows: rows[0].update(mae=-.001), "nonnegative"),
    (lambda rows: rows[0].update(mae=float("nan")), "nonnegative"),
    (lambda rows: [r.update(n_request_groups=2) for r in rows], "cluster denominator"),
])
def test_invalid_comparison_rejected(change, match):
    data = sample()
    change(data["selection"])
    with pytest.raises(ValueError, match=match):
        v2.make_figure(data, v2.base.FORMAL)


def test_no_complete_requests_produces_no_fabricated_chart():
    data = sample()
    for row in data["selection"]:
        row.update(status="NOT_AVAILABLE", N_common_complete_requests=0)
    assert v2.make_figure(data, v2.base.FORMAL) is None


def test_existing_output_rejected_without_writes(tmp_path):
    with patch.object(v2.base, "load_snapshot", return_value=sample()):
        with pytest.raises(ValueError, match="no-clobber"):
            v2.build("unused", tmp_path)
    assert list(tmp_path.iterdir()) == []
