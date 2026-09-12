"""New feed-only intervention regressions, synthetic values not private PDK."""
import copy

import numpy as np
import pytest
import torch

from research.broadband56_nn.eucap15_feedline_projection import FeedlineProjection, VERSION


FIELDS = ["primary_outer_width_um", "primary_outer_height_um", "secondary_outer_width_um",
          "secondary_outer_height_um", "line_width_um", "primary_terminal_y_span_um",
          "secondary_terminal_y_span_um", "offset_um", "primary_feed_extension_um", "secondary_feed_extension_um"]


def contract():
    return dict(field_names=FIELDS, lower=[160,160,160,160,3,20,20,-90,100,100],
        upper=[520,520,520,520,12,90,90,90,320,320], units="um", grid_um=.005,
        grid_source_sha256="1"*64, grid_status="SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR",
        topology_contract=dict(available=True, power_line_port_ground_overlap=dict(
            enabled=True, bar_offset_um=12, shield_opening_clearance_um=10, training_safety_margin_um=0)))


def candidate():
    return np.array([500.,240.,200.,230.,11.,80.,40.,10.,310.,100.])


def test_explicit_opt_in_no_old_module_change():
    with pytest.raises(TypeError):
        FeedlineProjection(contract())
    with pytest.raises(ValueError, match="Explicit development"):
        FeedlineProjection(contract(), version="hard_feasible_topology_v1")


def test_secondary_deficit_feed_only_and_no_old_result_inheritance():
    g = candidate(); old = g.copy(); c = contract(); saved = copy.deepcopy(c)
    result = FeedlineProjection(c, version=VERSION).construct(g)
    assert result["status"] == "ANALYTIC_ONLY_PASS"
    assert result["changed_grid_fields"] == ["secondary_feed_extension_um"]
    assert result["new_grid_geometry_um"][9] == 173.
    assert np.array_equal(g, old) and c == saved
    assert result["new_proxy"] is None and result["new_q_proxy"] is None
    assert result["REAL_EMX_VALIDATION"] == "NOT_RUN"


def test_primary_deficit_and_offset_direction():
    g = candidate(); g[0],g[2],g[7],g[8],g[9] = 200,500,-10,100,310
    result = FeedlineProjection(contract(), version=VERSION).construct(g)
    assert result["changed_grid_fields"] == ["primary_feed_extension_um"]
    assert result["new_grid_geometry_um"][8] == 193.


def test_grid_ceiling_after_snapping_avoids_reintroduced_deficit():
    g = candidate(); g[0],g[2],g[7],g[4] = 500.006,200.001,10.001,11.001
    result = FeedlineProjection(contract(), version=VERSION).construct(g)
    assert result["analytical_pass"]
    value = result["new_grid_geometry_um"][9]
    assert value >= result["new_continuous_geometry_um"][9] - .005
    assert abs(value / .005 - round(value / .005)) < 1e-8


def test_noop_and_idempotence_for_valid_geometry():
    g = candidate(); g[9] = 240
    projection = FeedlineProjection(contract(), version=VERSION)
    first = projection.construct(g)
    assert first["changed_grid_fields"] == []
    second = projection.construct(first["new_grid_geometry_um"])
    assert first["new_grid_geometry_um"] == second["new_grid_geometry_um"]


def test_gradient_retained_through_required_feed_and_identity_coordinates():
    g = torch.tensor(candidate(), dtype=torch.float64, requires_grad=True)
    out = FeedlineProjection(contract(), version=VERSION).continuous(g)
    out[9].backward()
    np.testing.assert_allclose(g.grad.numpy(), [.5,0,-.5,0,1,0,0,-1,0,0], atol=1e-14)
    assert out.grad_fn is not None


def test_nonfeed_failure_not_repaired_or_misreported():
    c = contract(); c["lower"][1] = 100
    g = candidate(); g[1] = 120; g[5] = 90
    result = FeedlineProjection(c, version=VERSION).construct(g)
    assert result["status"] == "HOLD_ANALYTIC_FAIL"
    assert result["new_grid_geometry_um"][5] == 90


def test_required_feed_over_upper_bound_rejected_without_expansion():
    c = contract(); c["upper"][9] = 150
    g = candidate()
    with pytest.raises(ValueError, match="exceeds unchanged upper"):
        FeedlineProjection(c, version=VERSION).construct(g)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 999.])
def test_nonfinite_or_outside_input_not_clipped(bad):
    g = candidate(); g[0] = bad
    with pytest.raises(ValueError, match="finite and within"):
        FeedlineProjection(contract(), version=VERSION).construct(g)


def test_unknown_topology_or_grid_refuses_guessing():
    c = contract(); c.pop("topology_contract")
    with pytest.raises(ValueError, match="Source-bound topology"):
        FeedlineProjection(c, version=VERSION)
    c = contract(); c["grid_source_sha256"] = "unknown"
    with pytest.raises(ValueError, match="source-SHA"):
        FeedlineProjection(c, version=VERSION)


def test_contract_input_isolation_and_dimension_guard():
    c = contract(); projection = FeedlineProjection(c, version=VERSION)
    c["lower"][9] = 315
    assert projection.construct(candidate())["analytical_pass"]
    with pytest.raises(ValueError, match="ordered geometry"):
        projection.construct([1,2,3])
