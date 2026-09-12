"""New v2 entry checks on six saved captures; no reconstruct/old suite/native."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def pin(p):
    p=Path(p).absolute();b=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))


def main():
    p=argparse.ArgumentParser()
    for name in ('repo','replay','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    subprocess.Popen=lambda *x,**kw: (_ for _ in ()).throw(RuntimeError('NATIVE_FORBIDDEN'))
    sys.path.insert(0,str(a.repo.absolute()))
    import gdstk
    from rfic_transformer_inverse_design.layout import port_endpoint_lineage as v2
    assert pin(a.replay)['sha256']=='7088341440eb8afe73e84150448f3761aa710296917e8b7843b9d8b1a9ccf52b'
    original=json.loads(a.replay.read_bytes());captures=[];rows=[]
    def cell(c):
        cell=gdstk.Cell('NEW_SIX_METADATA_ENTRY')
        for x in c['after']:cell.add(gdstk.Polygon(x['points'],layer=x['layer'],datatype=x['datatype']))
        for x in c['labels']:cell.add(gdstk.Label(x['text'],x['origin'],layer=x['layer'],texttype=x['texttype']))
        return cell
    for old in original['results']:
        p=old['capture'];assert pin(p['path'])==p
        c=json.loads(Path(p['path']).read_bytes());captures.append(c)
        current=cell(c);before=[poly.points.tobytes() for poly in current.polygons]
        labels=[(x.text,x.origin,x.layer,x.texttype) for x in current.labels]
        result=v2.construct_shared_port_edges_with_lineage(cell=current,ports=c['ports'],before=c['before'])
        assert result['lineage_binding']==old['existing_lineage_binding']
        assert result['ports']==old['existing_endpoint_with_lineage']['ports']
        assert before==[poly.points.tobytes() for poly in current.polygons]
        assert labels==[(x.text,x.origin,x.layer,x.texttype) for x in current.labels]
        assert result['changed_port_count']==result['changed_polygon_count']==0
        assert len(result['label_containment_checks'])==8
        rows.append(dict(request_id=c['request_id'],status='V2_LOCAL_ENTRY_PASS',
            rebound=result['lineage_binding']['rebound_ports'],all8_original_label_guards=True,
            polygon_coordinates_changed=False,labels_changed=False,original_failure_remains=True))
    first=captures[0];binding=rows[0]['rebound'][0];pi,ei=binding['polygon_index'],binding['edge_index']
    negatives=[]
    def reject(name,c,before,expected):
        try:v2.construct_shared_port_edges_with_lineage(cell=c,ports=first['ports'],before=before)
        except ValueError as exc:
            assert expected in str(exc),(name,str(exc))
            negatives.append(dict(fixture=name,status='EXPECTED_REJECTION',error=str(exc)))
        else:raise AssertionError('guard relaxed: '+name)
    bad=deepcopy(first['before']);bad[pi]['points'][ei][0]+=.01
    reject('new_DOE027_missing_exact_raw_face',cell(first),bad,'unique exact pre-grid lineage unavailable')
    bad=deepcopy(first)
    # Change full width without breaking adjacent axial runs.
    count=len(bad['after'][pi]['points'])
    for index in ((ei+1)%count,(ei+2)%count):bad['after'][pi]['points'][index][1]-=.005
    reject('new_DOE027_wrong_width_same_axial_adjacency',cell(bad),first['before'],'not exact intended width')
    badcell=cell(first)
    for label in badcell.labels:
        if label.text==binding['port_id']:label.origin=(1_000_000.,1_000_000.)
    reject('new_DOE027_label_outside_own_conductor',badcell,first['before'],'label outside lineage conductor')
    report=dict(schema='eucap15_terminal_lineage_v2_new_entry_checks.v1',
        status='SIX_NEW_LOCAL_CASES_AND_THREE_GUARDS_PASS_NOT_PHYSICAL',cases=rows,negative_fixtures=negatives,
        sources=[pin(__file__),pin(v2.__file__)],input=pin(a.replay),
        native_actions=0,gds_written=0,cadence_runs=0,calibre_runs=0,emx_runs=0,
        candidate_reconstruction_reruns=0,old_cases_or_suites_rerun=0,
        deployment='NOT_INSTALLED',original_failed_outcomes_replaced=0)
    with (a.out/'CHECK.json').open('x') as f:json.dump(report,f,indent=2,allow_nan=False)
    print(json.dumps(dict(receipt=pin(a.out/'CHECK.json'),cases=len(rows),negative_guards=len(negatives))))


if __name__=='__main__':main()
