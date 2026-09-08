"""New synthetic label/layout checks only; no historical tests or model calls."""
import copy

import pytest

from research.broadband56_nn import frequency_physical_selection_figures_v3 as v3


@pytest.mark.parametrize("n,status,expected", [
    (1, "NOT_ESTIMABLE", "N/E"), (2, "PROVISIONAL_SMALL_REQUEST_N", "P"),
    (9, "PROVISIONAL_SMALL_REQUEST_N", "P"), (10, "DESCRIPTIVE_REQUEST_BOOTSTRAP", "D")])
def test_exact_ci_mapping(n, status, expected):
    assert v3.ci_code(n, status) == expected


@pytest.mark.parametrize("n,status", [(0, "NOT_ESTIMABLE"), (None, "NOT_ESTIMABLE"),
    (1.5, "NOT_ESTIMABLE"), (float("nan"), "NOT_ESTIMABLE"),
    (2, "NOT_ESTIMABLE"), (1, "PROVISIONAL_SMALL_REQUEST_N"), (10, "UNKNOWN")])
def test_missing_invalid_or_mismatched_ci_rejected(n, status):
    with pytest.raises(ValueError):
        v3.ci_code(n, status)


def test_three_frequency_artists_unchanged_and_tick_boxes_disjoint():
    rows = []
    for f, n, status in [(9, 1, "NOT_ESTIMABLE"), (14, 2, "PROVISIONAL_SMALL_REQUEST_N"),
                         (18, 1, "NOT_ESTIMABLE")]:
        for strategy, *_ in v3.v2.STRATEGIES:
            for feature in v3.base.FEATURES:
                rows.append(dict(dataset_scope=v3.base.FORMAL, status="AVAILABLE_DESCRIPTIVE",
                    frequency_ghz=f, model_id="synthetic", target_source="HELDOUT_TRIPLE_AUDIT",
                    label_mode="STRICT_LUMPED", protocol_sha256="b" * 64, strategy=strategy,
                    feature=feature, N_common_complete_requests=n, n_request_groups=n,
                    ci_status=status, mae=0 if strategy == "q_emx" else .01))
    data = {"source_pin": {"sha256": "a" * 64}, "selection": rows}
    before = copy.deepcopy(data)
    old = v3.v2.make_figure(data, v3.base.FORMAL)
    new = v3.make_figure(data, v3.base.FORMAL)
    new.canvas.draw()
    assert data == before
    for a, b in zip(old.axes, new.axes):
        assert a.get_ylim() == b.get_ylim() and b.get_ylim()[0] == 0
        assert a.get_xlim() == b.get_xlim()
        for x, y in zip(a.collections, b.collections):
            assert (x.get_offsets() == y.get_offsets()).all()
            assert not y.get_clip_on()
        labels = b.get_xticklabels()
        assert [t.get_text() for t in labels] == ["9 GHz\nR=1 | CI N/E", "14 GHz\nR=2 | CI P", "18 GHz\nR=1 | CI N/E"]
        boxes = [t.get_window_extent(new.canvas.get_renderer()) for t in labels]
        assert all(x.x1 < y.x0 for x, y in zip(boxes, boxes[1:]))
    assert any(t.get_text() == v3.CI_KEY for t in new.texts)
    v3.base.plot_style().close(old)
    v3.base.plot_style().close(new)
