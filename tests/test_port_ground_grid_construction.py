"""Exporter regression using synthetic geometry, never simulator evidence."""
from decimal import Decimal
from types import SimpleNamespace

import gdstk
import pytest

from rfic_transformer_inverse_design.layout.export import (
    _add_vdd_bar, _canonicalize_cell_to_foundry_grid, _port_ground_endpoint_grid_units,
)
from rfic_transformer_inverse_design.layout.port_ground_metrics import COUNT, ERROR, EVIDENCE
from tests.test_port_ground_metrics import no_process_launch, produce


@pytest.mark.parametrize('port,layer,datatype', [('P005', 39, 60), ('P007', 74, 0)])
def test_shared_bar_constructor_uses_snapped_references(port, layer, datatype):
    cell = gdstk.Cell(port)
    half_height = 227.5479344263994
    bar = SimpleNamespace(enabled=True, width_um=None, bar_layer=layer,
                          route_layer=None, bar_via_layer=None, route_via_layer=None)
    inductor = SimpleNamespace(vdd_bar=bar, center_tap=True, trace_width_um=5.681751207678356)
    _add_vdd_bar(cell=cell, label_prefix='TEST', pin_name_prefix='TEST',
                 inductor=inductor, terminals=SimpleNamespace(center_tap=(0., 0.)),
                 target_height_um=2*half_height, coil_layer=layer, metal_datatype=datatype,
                 pin_layer=None, pin_datatype=0, label_layer=999, label_datatype=0,
                 port_ground_grid_um=.005)
    _canonicalize_cell_to_foundry_grid(cell=cell, grid_um=.005)
    bbox = cell.polygons[0].bounding_box()
    bottom, top = (round(bbox[i][1]/.005) for i in (0, 1))
    assert top - 43510 == 2000
    assert -43510 - bottom == 2000
    assert top == -bottom
    before = [p.points.tolist() for p in cell.polygons]
    _canonicalize_cell_to_foundry_grid(cell=cell, grid_um=.005)
    assert before == [p.points.tolist() for p in cell.polygons]


@pytest.mark.parametrize('anchor', ['217.5479344263994', '0', '0.0025', '0.0075', '217.5525'])
def test_signed_endpoint_construction_is_symmetric_including_half_grid(anchor):
    top = _port_ground_endpoint_grid_units(ground_reference_um=float(anchor), outward_sign=1, grid_um=.005)
    bottom = _port_ground_endpoint_grid_units(ground_reference_um=-float(anchor), outward_sign=-1, grid_um=.005)
    assert top == -bottom
    expected_anchor = int((Decimal(anchor)/Decimal('.005')).to_integral_value())
    assert top == expected_anchor + 2000


@pytest.mark.parametrize('port', ['P005', 'P007'])
def test_shortened_top_is_one_grid_failure_and_six_others_unchanged(tmp_path, port):
    (tmp_path / 'valid').mkdir()
    (tmp_path / 'short').mkdir()
    _, valid = produce(tmp_path / 'valid')
    def shorten(cell, objects, source):
        old = objects[port]
        points = old.points.copy()
        points[points[:, 1] == points[:, 1].max(), 1] -= .005
        cell.remove(old)
        cell.add(gdstk.Polygon(points, layer=old.layer, datatype=old.datatype))
    _, failed = produce(tmp_path / 'short', shorten)
    assert valid['metrics'] == {COUNT: 8, ERROR: 0.0}
    assert failed['metrics'] == {COUNT: 7, ERROR: .005}
    for good, bad in zip(valid[EVIDENCE], failed[EVIDENCE]):
        if good['port_id'] == port:
            assert round(bad['observed_geometry']['overlap_um']/.005) == 1999
        else:
            assert good == bad
    assert valid['via_stack_check']['overall_status'] == 'PASS'


def test_off_grid_near_equality_is_rejected(tmp_path):
    # A 1 nm residual is below one manufacturing step but is real GDS evidence.
    _, result = produce(tmp_path, lambda cell, objects, _: objects['P005'].translate(0, -.001))
    assert result['power_line_check'] == 'FAIL'
    assert result['grid_audit']['off_grid_vertex_count'] > 0


@pytest.mark.parametrize('grid', [0, -1, float('nan'), float('inf'), .003])
def test_invalid_or_nonintegral_grid_fails(grid):
    with pytest.raises(ValueError):
        _port_ground_endpoint_grid_units(ground_reference_um=217.55, outward_sign=1, grid_um=grid)
