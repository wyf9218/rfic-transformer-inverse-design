"""Synthetic display-only tests; no real metrics, models, training or EMX."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from matplotlib.collections import PathCollection
import numpy as np
import pytest

from research.broadband56_nn import frequency_physical_metric_panels_v2 as module


def synthetic_data():
    rows = []
    for scope, frequencies in ((module.v1.FORMAL, (5, 6, 7)), (module.v1.DEVELOPMENT, (15,))):
        for estimand in module.ESTIMANDS:
            for comparison in ("emx_minus_target", "emx_minus_frozen_proxy"):
                for feature in module.v1.FEATURES:
                    for f in frequencies:
                        rows.append(dict(frequency_ghz=f, model_id=f"synthetic-{f}", dataset_scope=scope,
                            estimand=estimand, comparison=comparison, validity="strict_valid", feature=feature,
                            n=0 if f == 7 else 1, n_request_groups=0 if f == 7 else 1,
                            mae=None if f == 7 else (0.0 if f == 5 else 1e-12),
                            abs_error_p95=None if f == 7 else 1e-12,
                            mae_ci_low=None if f == 7 else 0.0,
                            mae_ci_high=None if f == 7 else 1e-12))
    return dict(metrics=rows, source_pin=dict(sha256="a" * 64))


@pytest.mark.parametrize("scope", module.SCOPES)
@pytest.mark.parametrize("estimand", module.ESTIMANDS)
def test_identical_values_axes_counts_and_caveats_except_scatter_style(scope, estimand):
    data = synthetic_data()
    before = copy.deepcopy(data)
    pages = []
    with patch.object(module.v1, "export", side_effect=lambda fig, out, stem: pages.append(fig)):
        module.v1.mae_p95(data, Path("/synthetic-not-written"), scope, estimand)
    old, new = pages[0], module.make_figure(data, scope, estimand)
    try:
        assert data == before
        assert [text.get_text() for text in old.texts] == [text.get_text() for text in new.texts]
        for old_ax, new_ax in zip(old.axes, new.axes):
            assert old_ax.get_ylim() == new_ax.get_ylim()
            assert new_ax.get_ylim()[0] == 0
            assert old_ax.get_xlim() == new_ax.get_xlim()
            assert [t.get_text() for t in old_ax.get_xticklabels()] == [t.get_text() for t in new_ax.get_xticklabels()]
            assert old_ax.get_ylabel() == new_ax.get_ylabel()
            assert len(old_ax.patches) == len(new_ax.patches)
            for first, second in zip(old_ax.collections, new_ax.collections):
                np.testing.assert_array_equal(first.get_offsets(), second.get_offsets())
                if isinstance(second, PathCollection):
                    assert not second.get_clip_on()
                    assert second.get_zorder() == 4
                    assert second.get_zorder() > new_ax.spines["bottom"].get_zorder()
                else:
                    np.testing.assert_array_equal(first.get_segments(), second.get_segments())
                    assert first.get_clip_on() == second.get_clip_on()
                    assert first.get_zorder() == second.get_zorder()
    finally:
        module.v1.plot_style().close(old)
        module.v1.plot_style().close(new)


def test_zero_retained_missing_not_coerced_or_borrowed():
    fig = module.make_figure(synthetic_data(), module.v1.FORMAL, "selected_q_proxy")
    try:
        dots = [c for c in fig.axes[0].collections if isinstance(c, PathCollection)]
        assert len(dots) == 2
        assert dots[0].get_offsets()[:, 1].tolist() == [0.0, 1e-12]
        assert len(fig.axes[0].patches) == 14
        assert fig.axes[0].get_xticklabels()[2].get_text() == "7\nn=0\nR=0"
        assert fig.axes[0].get_xticklabels()[10].get_text() == "15\nN/S"
    finally:
        module.v1.plot_style().close(fig)


def build_data(tmp_path):
    data = synthetic_data()
    source = tmp_path / "SYNTHETIC_STATS_RECEIPT.json"
    source.write_text('{"synthetic":true}\n')
    metrics = tmp_path / "METRICS.csv"
    metrics.write_text("synthetic only; table is supplied by test fixture\n")
    data.update(source_pin=module.v1.pin(source), files={"METRICS.csv": module.v1.pin(metrics)})
    return data


def test_no_clobber_before_load_or_render(tmp_path):
    with patch.object(module.v1, "load_snapshot") as load, patch.object(module, "make_figure") as make:
        with pytest.raises(ValueError, match="no-clobber"):
            module.build("unused", tmp_path)
        load.assert_not_called()
        make.assert_not_called()


def test_failure_retains_inputs_and_has_no_completion_receipt(tmp_path):
    data = build_data(tmp_path)
    out = tmp_path / "failed"
    with patch.object(module.v1, "load_snapshot", return_value=data), patch.object(module, "make_figure", side_effect=RuntimeError("synthetic failure")):
        with pytest.raises(RuntimeError, match="synthetic failure"):
            module.build("synthetic", out)
    assert (out / "INPUTS.json").is_file()
    assert json.loads((out / "FAILED_RENDER.json").read_text())["existing_artifacts_preserved"]
    assert not (out / "FIGURES_RECEIPT.json").exists()


def test_synthetic_export_complete_after_pins_and_three_formats(tmp_path):
    data = build_data(tmp_path)
    out = tmp_path / "figures"
    with patch.object(module.v1, "load_snapshot", return_value=data):
        receipt = module.build("synthetic", out)
    value = json.loads(Path(receipt["path"]).read_text())
    assert value["status"] == "COMPLETE"
    assert value["visual_qa"] == "NOT_RUN_AUTOMATIC_RENDER_ONLY"
    assert {Path(item["path"]).suffix for item in value["exports"]} == {".png", ".svg", ".pdf"}
    assert value["inference_calls"] == value["model_calls"] == value["remote_calls"] == 0
    assert not (out / ".FIGURES_RECEIPT.pending.json").exists()
    for item in value["artifacts"]:
        module.v1.verify(item)
    assert module.v1.pin(module.v1.__file__)["sha256"] == module.V1_SHA256


def test_scope_and_estimand_fail_closed():
    with pytest.raises(ValueError, match="unsupported dataset scope"):
        module.make_figure(synthetic_data(), "MIXED", "selected_q_proxy")
    with pytest.raises(ValueError, match="unsupported estimand"):
        module.make_figure(synthetic_data(), module.v1.FORMAL, "best_survivors")
