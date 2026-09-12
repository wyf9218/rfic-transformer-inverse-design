"""New opt-in endpoint construction regressions; no native program calls."""
import copy
import inspect
import subprocess

import gdstk
import pytest

from rfic_transformer_inverse_design.layout.port_endpoint_construction import construct_shared_port_edges


@pytest.fixture(autouse=True)
def forbid_native(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("native calls forbidden in endpoint regression")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def fixture(displacement=1):
    cell, ports = gdstk.Cell("NEW_DEVELOPMENT_FIXTURE"), []
    for i, side in enumerate(("left", "left", "right", "right", "top", "bottom", "top", "bottom")):
        axis = 0 if side in ("left", "right") else 1
        sign = -1 if side in ("left", "bottom") else 1
        cross = i*40.
        endpoint = sign*110.
        edge = endpoint+sign*displacement*.005
        inner = endpoint-sign*30.
        if axis == 0:
            bounds = [(min(edge, inner), cross-5), (max(edge, inner), cross+5)]
        else:
            bounds = [(cross-5, min(edge, inner)), (cross+5, max(edge, inner))]
        cell.add(gdstk.rectangle(*bounds, layer=74))
        ports.append(dict(port_id=f"P{i+1:03d}", side=side, drawing_pair=(74,0),
                          ground_inner_edge_um=sign*100., nominal_terminal_um=endpoint,
                          cross_center_um=cross, width_um=10.))
    cell.add(gdstk.Label("unchanged", (0,0), layer=999))
    return cell, ports


@pytest.mark.parametrize("displacement", [-1,0,1])
def test_eight_edges_are_common_ground_plus_exact_2000_units(displacement):
    cell, ports = fixture(displacement)
    before = [p.points.tolist() for p in cell.polygons]
    result = construct_shared_port_edges(cell=cell, ports=ports)
    assert result['changed_port_count'] == (8 if displacement else 0)
    assert all(p['overlap_grid_units'] == 2000 for p in result['ports'])
    assert cell.labels[0].text == "unchanged" and tuple(cell.labels[0].origin) == (0,0)
    after = [p.points.tolist() for p in cell.polygons]
    for row in result['ports']:
        pi, ei, axis = row['polygon_index'], row['edge_index'], row['axis']
        for vi, (a,b) in enumerate(zip(before[pi],after[pi])):
            assert a[1-axis] == b[1-axis]
            if vi not in (ei,(ei+1)%len(before[pi])):
                assert a == b
    second = construct_shared_port_edges(cell=cell, ports=ports)
    assert second['changed_port_count'] == 0
    assert after == [p.points.tolist() for p in cell.polygons]


@pytest.mark.parametrize("mutation", ['missing','duplicate','offgrid','large_displacement','wrong_pair','wrong_grid','wrong_side'])
def test_malformed_or_ambiguous_input_fails_without_partial_mutation(mutation):
    cell, ports = fixture(1)
    kw = {}
    if mutation == 'missing': ports.pop()
    if mutation == 'duplicate': cell.add(cell.polygons[0].copy())
    if mutation == 'offgrid': cell.polygons[-1].translate(.001,0)
    if mutation == 'large_displacement': ports[-1]['ground_inner_edge_um'] += .02
    if mutation == 'wrong_pair': ports[-1]['drawing_pair'] = (777,0)
    if mutation == 'wrong_grid': kw['grid_um'] = .001
    if mutation == 'wrong_side': ports[-1]['side'] = 'unknown'
    before = [p.points.tolist() for p in cell.polygons]
    with pytest.raises(ValueError): construct_shared_port_edges(cell=cell,ports=ports,**kw)
    assert before == [p.points.tolist() for p in cell.polygons]


def test_exporter_is_explicit_opt_in_and_old_default_is_preserved():
    from rfic_transformer_inverse_design.layout.export import export_transformer_layout
    assert inspect.signature(export_transformer_layout).parameters['port_endpoint_policy'].default == 'legacy'


def test_unrelated_polygon_and_port_inputs_are_unchanged():
    cell,ports = fixture()
    extra = gdstk.rectangle((1000,1000),(1010,1010),layer=35)
    cell.add(extra)
    saved = copy.deepcopy(ports)
    construct_shared_port_edges(cell=cell,ports=ports)
    assert cell.polygons[-1] is extra
    assert ports == saved
