"""Synthetic figure evidence tests: no model, EMX, training or private inputs."""
import copy

import numpy as np
import pytest

from research.broadband56_nn.frequency_qscan_figures import (
    SCALE, TAU, SOURCES, check_request, fixed_cdf, group_summary,
    pin, render_qscan_figures, target_cells, verify,
)


def fixture():
    freeze = dict(model_id="synthetic", frequency_ghz=15, config={"dataset_scope":"SYNTHETIC_TEST_ONLY"},
                  protocol={"q_values":list(range(10, 21))})
    request = dict(request_id="synthetic-H0", target_source=SOURCES[0], model_id="synthetic", lp_nh=1., ls_nh=1., k_abs=.2,
                   source_geometry_id="synthetic-id", source_geometry_sha256="synthetic-sha", request_order=0,
                   q_proxy=14, N_proxy_finite=11, full_proxy_scan=True, q_emx=None, best_available_emx=None,
                   complete_fresh_emx_candidates=0, independent_solves=0, REAL_EMX_VALIDATION="NOT_RUN",
                   selected_error=[0., 0., 0., 0.], selected_support="IN_TRAIN_MARGINAL_SUPPORT_JOINT_UNKNOWN",
                   selected_analytic_pass=True, selected_joint_response_hit=True, preselected_emx=True)
    candidates = []
    for q in range(10, 21):
        target = np.asarray([1., 1., q, .2])
        response = target + (q-14)*np.asarray([.005, .003, .01, .001])
        candidates.append(dict(request_id=request["request_id"], target_source=SOURCES[0], frequency_ghz=15,
            dataset_scope="SYNTHETIC_TEST_ONLY", model_id="synthetic", q_target=q, target=target.tolist(),
            grid_proxy=response.tolist(), grid_proxy_score=float(np.sqrt(np.mean(((response-target)/SCALE)**2))),
            evidence_source="SELF_PROXY", emx_status="NOT_RUN", actual_response=None, unique_emx_solve=False,
            inference_status="FINITE", within_tolerance=list(np.abs(response-target)<=TAU), analytic_grid=q < 19,
            q_proxy=14, proxy_preselected=q == 14, support_status="IN_TRAIN_MARGINAL_SUPPORT_JOINT_UNKNOWN"))
    return freeze, request, candidates


def test_fixed_cdf_retains_missing_in_original_denominator():
    x, y = fixed_cdf([.1, .5, np.nan], 4)
    assert x.tolist() == [.1, .5]
    assert y.tolist() == [.25, .5]


@pytest.mark.parametrize("values,n", [([1, 2], 1), ([1], 0), ([-1], 1)])
def test_fixed_cdf_refuses_shrunken_or_invalid_denominator(values, n):
    with pytest.raises(ValueError):
        fixed_cdf(values, n)


def test_candidate_scan_preserves_analytic_failures_and_proxy_only_state():
    freeze, request, rows = fixture()
    result = check_request(request, rows, freeze)
    assert result["q_proxy"] == 14 and result["q_emx"] is None
    assert result["analytic_candidate_failures"] == 2
    assert result["selected_score"] == 0


@pytest.mark.parametrize("mutation", ["score", "winner", "identity", "tolerance", "physical", "missing", "duplicate", "selected_error", "analytic"])
def test_candidate_evidence_mismatch_rejected(mutation):
    freeze, request, rows = fixture()
    if mutation == "score": rows[0]["grid_proxy_score"] = 1
    elif mutation == "winner": request["q_proxy"] = 15
    elif mutation == "identity": rows[0]["frequency_ghz"] = 16
    elif mutation == "tolerance": rows[0]["within_tolerance"][0] = False
    elif mutation == "physical": rows[0]["actual_response"] = [1, 1, 10, .2]
    elif mutation == "missing": rows.pop()
    elif mutation == "duplicate": rows[-1] = copy.deepcopy(rows[0])
    elif mutation == "selected_error": request["selected_error"] = [1, 0, 0, 0]
    elif mutation == "analytic": request["selected_analytic_pass"] = False
    with pytest.raises(ValueError):
        check_request(request, rows, freeze)


