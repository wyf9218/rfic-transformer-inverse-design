"""Only synthetic incremental128 integration; no model/native/real labels.

The success test passes through the unmocked selected evidence reader. All
physical-looking bytes, including minimal GDS and exact56 CSV, are fixtures.
The previously completed128/64/FINAL test suites are not collected or run.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import socket

import pytest

from research.broadband56_nn import eucap15_development128_results as results
from research.broadband56_nn import eucap15_selected_evidence as evidence
from tests.fixtures.eucap15_development128_incremental_fixture import IncrementalFixture, pin


@pytest.fixture(autouse=True)
def forbid_native_model_or_data(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('synthetic test must not launch a process or load data/model')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    original=Path.open
    def guarded(self,*args,**kwargs):
        if self.suffix in ('.pt','.npz') or self.name.startswith('UNREAD_'):
            raise AssertionError('forbidden model/data read: '+str(self))
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)


def fixture(tmp_path,monkeypatch,*,all_three=True):
    f=IncrementalFixture(tmp_path/'fixture',monkeypatch)
    if all_three:
        f.chain(35,'success');f.chain(36,'solver');f.chain(37,'feature')
    return f


def consume_preview(f):
    if not hasattr(f,'prepared'):f.prepare(allow_unclosed_delta=True)
    mirror=results.Mirror(f.prepared['sources']);ctx=f.context()
    results.validate_export(ctx,f.prepared,mirror,allow_unclosed_delta=True)
    rows,closures=results.consume(ctx,f.prepared,mirror)
    return rows,closures


def test_full128_prepare_validate_consume_actual_three_physical_branches(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    assert results.inspect_features is evidence.inspect_features
    f.prepare(allow_unclosed_delta=True)
    before={p:Path(e['resolved']['path']).read_bytes() for p,e in f.entries.items()}
    rows,closures=consume_preview(f)
    assert len(rows)==128 and len({r['request_id'] for r in rows})==128
    assert Counter(r['state'] for r in rows)==dict(ANALYTIC_FAIL=35,STRICT_VALID=1,
        SOLVER_FAIL=1,FEATURE_FAIL=1,PENDING=90)
    for row,original,item in zip(rows[:35],f.context().rows[:35],f.items[:35]):
        assert all(row[k]==v for k,v in original.items() if k not in ('status_detail','native_result_pin'))
        assert f.read(row['native_result_pin'])==f.analytic_failure(item)
    assert rows[35]['actual']==[1.,1.,10.,.3] and rows[35]['strict_joint_hit'] is True
    assert rows[35]['touchstone_sha']==f.chains[35]['paths']['s4p']['sha256']
    assert rows[36]['touchstone_sha'] is None
    assert rows[37]['touchstone_sha']==f.chains[37]['paths']['s4p']['sha256']
    for i,row in enumerate(rows):
        assert row['q_proxy']==10 and row['q_emx'] is None
        if i!=35:assert row['actual'] is None and row['strict_joint_hit'] is None
    failures=[c['failure'] for c in closures if 'failure' in c]
    assert {c['failed_substage'] for c in failures}=={'solver','extractor'}
    assert all(c['evidence_pins'] and c['resolution_evidence'] for c in failures)
    assert all(c['native_calls']==c['model_inferences']==0 and c['FINAL'] is False for c in failures)
    assert all(Path(f.entries[p]['resolved']['path']).read_bytes()==raw for p,raw in before.items())
    assert not f.native.exists()


def test_strict_invalid_extraction_not_misclassified_as_feature_failure(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,all_three=False);f.chain(35,'success',strict=False)
    rows,_=consume_preview(f)
    assert rows[35]['state']=='EMX_INVALID' and rows[35]['actual']==[1.,1.,10.,.3]
    assert rows[35]['strict_joint_hit'] is False and rows[35]['touchstone_sha']
    assert len(rows)==128


@pytest.mark.parametrize('key,value',[
    ('status','UNKNOWN_TERMINAL'),('candidate_id','SYNTHETIC_FOREIGN'),('q_proxy',11),
    ('candidate_geometry_identity_sha256','a'*64),('model_id','SYNTHETIC_OTHER_MODEL'),
])
def test_closed_result_identity_unknown_never_becomes_pending(tmp_path,monkeypatch,key,value):
    f=fixture(tmp_path,monkeypatch);f.edit_chain(36,'result',lambda d:d.__setitem__(key,value))
    with pytest.raises((ValueError,evidence.SelectedEvidenceError)):
        consume_preview(f)


@pytest.mark.parametrize('index,key',[(35,'csv'),(36,'stderr'),(37,'s4p'),(36,'proof')])
def test_missing_actual_mirror_source_fails_closed(tmp_path,monkeypatch,index,key):
    f=fixture(tmp_path,monkeypatch)
    del f.entries[f.chains[index]['paths'][key]['path']]
    with pytest.raises((ValueError,evidence.SelectedEvidenceError)):
        consume_preview(f)


def test_mutated_mirror_bytes_fail_hash_without_reextract(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    p=f.chains[35]['paths']['csv'];Path(f.entries[p['path']]['resolved']['path']).write_bytes(b'SYNTHETIC TAMPER')
    with pytest.raises((ValueError,evidence.SelectedEvidenceError)):
        consume_preview(f)


@pytest.mark.parametrize('attack',[
    'resource','negative_wrapper','negative_native','wrong_command','success_contradiction',
    'feature_manifest_contradiction','missing_runtime_source','missing_proof_runtime',
    'foreign_gds','missing_geometry_check','missing_drc_check','bad_normalized_gds',
])
def test_rehashed_failure_semantic_attacks_rejected(tmp_path,monkeypatch,attack):
    f=fixture(tmp_path,monkeypatch);i=36
    if attack=='resource':
        f.edit_chain(i,'failure',lambda d:d.__setitem__('error','RuntimeError: RESOURCE_WAIT'))
        f.publish_raw(f.items[i]['request_id']+'/emx_0001.log',b'RuntimeError: RESOURCE_WAIT\n')
    elif attack=='negative_wrapper':f.edit_chain(i,'process',lambda d:d.__setitem__('returncode',-15))
    elif attack=='negative_native':
        error=f.read(f.chains[i]['paths']['failure'])['error'].replace('exit code 7','exit code -9')
        f.edit_chain(i,'failure',lambda d:d.__setitem__('error',error))
        log=f.publish_raw(f.items[i]['request_id']+'/emx_0001.log',(error+'\n').encode())
        f.edit_chain(i,'process',lambda d:d.__setitem__('log',log))
    elif attack=='wrong_command':f.edit_chain(i,'intent',lambda d:d['command'].__setitem__(6,'/SYNTHETIC/WRONG_REQUEST'))
    elif attack=='success_contradiction':f.publish(f.items[i]['request_id']+'/emx_selected/SOLVER_RECEIPT.json',{'status':'PASS'})
    elif attack=='feature_manifest_contradiction':f.publish(f.items[i]['request_id']+'/emx_selected/features/MANIFEST.json',{'artifacts':[]})
    elif attack=='missing_runtime_source':f.edit_chain(i,'request',lambda d:d['runtime']['source_pins'].pop())
    elif attack=='missing_proof_runtime':
        path=f.runtime['source_pins'][0]['path']
        f.edit_chain(i,'proof',lambda d:d.__setitem__('source_pins',[p for p in d['source_pins'] if p['path']!=path]))
    elif attack=='foreign_gds':f.edit_chain(i,'proof',lambda d:d.__setitem__('gds',f.chains[35]['paths']['gds']))
    elif attack=='missing_geometry_check':f.edit_chain(i,'geometry',lambda d:d['checks'].pop(next(iter(d['checks']))))
    elif attack=='missing_drc_check':f.edit_chain(i,'drc',lambda d:d['checks'].pop('foundry_drc_pass'))
    else:f.edit_chain(i,'drc',lambda d:d.__setitem__('gds_timestamp_normalized_sha256','b'*64))
    with pytest.raises((ValueError,evidence.SelectedEvidenceError)):
        consume_preview(f)


@pytest.mark.parametrize('error',[
    'FileNotFoundError: missing output',
    'Broadband56S4pQaError: Touchstone parse failed: FileNotFoundError: missing file',
    'RuntimeError: FAILED_FEATURES',
])
def test_feature_unknown_io_partial_never_promoted(tmp_path,monkeypatch,error):
    f=fixture(tmp_path,monkeypatch);i=37
    f.edit_chain(i,'failure',lambda d:d.__setitem__('error',error))
    log=f.publish_raw(f.items[i]['request_id']+'/emx_0001.log',(error+'\n').encode())
    f.edit_chain(i,'process',lambda d:d.__setitem__('log',log))
    with pytest.raises((ValueError,evidence.SelectedEvidenceError)):
        consume_preview(f)


@pytest.mark.parametrize('attack',['duplicate','missing','changed_q','count'])
def test_fixed128_publication_cannot_hide_or_replace_rows(tmp_path,monkeypatch,attack):
    f=fixture(tmp_path,monkeypatch);f.prepare(allow_unclosed_delta=True)
    snapshot=deepcopy(f.prepared)
    if attack=='duplicate':snapshot['requests'][36]=deepcopy(snapshot['requests'][35])
    elif attack=='missing':snapshot['requests'].pop()
    elif attack=='changed_q':
        # Direct fixed identity mutation: change the byte pin as well to test semantics.
        owner=json.loads(Path(f.export_pin['path']).read_text());p=owner['immutable_snapshot']
        local=f.export_root/'FIXED_SNAPSHOT_MANIFEST.json';value=json.loads(local.read_text())
        value['rows'][36]['q_proxy']=11;local.write_text(json.dumps(value))
        updated=dict(pin(local),path=p['path']);owner['immutable_snapshot']=updated
        Path(f.export_pin['path']).write_text(json.dumps(owner))
        snapshot['owner_export']=pin(f.export_pin['path'])
        for e in snapshot['sources']:
            if e['original']['path']==p['path']:e.update(original=updated,resolved=pin(local))
            elif e['original']['path']==f.export_pin['path']:e.update(original=snapshot['owner_export'],resolved=snapshot['owner_export'])
    else:snapshot['N_original_requests']=127
    with pytest.raises((ValueError,evidence.SelectedEvidenceError)):
        mirror=results.Mirror(snapshot['sources']);ctx=f.context()
        results.validate_export(ctx,snapshot,mirror,allow_unclosed_delta=True)
        results.consume(ctx,snapshot,mirror)


def delta_fixture(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,all_three=False)
    previous=f.write_export()
    f.chain(35,'success');f.chain(36,'solver');f.chain(37,'feature')
    exporter,request=f.delta_request(previous,[35,36,37])
    return f,exporter,request


def test_actual_owner_export_delta_default_snapshot_validate_consume_build(tmp_path,monkeypatch):
    f,exporter,request=delta_fixture(tmp_path,monkeypatch)
    sources_before={p:Path(e['resolved']['path']).read_bytes() for p,e in f.entries.items()}
    out=tmp_path/'delta_export'
    closing_pin=exporter.export_delta(request,out)
    closed=json.loads(Path(closing_pin['path']).read_text())
    assert closed['new_results_copied']==3 and closed['old_results_recopied']==0
    assert closed['new_terminal_ids']==[f.items[i]['request_id'] for i in (35,36,37)]
    assert len(list((out/'candidates').rglob('RESULT.json')))==3
    snapshot_pin=results.prepare_snapshot(pin(out/'EXPORT_RECEIPT.json'),tmp_path/'EXTERNAL_SNAPSHOT.json')
    snapshot=json.loads(Path(snapshot_pin['path']).read_text())
    assert snapshot['closed_delta']==closing_pin
    results.validate_export(f.context(),snapshot,results.Mirror(snapshot['sources']))
    rows,closures=results.consume(f.context(),snapshot,results.Mirror(snapshot['sources']))
    assert len(rows)==128 and len(closures)==3
    delivery=tmp_path/'statistics'
    summary=results.build(f.manifest_pin,f.qa_pin,snapshot_pin,delivery)
    assert summary['N_original_requests']==128 and summary['N_terminal_requests']==38
    assert summary['N_pending_requests']==90 and summary['N_analytic_fail']==35
    assert summary['N_strict_valid']==summary['N_solver_fail']==summary['N_feature_fail']==1
    assert summary['N_joint_hit']==1 and summary['observed_joint_hit_fraction_original']==1/128
    assert summary['conditional_valid_joint_hit_fraction']==1
    assert summary['completed_joint_hit_fraction_original'] is None
    assert summary['FINAL'] is False and summary['model_loads']==summary['native_calls']==0
    assert (delivery/'REQUEST_RESULTS.csv').read_text().count('\n')==129
    assert (delivery/'PHYSICAL_METRICS.csv').read_text().count('\n')==9
    assert (delivery/'ERROR_ECDF.csv').read_text().count('\n')==9
    for p,raw in sources_before.items():
        assert Path(f.entries[p]['resolved']['path']).read_bytes()==raw
    assert not f.native.exists()
    before={p:p.read_bytes() for p in delivery.iterdir() if p.is_file()}
    with pytest.raises(FileExistsError):results.build(f.manifest_pin,f.qa_pin,snapshot_pin,delivery)
    assert before=={p:p.read_bytes() for p in delivery.iterdir() if p.is_file()}


@pytest.mark.parametrize('attack',['preview_build','missing_close','wrong_close_snapshot','altered_external'])
def test_unclosed_or_substituted_delta_cannot_publish_statistics(tmp_path,monkeypatch,attack):
    f,exporter,request=delta_fixture(tmp_path,monkeypatch);out=tmp_path/'delta_export'
    exporter.export_delta(request,out)
    if attack=='preview_build':
        snapshot=pin(out/'READER_SNAPSHOT.json')
        failed=tmp_path/'failed_statistics'
        with pytest.raises(ValueError,match='closed delta receipt required'):
            results.build(f.manifest_pin,f.qa_pin,snapshot,failed)
        assert json.loads((failed/'FAILURE_RECEIPT.json').read_text())['status']=='NO_GO_PRESERVED'
        assert not (failed/'SUMMARY.json').exists()
    elif attack=='missing_close':
        # Private fixture only: absence cannot be interpreted as final export.
        (out/'CLOSED_EXPORT_RECEIPT.json').rename(out/'PRESERVED_CLOSURE_NOT_AT_REQUIRED_PATH.json')
        with pytest.raises((ValueError,FileNotFoundError)):
            results.prepare_snapshot(pin(out/'EXPORT_RECEIPT.json'),tmp_path/'not_published.json')
        assert not (tmp_path/'not_published.json').exists()
    elif attack=='wrong_close_snapshot':
        closing=out/'CLOSED_EXPORT_RECEIPT.json';value=json.loads(closing.read_text())
        value['snapshot']['sha256']='c'*64;closing.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            results.prepare_snapshot(pin(out/'EXPORT_RECEIPT.json'),tmp_path/'not_published.json')
    else:
        snapshot_pin=results.prepare_snapshot(pin(out/'EXPORT_RECEIPT.json'),tmp_path/'external.json')
        value=json.loads(Path(snapshot_pin['path']).read_text());value['requests'][35]['result']=None
        with pytest.raises(ValueError,match='differs from the closed preview'):
            results.validate_export(f.context(),value,results.Mirror(value['sources']))


def test_actual_exporter_preserves_unknown_failure_and_forbids_same_output_retry(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,all_three=False);previous=f.write_export()
    f.chain(36,'solver');f.edit_chain(36,'result',lambda d:d.__setitem__('status','UNKNOWN_TERMINAL'))
    exporter,request=f.delta_request(previous,[36]);out=tmp_path/'rejected_export'
    with pytest.raises(ValueError):exporter.export_delta(request,out)
    failure=json.loads((out/'FAILURE_RECEIPT.json').read_text())
    assert failure['status']=='NO_GO_PRESERVED'
    assert not (out/'CLOSED_EXPORT_RECEIPT.json').exists()
    before=(out/'FAILURE_RECEIPT.json').read_bytes()
    with pytest.raises(FileExistsError):exporter.export_delta(request,out)
    assert (out/'FAILURE_RECEIPT.json').read_bytes()==before
