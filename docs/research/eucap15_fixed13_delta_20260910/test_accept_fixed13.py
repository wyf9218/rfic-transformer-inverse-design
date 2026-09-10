"""Small synthetic adapter tests; no real fixed13 files or physical-chain calls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('fixed13_adapter',Path(__file__).with_name('accept_fixed13.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def proposal():
    return dict(candidate_id='synthetic-r-q12',request_id='synthetic-r',q_proxy=12,
        canonical_geometry_sha256='1'*64,local_dispatch_eligible=True,analytic_pass=True,
        arm='COVERAGE_DIRECTED',arm_order=1,global_order=1,source='SPARSE_TARGETED',
        target=[1.,1.,12.,.5],proxy=[1.01,1.02,12.1,.51])


def success():
    p=proposal()
    return dict(candidate_id=p['candidate_id'],request_id=p['request_id'],q_proxy=12,
        evidence_status='CANDIDATE_PHYSICAL_CHAIN_BOUND',original_status='FRESH_EMX_EXTRACTED',
        geometry_sha256=p['canonical_geometry_sha256'],production_accepted=False,
        strict_valid_source_flag=False,core15_eligible_source_flag=False,
        original_result={'path':'/synthetic/RESULT.json','sha256':'2'*64,'bytes':1})


def failed():
    p=proposal()
    return dict(candidate_id=p['candidate_id'],request_id=p['request_id'],q_proxy=12,
        evidence_status='FAILURE_EVIDENCE_BOUND',original_status='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION',
        failure_stage='ACTUAL_GDS_AUDIT',original_error='ACTUAL_GDS_AUDIT_REJECTED',
        failed_checks=list(m.FAILED_CHECKS),original_result={'path':'/synthetic/synthetic-r/RESULT.json','sha256':'2'*64,'bytes':1})


def test_success_aliases_preserve_invalid():
    result=m.normalized_entry(success(),proposal())
    assert result['strict_valid'] is False and result['core_eligible'] is False
    assert result['result']==success()['original_result'] and result['arm']=='COVERAGE_DIRECTED'


@pytest.mark.parametrize('key,value',[
    ('candidate_id','other'),('request_id','other'),('q_proxy',13),
    ('geometry_sha256','3'*64),('evidence_status','PENDING'),
    ('strict_valid_source_flag','false'),('production_accepted',True)])
def test_closed_identity_or_unknown_state_rejected(key,value):
    item=success(); item[key]=value
    with pytest.raises(ValueError): m.normalized_entry(item,proposal())


def test_held_does_not_become_native_input():
    p=proposal(); p['local_dispatch_eligible']=False
    with pytest.raises(ValueError): m.normalized_entry(success(),p)


def test_failure_is_distinct_not_zero_or_replacement():
    assert m.normalized_entry(failed(),proposal()) is None
    bad=failed(); bad['feature']={'path':'/false'}
    with pytest.raises(ValueError): m.normalized_entry(bad,proposal())


def test_duplicate_frame_rejected():
    with pytest.raises(ValueError): m.unique([success(),success()],'candidate_id')


def test_exact_external_merge_keeps_new_not_old_physics():
    ext=[{'status':'EXTERNAL_CONTRACT_OR_METADATA_PIN_REFERENCED_NOT_REREAD',
          'source':{'path':'/remote/config','sha256':'a'*64,'bytes':2}}]
    paths,expected=m.merge_external({'/remote/new':'/local/new'},ext,
        {'/remote/config':'/local/config','/remote/oldphysics':'/local/old'},[])
    assert paths=={'/remote/new':'/local/new','/remote/config':'/local/config'}
    assert set(expected)=={'/remote/config'}
    with pytest.raises(ValueError): m.merge_external({'/remote/config':'/other'},ext,{'/remote/config':'/local/config'},[])
    with pytest.raises(ValueError): m.merge_external({},ext,{},[])


def test_supplement_hash_conflict_rejected():
    item={'original':{'path':'/r/a','sha256':'a'*64,'bytes':2},
          'resolved':{'path':'/l/a','sha256':'b'*64,'bytes':2}}
    with pytest.raises(ValueError): m.merge_external({},[],{},[item])


class FakeReader:
    """Metadata-only synthetic reader; does not claim physical authentication."""
    def __init__(self,documents): self.documents=documents; self.reads=[]
    def document(self,p): self.reads.append(p); return self.documents[p['path']]
    def read(self,p): self.reads.append(p); return b'synthetic'


def rejection_fixture():
    p=proposal(); item=failed(); sha=hashlib.sha256(p['candidate_id'].encode()).hexdigest()
    def pin(name): return dict(path='/synthetic/'+name,sha256='a'*64,bytes=1)
    batch={k:pin(k) for k in ('manifest','recipe','proposals')}
    context=dict(request_id=p['request_id'],frequency_ghz=15,q_proxy=12,
        model_id='ACQUISITION_RECIPE_BOUND_NOT_MODEL_INFERRED_HERE',dataset_scope='DEVELOPMENT_ACQUISITION_PAIR',
        target_source=p['source'],arm=p['arm'],arm_order=1,global_order=1)
    ar=pin('synthetic-r/gds_audit/REQUEST_GDS_AUDIT.json'); ga=pin('geometry_audit'); gds=pin('actual.gds'); port=pin('port.json')
    rec=dict(candidate_id=p['candidate_id'],candidate_id_sha256=sha,candidate_geometry_identity_sha256='1'*64,
        analytic_grid=True,status='FAIL',audit_attempted=True,cadence_routed=True,calibre_eligible=False,
        failed_checks=list(m.FAILED_CHECKS),original_record=p,geometry_audit=ga,gds=gds,port_manifest=port)
    config=pin('config'); config['sha256']=m.CONFIG_SHA
    docs={item['original_result']['path']:dict(status=item['original_status'],request_id=p['request_id'],
        candidate_id=p['candidate_id'],q_proxy=12,error=item['original_error']),
        ar['path']:dict(schema='eucap15_acquisition_gds_audit.v1',request=context,N_logical=1,N_audit_attempted=1,
                       source_pins=dict(batch,private_config=config),records=[rec]),
        ga['path']:dict(overall_status='FAIL',candidate_id_sha256=sha,candidate_geometry_identity_sha256='1'*64,
            gds_path=gds['path'],gds_sha256=gds['sha256'],checks={k:False for k in m.FAILED_CHECKS})}
    return FakeReader(docs),item,p,batch,{ar['path']:ar}


def test_failure_row_keeps_original_target_and_null_labels():
    args=rejection_fixture(); row=m.failure_row(*args)
    assert row['state']=='GDS_FAIL' and row['target']==proposal()['target'] and row['q_proxy']==12
    assert all(row[k] is None for k in ('actual','strict_valid','core_eligible','feature','s4p','emx_minus_target'))


def test_failure_audit_other_candidate_rejected():
    args=rejection_fixture(); args[0].documents['/synthetic/geometry_audit']['candidate_id_sha256']='9'*64
    with pytest.raises(ValueError): m.failure_row(*args)


def test_no_clobber_and_failed_attempt_preserved(tmp_path,monkeypatch):
    existing=tmp_path/'existing'; existing.mkdir(); marker=existing/'keep'; marker.write_text('original')
    with pytest.raises(FileExistsError): m.run(existing)
    assert marker.read_text()=='original'
    bad=tmp_path/'bad.json'; bad.write_text('{}\n')
    monkeypatch.setattr(m,'RUNTIME',{}); monkeypatch.setattr(m,'TOP',{'bad':(bad,'0'*64)})
    output=tmp_path/'failed_run'
    with pytest.raises(ValueError,match='Frozen input changed'): m.run(output)
    receipt=json.loads((output/'FAILURE_RECEIPT.json').read_text())
    assert receipt['completed_candidate_ids']==[] and receipt['native_actions']==receipt['model_loads']==0
    with pytest.raises(FileExistsError): m.run(output)