def test_incomplete_scan_has_no_selection_but_keeps_original_candidate_count():
    freeze, request, rows = fixture()
    rows[0].update(grid_proxy=[None]*4, grid_proxy_score=None, inference_status="REJECTED_OUTSIDE_SUPPORT", within_tolerance=[False]*4)
    for row in rows:
        row.update(q_proxy=None, proxy_preselected=False)
    request.update(q_proxy=None, N_proxy_finite=10, full_proxy_scan=False, selected_error=None,
                   selected_analytic_pass=False, selected_joint_response_hit=False, selected_support="NOT_SELECTED")
    result = check_request(request, rows, freeze)
    assert result["selected_score"] is None
    assert result["nonfinite_or_rejected_candidates"] == 1
    assert not result["end_to_end_proxy_hit"]


def test_summary_keeps_selected_analytic_failure_in_residuals_and_denominator():
    freeze, request, rows = fixture()
    a = check_request(request, rows, freeze)
    b = dict(a, request_id="synthetic-C0", target_source=SOURCES[1], selected_analytic_pass=False, end_to_end_proxy_hit=False)
    groups = group_summary([a, b])
    assert groups[1]["N_requested"] == 1 and groups[1]["features"]["Lp_nH"]["N_finite"] == 1
    assert groups[1]["analytic_pass"] == 0 and groups[1]["joint_response_hit"] == 1


def test_cells_include_upper_boundary_missing_failed_and_outside():
    base = dict(q_proxy=14, selected_score=.2, selected_support="EXTRAPOLATION", selected_analytic_pass=False)
    rows = [dict(base, lp_nh=x, ls_nh=y) for x, y in [(0, 0), (1, 1), (2, 2)]]
    rows.append(dict(base, lp_nh=.8, ls_nh=.8, q_proxy=None, selected_score=None, selected_support="NOT_SELECTED"))
    data = target_cells(rows, [0, .5, 1], [0, .5, 1])
    assert data["N_requested"] == 4 and data["N_outside"] == 1
    corner = data["cells"][-1]
    assert corner["N_requested"] == 2 and corner["N_finite_score"] == 1
    assert corner["N_unselected"] == 1 and corner["N_analytic_failed"] == 1
    assert corner["selected_extrapolation_fraction"] == .5
    assert data["cells"][1]["mean_score_available"] is None


def test_source_pin_detects_mutated_bytes(tmp_path):
    path = tmp_path/"synthetic"
    path.write_text("before")
    source = pin(path)
    path.write_text("after")
    with pytest.raises(ValueError, match="pin mismatch"):
        verify(source)


def test_renderer_no_clobber_and_missing_input(tmp_path):
    out = tmp_path/"existing"
    out.mkdir()
    with pytest.raises(FileExistsError):
        render_qscan_figures(tmp_path/"absent", out)
    with pytest.raises(FileNotFoundError):
        render_qscan_figures(tmp_path/"absent", tmp_path/"new")
    assert not (tmp_path/"new").exists()


@pytest.mark.parametrize("has_failure", [False, True])
def test_distribution_failure_legend_keeps_hatch_when_category_is_empty(has_failure):
    from matplotlib.colors import to_rgba
    from research.broadband56_nn.frequency_qscan_figures import _plotting, _draw_distribution, ORANGE
    freeze, request, candidates = fixture()
    h = check_request(request, candidates, freeze)
    c = dict(h, target_source=SOURCES[1], selected_analytic_pass=not has_failure,
             end_to_end_proxy_hit=not has_failure)
    rows = [h, c]
    plt = _plotting()
    fig = _draw_distribution(plt, rows, group_summary(rows), "SYNTHETIC TEST ONLY")
    try:
        legend = fig.axes[1].get_legend()
        handles = dict(zip([text.get_text() for text in legend.get_texts()], legend.legend_handles))
        assert handles['analytic FAIL'].get_hatch() == '///'
        assert handles['analytic FAIL'].get_edgecolor() == to_rgba(ORANGE)
        assert handles['analytic FAIL'].get_facecolor() == to_rgba('white')
    finally:
        plt.close(fig)
