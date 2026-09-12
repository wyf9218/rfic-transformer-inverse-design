"""One saved DOE006 in-memory replay plus four targeted failure guards."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import subprocess

from lineage_cross_binding_v1 import bind_cross_centers


def pin(path):
    path=Path(path).resolve(); data=path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def main():
    p=argparse.ArgumentParser(); p.add_argument('--trace-dir', type=Path, required=True)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(exist_ok=False,parents=True)
    subprocess.Popen=lambda *x,**k: (_ for _ in ()).throw(RuntimeError('NATIVE_FORBIDDEN'))
    sys.path.insert(0,str(a.repo.resolve()))
    import gdstk
    from rfic_transformer_inverse_design.layout import port_endpoint_construction as endpoint
    assert pin(endpoint.__file__)['sha256']=='df712c44abb2113d6cced55653d8da169ecae74a7061b8dec756a4f73f09c151'
    tp=a.trace_dir/'CANONICALIZATION_TRACE.json';cp=a.trace_dir/'POLYGON_CAPTURE.json'
    assert pin(tp)['sha256']=='2eeb762d3f212856fc5fbd7e46302de115670acc94e81aeb9f0c7091541114f8'
    trace=json.loads(tp.read_bytes());capture=json.loads(cp.read_bytes())
    assert capture['polygons']==trace['after']
    def cell(polygons=None):
        c=gdstk.Cell('LOCAL_DOE006_ONLY')
        for item in polygons or trace['after']:
            c.add(gdstk.Polygon(item['points'],layer=item['layer'],datatype=item['datatype']))
        for item in capture['labels']:
            c.add(gdstk.Label(item['text'],item['origin'],layer=item['layer'],texttype=item['texttype']))
        return c
    original=cell()
    try:endpoint.construct_shared_port_edges(cell=original,ports=capture['ports'],require_port_labels=True)
    except ValueError as e:original_error=str(e)
    else:raise AssertionError('original failure not reproduced')
    assert original_error=='P004: expected one full-width terminal face, got 0'
    c=cell();before_bytes=[poly.points.tobytes() for poly in c.polygons]
    rebound, binding=bind_cross_centers(cell=c,ports=capture['ports'],before=trace['before'],after=trace['after'],endpoint=endpoint)
    assert before_bytes==[poly.points.tobytes() for poly in c.polygons]
    construction=endpoint.construct_shared_port_edges(cell=c,ports=rebound,require_port_labels=True)
    fixtures=[]
    def reject(name, fn):
        try:fn()
        except ValueError as e:fixtures.append(dict(name=name,status='EXPECTED_REJECTION',error=str(e)))
        else:raise AssertionError('guard failed: '+name)
    def invoke(c, before, after):
        return bind_cross_centers(cell=c,ports=capture['ports'],before=before,after=after,endpoint=endpoint)
    bad=deepcopy(trace['before']);bad[2]['points'][12][0]+=.01
    reject('missing_exact_raw_face', lambda:invoke(cell(),bad,trace['after']))
    bad2=deepcopy(trace['after']);bad2[2]['points'][13][1]+=.005
    reject('wrong_actual_width', lambda:invoke(cell(bad2),trace['before'],bad2))
    bad3=deepcopy(trace['after']);bad3[2]['points'][12][0]+=.01;bad3[2]['points'][13][0]+=.01
    reject('outside_original_one_grid_axial_limit', lambda:invoke(cell(bad3),trace['before'],bad3))
    c4=cell()
    for label in c4.labels:
        if label.text=='P004':label.origin=(0,0)
    reject('label_not_in_corresponding_conductor', lambda:invoke(c4,trace['before'],trace['after']))
    report=dict(schema='eucap15.doe006_lineage_draft_replay.v1',status='LOCAL_REPLAY_PASS_NOT_PHYSICAL_VALIDATION',
        original_failure=original_error,binding=binding,construction=construction,guard_fixtures=fixtures,
        new_candidates=0,new_training=0,gds_written=0,cadence_runs=0,calibre_runs=0,emx_runs=0,old_suite_runs=0,
        original_geometry_targets_q_results_changed=False,native_runtime_changed=False,deployment='NOT_INSTALLED',
        limitation='Draft requires exact pre/post-grid polygon lineage and native integration review; DRC/EMX NOT_RUN. Old DOE006 remains failed.',
        source_pins=[pin(__file__),pin(Path(__file__).with_name('lineage_cross_binding_v1.py')),pin(endpoint.__file__)],
        input_pins=[pin(tp),pin(cp)])
    with (a.out/'REPLAY.json').open('x') as f:json.dump(report,f,indent=2,allow_nan=False)
    print(json.dumps(dict(receipt=pin(a.out/'REPLAY.json'),binding=binding,construction=construction,fixtures=fixtures)))


if __name__=='__main__':main()
