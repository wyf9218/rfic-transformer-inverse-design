"""Static UI contract checks; browser interactions are recorded separately."""

from collections import Counter
from html.parser import HTMLParser

import pytest

from rfic_transformer_inverse_design.synthesis.q_sweep_gui import _index_html, build_parser


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.external_scripts = []
        self.stack = []
        self.log_ancestors = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "script" and attrs.get("src"):
            self.external_scripts.append(attrs["src"])
        if attrs.get("id") == "logs":
            self.log_ancestors = list(self.stack)
        if tag not in {"meta", "link", "input", "br", "hr", "img", "path", "line", "circle"}:
            self.stack.append((tag, attrs.get("id")))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break


@pytest.mark.parametrize("mode", ["proxy", "layout", "physical"])
def test_packaged_ui_is_self_contained_and_has_unique_controls(mode):
    html = _index_html(mode)
    parser = Elements()
    parser.feed(html)
    assert not parser.external_scripts
    assert all(count == 1 for count in Counter(parser.ids).values())
    assert not {"viewedQ", "selectedQ", "geometryRows", "errorChart", "rows", "history",
                "downloadGds", "downloadBundle", "modelForm", "loadedModels"} - set(parser.ids)
    assert "__MODE__" not in html
    assert "__MODE_LABEL__" not in html
    assert f'data-mode="{mode}"' in html
    assert "候选误差 CSV" not in html
    assert "运行 JSON" not in html


def test_failure_logs_are_outside_hidden_candidate_result():
    parser = Elements()
    parser.feed(_index_html("proxy"))
    assert parser.log_ancestors is not None
    assert ("section", "result") not in parser.log_ancestors
    assert ("details", "jobTrace") in parser.log_ancestors


def test_gui_defaults_to_historical_100k_without_physical_launch(monkeypatch):
    monkeypatch.delenv("RFIC_Q_SWEEP_MODEL_PROFILE", raising=False)
    monkeypatch.delenv("RFIC_Q_SWEEP_MODE", raising=False)
    args = build_parser().parse_args([])
    assert args.model_profile == "deployed100k"
    assert args.mode == "proxy"


def test_q_training_and_ranking_semantics_remain_distinct():
    html = _index_html("proxy")
    assert "meta.q_target_semantics==='minimum'" in html
    assert "对称精确目标评分，不是 Q 下限违规率" in html
    assert "不额外加训练裕量" in html
    assert "|K| 接近零时百分比不稳定" in html


def test_percentage_errors_are_prominent_without_changing_ranking():
    html = _index_html("proxy")
    assert html.index('id="percentPanel"') < html.index('<div class="detail-grid">')
    assert 'id="currentPercentRms"' in html
    assert 'id="percentRows"' in html
    for label in ("Lp 误差 %", "Ls 误差 %", "Q 误差 %", "|K| 误差 %", "综合 RMS %"):
        assert label in html
    assert "推荐仍按归一化 RMSE，不按百分比重新排序" in html
    assert "c.per_feature_percent_error" in html
    assert "invalid?'—':pct(c.relative_percent_rmse)" in html
