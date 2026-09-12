"""New process-free runner/evaluator forwarding and label-containment checks."""
from types import SimpleNamespace

import gdstk
import pytest

from scripts import run_candidate_queue_dataset as single
from scripts import run_candidate_queue_dataset_parallel as parallel
from rfic_transformer_inverse_design.api import default_run_config
from rfic_transformer_inverse_design.execution import evaluator as evaluation
from rfic_transformer_inverse_design.layout.port_endpoint_construction import construct_shared_port_edges
from tests.test_eucap15_shared_port_edges import fixture

POLICY = 'shared_port_edges_20260912_v1'


@pytest.mark.parametrize('policy', ['legacy', POLICY])
def test_parallel_cli_reaches_actual_single_evaluator_export_call(tmp_path, monkeypatch, policy):
    csv = tmp_path/'candidates.csv';csv.write_text('fixture\n')
    shard = tmp_path/'new_shard';shard.mkdir()
    cfg = default_run_config('1t1t');geometry=cfg.bounds.midpoint()
    # This test proves argument forwarding, not geometry admissibility. The
    # default bounds midpoint need not be a valid physical training sample.
    monkeypatch.setattr(type(cfg.bounds),'validate',lambda self,g:[])
    monkeypatch.setattr(type(geometry),'validate',lambda self:[])
    monkeypatch.setattr(single,'load_run_config',lambda _:cfg)
    monkeypatch.setattr(single,'_apply_overrides',lambda c,a:c)
    monkeypatch.setattr(single,'_read_csv',lambda _: [{}])
    monkeypatch.setattr(single,'_select_input_rows',lambda r,a:(r,[]))
    monkeypatch.setattr(single,'_geometry_from_rows',lambda *a:([geometry],[{}],[]))
    monkeypatch.setattr(single,'_frequency_checks',lambda *a:[])
    monkeypatch.setattr(evaluation,'run_transformer_gdstk_checks',
                        lambda **kw:SimpleNamespace(errors=[],warnings=[],metrics={}))
    observed=[]
    class StopBeforeExport(BaseException):pass
    def capture_export(**kwargs):
        observed.append(kwargs)
        raise StopBeforeExport()
    monkeypatch.setattr(evaluation,'export_transformer_layout',capture_export)
    def in_process_shard(command,**kwargs):
        assert command[1].endswith('/scripts/run_candidate_queue_dataset.py')
        assert ('--port-endpoint-policy' in command) == (policy != 'legacy')
        return single.main(command[2:])
    monkeypatch.setattr(parallel.subprocess,'run',in_process_shard)
    args=parallel._parse_args(['--candidate-csv',str(csv),'--out-dir',str(tmp_path),
                              '--create-only','--port-endpoint-policy',policy])
    with pytest.raises(StopBeforeExport):parallel._run_shard(0,1,csv,shard,args)
    assert len(observed)==1 and observed[0]['port_endpoint_policy']==policy
    assert observed[0]['validate_geometry'] is False


def test_policy_is_part_of_new_cache_identity_only(tmp_path):
    cfg=default_run_config('1t1t');geometry=cfg.bounds.midpoint()
    old=evaluation.TransformerEmxEvaluator(cfg,tmp_path/'default')
    explicit=evaluation.TransformerEmxEvaluator(cfg,tmp_path/'legacy',port_endpoint_policy='legacy')
    new=evaluation.TransformerEmxEvaluator(cfg,tmp_path/'new',port_endpoint_policy=POLICY)
    assert old.cache_key(geometry)==explicit.cache_key(geometry)
    assert old.cache_key(geometry)!=new.cache_key(geometry)


@pytest.mark.parametrize('side_inset', [1,50,200])
def test_original_labels_are_kept_inside_planned_conductors(side_inset):
    cell,ports=fixture(1)
    for p in ports:
        axis=0 if p['side'] in ('left','right') else 1
        sign=-1 if p['side'] in ('left','bottom') else 1
        point=[p['cross_center_um'],p['cross_center_um']]
        point[axis]=p['nominal_terminal_um']-sign*side_inset*.005
        cell.add(gdstk.Label(p['port_id'],point,layer=999))
    before=[(l.text,tuple(l.origin)) for l in cell.labels]
    result=construct_shared_port_edges(cell=cell,ports=ports,require_port_labels=True)
    assert len(result['label_containment_checks'])==8
    assert all(p['endpoint_inset_grid_units']==side_inset for p in result['label_containment_checks'])
    assert before==[(l.text,tuple(l.origin)) for l in cell.labels]


@pytest.mark.parametrize('error',['missing','outside','on_edge'])
def test_label_failure_is_atomic_and_does_not_move_label(error):
    cell,ports=fixture(1)
    for p in ports:
        axis=0 if p['side'] in ('left','right') else 1
        sign=-1 if p['side'] in ('left','bottom') else 1
        point=[p['cross_center_um'],p['cross_center_um']]
        point[axis]=p['nominal_terminal_um']-sign*.25
        if p['port_id']=='P008':
            if error=='missing':continue
            point[axis]=p['nominal_terminal_um']+(sign*.005 if error=='outside' else 0)
        cell.add(gdstk.Label(p['port_id'],point,layer=999))
    before=[p.points.tolist() for p in cell.polygons]
    with pytest.raises(ValueError):construct_shared_port_edges(cell=cell,ports=ports,require_port_labels=True)
    assert before==[p.points.tolist() for p in cell.polygons]


@pytest.mark.parametrize('runner',[single,parallel])
def test_runner_rejects_unknown_policy(runner):
    with pytest.raises(SystemExit):runner._parse_args(['--candidate-csv','unused','--out-dir','unused','--port-endpoint-policy','unknown'])
