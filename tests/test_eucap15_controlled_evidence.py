"""Controlled64 full-feature-chain synthetic tests, not real native acceptance.

Only this file is collected. Reused fixture functions do not execute old tests.
No owner RESULT, export closure or native birth is invented; success explicitly
does not establish terminal publication, equal solver-start budget or admission.
"""
from copy import deepcopy
import math
from pathlib import Path
import socket
import subprocess

import pytest

from research.broadband56_nn import eucap15_controlled_evidence as m
from tests.fixtures.eucap15_controlled_evidence_fixture import ControlledEvidence, csv_bytes, pin


@pytest.fixture(autouse=True)
def forbid_native_model_network(monkeypatch):
    import torch
    def forbidden(*args,**kwargs):
        raise AssertionError('Synthetic tests forbid native/network/model execution')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(torch,'load',forbidden)
    monkeypatch.setattr(torch.jit,'load',forbidden)


@pytest.fixture
def chain(tmp_path,monkeypatch):
    return ControlledEvidence(tmp_path,monkeypatch)


def fingerprint(root):
    return {str(p.relative_to(root)):(p.read_bytes(),p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('source',['SPARSE_TARGETED','GEOMETRY_DOE','EXPLORATION'])
def test_each_source_complete_chain_no_result_no_birth_no_authorization(tmp_path,monkeypatch,source):
    c=ControlledEvidence(tmp_path,monkeypatch,source=source,actual=[1.,1.,13.,.5])
    reader=c.mirror(); before=fingerprint(c.root); batch=deepcopy(c.batch)
    def no_write(*args,**kwargs): raise AssertionError('Read-only inspector wrote a file')
    monkeypatch.setattr(Path,'write_bytes',no_write)
    monkeypatch.setattr(Path,'write_text',no_write)
    result=c.inspect(reader)
    assert fingerprint(c.root)==before and c.batch==batch
    assert len(c.batch['rows'])==64 and c.read('feature')['original_proposal_denominator']==64
    assert result['strict_valid'] is True and result['actual']==[1.,1.,13.,.5]
    assert result['source']==source and result['candidate_id']==c.candidate_id
    assert result['request_id']==c.request_id and result['geometry_sha256']==c.geometry_sha
    assert result['parameter_geometry_hash']==c.original['parameter_geometry_hash']
    assert result['feature']==c.p('feature') and result['solver']==c.p('solver')
    assert result['preflight']==c.p('proof') and result['s4p']==c.p('s4p')
    assert result['source_validation']=='PINNED_CANDIDATE_CHAIN_AND_ORIGINAL56_RECONCILED'
    assert result['evidence_class']=='FRESH_REAL_EMX'  # Synthetic assertion of schema only.
    assert result['terminal_publication_verified'] is False
    assert result['solver_start_verified'] is False and result['production_admission'] is False
    assert not list(c.root.rglob('RESULT.json')) and not list(c.root.rglob('RUNNING.json'))
    targeted=source=='SPARSE_TARGETED'
    assert result['q_proxy']==(13 if targeted else None)
    assert result['target_errors_defined'] is targeted
    assert result['strict_joint_hit'] is (True if targeted else None)
    if not targeted:
        assert result['emx_minus_target'] is result['emx_minus_proxy'] is None
        assert c.read('feature')['target'] is c.read('feature')['proxy_self'] is None
        assert c.read('feature')['candidate_model_id'] is None
    assert c.read('feature')['model_used_for_proposal'] is targeted


@pytest.mark.parametrize('mapped',[False,True])
def test_exact_original_and_native_manifest_paths_same_bytes(tmp_path,monkeypatch,mapped):
    c=ControlledEvidence(tmp_path,monkeypatch,mapped_sources=mapped)
    reader=c.mirror(); c.inspect(reader)
    feature=c.read('feature')
    assert feature['original_controlled_manifest']==c.batch['manifest']
    assert feature['controlled_manifest']==c.sources['manifest']
    assert (feature['controlled_manifest']['path']!=c.batch['manifest']['path']) is mapped
    for name in ('manifest','intent','proposals','preparation'):
        p=c.sources[name]
        assert p['sha256']==c.batch[name]['sha256'] and p['bytes']==c.batch[name]['bytes']
        assert reader.evidence[p['path']]['original']==p


@pytest.mark.parametrize('kwargs',[
    dict(below_half_srf=False),dict(physics=False),dict(actual=[1.,1.,math.nan,.5])])
def test_saved_invalid_labels_and_srf_never_promoted(tmp_path,monkeypatch,kwargs):
    c=ControlledEvidence(tmp_path,monkeypatch,**kwargs)
    result=c.inspect()
    assert result['strict_valid'] is False and result['core_eligible'] is False
    assert result['strict_joint_hit'] is False and result['actual']==c.read('feature')['actual_fresh_emx']
    assert result['s4p']==c.p('s4p')


def test_strict_response_miss_is_not_failed_extraction(chain):
    result=chain.inspect()
    assert result['strict_valid'] is True and result['strict_joint_hit'] is False
    assert result['actual']==[1.,1.5,13.,.5]


@pytest.mark.parametrize('key,field,value',[
    ('feature','schema','eucap15_acquisition_fresh_features.v1'),
    ('feature','original_proposal_denominator',256),
    ('feature','dataset_scope','DEVELOPMENT_ACQUISITION_PAIR'),
    ('feature','physical_selection','FROZEN_ACQUISITION_SINGLE'),
    ('feature','model_id','FOREIGN_MODEL'),('feature','candidate_model_id',None),
    ('feature','model_used_for_proposal',False),('feature','q_proxy',14),
    ('feature','q_requested',14),('feature','q_emx',20),
    ('feature','production_membership',True),
    ('proof','original_request_denominator',128),('proof','request_id','FOREIGN_REQUEST'),
    ('proof','port_permutation',[0,1,2,3]),('proof','reference_ohm',75),
    ('request','schema','eucap15_acquisition_emx_request.v1'),
    ('request','arm_order',99),('request','production_campaign_membership',True),
    ('audit','schema','eucap15_acquisition_gds_audit.v1'),('audit','N_logical',11),
    ('solver','status','FAIL'),('solver','real_emx',False),('solver','production_modified',True),
])
def test_reclosed_scope_identity_model_and_original_q_drift(chain,key,field,value):
    chain.edit(key,lambda x:x.__setitem__(field,value))
    with pytest.raises(ValueError): chain.inspect()


@pytest.mark.parametrize('kind',['unknown_candidate','wrong_geometry','wrong_arm','held','canonical_drift','field_order'])
def test_frozen_candidate_context_and_entry_rejections(chain,kind):
    entry=chain.entry(); batch=deepcopy(chain.batch); original=batch['rows'][chain.candidate_id]
    if kind=='unknown_candidate':entry['candidate_id']='FOREIGN_CANDIDATE'
    elif kind=='wrong_geometry':entry['geometry_sha256']='0'*64
    elif kind=='wrong_arm':entry['arm']='OTHER_ARM'
    elif kind=='held':original['local_dispatch_eligible']=False
    elif kind=='canonical_drift':original['geometry'][0]+=.005
    else:original['geometry_fields'].reverse()
    with pytest.raises(ValueError): chain.inspect(entry=entry,batch=batch)


@pytest.mark.parametrize('kind',['original_manifest','resolved_manifest','mapping_other_bytes','missing_mirror','preparation_omitted'])
def test_original_resolved_source_identity_and_preparation_closure(chain,kind):
    reader=None
    if kind=='original_manifest':
        chain.edit('request',lambda x:x.__setitem__('controlled_manifest',chain.sources['manifest']))
    elif kind=='resolved_manifest':
        chain.edit('feature',lambda x:x.__setitem__('controlled_manifest',chain.batch['manifest']))
    elif kind=='mapping_other_bytes':
        chain.edit('request',lambda x:x['path_map'].__setitem__(chain.batch['manifest']['path'],chain.sources['intent']['path']))
    elif kind=='missing_mirror':
        reader=chain.mirror();reader.paths.pop(chain.sources['preparation']['path'])
    else:
        chain.edit('proof',lambda x:x.__setitem__('source_pins',[p for p in x['source_pins'] if p!=chain.sources['preparation']]))
    with pytest.raises(ValueError): chain.inspect(reader=reader)


@pytest.mark.parametrize('source,field,value',[
    ('GEOMETRY_DOE','target',[1.,1.,13.,.5]),('EXPLORATION','proxy_self',[1.,1.,13.,.5]),
    ('GEOMETRY_DOE','strict_joint_hit',True),('EXPLORATION','candidate_model_id','SYNTHETIC_MODEL_NOT_FINAL')])
def test_untargeted_cannot_acquire_objective_or_model_claim(tmp_path,monkeypatch,source,field,value):
    c=ControlledEvidence(tmp_path,monkeypatch,source=source)
    c.edit('feature',lambda x:x.__setitem__(field,value))
    with pytest.raises(ValueError): c.inspect()


@pytest.mark.parametrize('kind',['short56','wrong_frequency','duplicate_columns','qmin','signed_k','srf','error','score'])
def test_actual_original56_and_saved_metric_checks_are_not_mocked(chain,kind):
    rows=deepcopy(chain.rows);fields=None
    if kind=='short56':rows.pop()
    elif kind=='wrong_frequency':rows[0]['frequency_hz']=6*10**9
    elif kind=='duplicate_columns':fields=[*rows[0],'lp_nh']
    elif kind in ('qmin','signed_k','srf'):
        k={'qmin':'qmin','signed_k':'signed_k','srf':'below_half_srf'}[kind]
        rows[10][k]=False if kind=='srf' else rows[10][k]+.5
        # Reclose the original row as well, so the actual semantic predicate,
        # not merely a stale original_frequency_row or file pin, must reject.
        chain.edit('feature',lambda x:x.__setitem__('original_frequency_row',deepcopy(rows[10])))
    elif kind=='error':
        chain.edit('feature',lambda x:x['emx_minus_target'].__setitem__(0,10.))
    else:chain.edit('feature',lambda x:x.__setitem__('normalized_response_score',1.))
    if kind not in ('error','score'):
        chain.blob('csv',chain.paths['csv'],csv_bytes(rows,fields=fields));chain.repin_after('csv')
    with pytest.raises(ValueError):chain.inspect()


@pytest.mark.parametrize('kind',['geometry_check','drc_check','blocking','scope','geometry_sha','normalized','deck','index_duplicate','index_sha'])
def test_same_gds_geometry_calibre_and_index_semantics(chain,kind):
    if kind=='geometry_check':chain.edit('geometry',lambda x:x['checks'].pop('topology_pass'))
    elif kind=='drc_check':chain.edit('drc',lambda x:x['checks'].__setitem__('foundry_drc_pass',False))
    elif kind=='blocking':chain.edit('drc',lambda x:x.__setitem__('blocking_drc_violation_count',1))
    elif kind=='scope':chain.edit('drc',lambda x:x.__setitem__('drc_scope','FULL_CHIP'))
    elif kind=='geometry_sha':chain.edit('geometry',lambda x:x.__setitem__('candidate_geometry_identity_sha256','0'*64))
    elif kind=='normalized':chain.edit('geometry',lambda x:x.__setitem__('gds_timestamp_normalized_sha256','0'*64))
    elif kind=='deck':chain.edit('drc',lambda x:x.__setitem__('drc_source_rule_deck_sha256','0'*64))
    else:
        if kind=='index_duplicate':chain.index_rows.append(deepcopy(chain.index_rows[0]))
        else:chain.index_rows[0]['drc_summary_sha256']='0'*64
        chain.write_index();chain.repin_after('index')
    with pytest.raises(ValueError):chain.inspect()


@pytest.mark.parametrize('kind',['gds_after','s4p_pin','artifact_outside','command_changed','command_gds','command_sweep','feature_manifest'])
def test_solver_same_gds_s4p_command_and_completion_closure(chain,kind):
    if kind=='gds_after':chain.edit('solver',lambda x:x['source_gds_after'].__setitem__('sha256','0'*64))
    elif kind=='s4p_pin':
        entry=chain.entry();entry['s4p']=chain.p('log')
        with pytest.raises(ValueError):chain.inspect(entry=entry)
        return
    elif kind=='artifact_outside':chain.edit('solver',lambda x:x['artifacts'].append(chain.p('config')))
    elif kind=='command_changed':chain.edit('command',lambda x:x.append('--different-command'))
    elif kind in ('command_gds','command_sweep'):
        command=deepcopy(chain.command)
        if kind=='command_gds':command[1]='/SYNTHETIC_FOREIGN/another.gds'
        else:command[command.index('--sweep')+2]='59000000000'
        chain.put('command',chain.paths['command'],command);chain.repin_after('command')
        chain.edit('proof',lambda x:x.__setitem__('command',command))
    else:chain.edit('manifest',lambda x:x.__setitem__('inputs_unchanged',False))
    with pytest.raises(ValueError):chain.inspect()


def test_mirror_bytes_tampered_without_repin_and_no_original_fallback(chain):
    reader=chain.mirror(); local=Path(reader.paths[chain.p('s4p')['path']])
    local.write_bytes(b'TAMPERED SYNTHETIC MIRROR')
    with pytest.raises(ValueError,match='SHA/size'):chain.inspect(reader=reader)


def test_absent_feature_is_not_promoted_to_pending_or_completed(chain):
    reader=chain.mirror();reader.paths.pop(chain.p('feature')['path'])
    with pytest.raises(ValueError,match='Missing exact mirror'):chain.inspect(reader=reader)


@pytest.mark.parametrize('kind',['missing_wrapper','missing_process','missing_runtime_source',
    'missing_required_module','wrong_repo','duplicate_runtime_path','command_wrapper','command_process',
    'touchstone_output','duplicate_output'])
def test_request_runtime_closed_sources_and_actual_command_positions(chain,kind):
    if kind in ('missing_wrapper','missing_process','missing_runtime_source'):
        key={'missing_wrapper':'emx_wrapper','missing_process':'process','missing_runtime_source':'runtime_0'}[kind]
        chain.edit('proof',lambda x:x.__setitem__('source_pins',[p for p in x['source_pins'] if p!=chain.p(key)]))
    elif kind=='missing_required_module':
        chain.edit('request',lambda x:x['runtime']['source_pins'].pop())
    elif kind=='wrong_repo':
        chain.edit('request',lambda x:x['runtime'].__setitem__('repo',str(chain.native/'OTHER_RUNTIME')))
    elif kind=='duplicate_runtime_path':
        chain.edit('request',lambda x:x['runtime']['source_pins'].append(deepcopy(x['runtime']['source_pins'][0])))
    else:
        command=deepcopy(chain.command)
        if kind=='touchstone_output':command[command.index('-s')+1]='/SYNTHETIC_OTHER/not_the_returned.s4p'
        elif kind=='duplicate_output':command.extend(['-s',chain.p('s4p')['path']])
        else:command[0 if kind=='command_wrapper' else 3]='/SYNTHETIC_OTHER/not_the_pinned_runtime'
        chain.put('command',chain.paths['command'],command);chain.repin_after('command')
        chain.edit('proof',lambda x:x.__setitem__('command',command))
    with pytest.raises(ValueError):chain.inspect()
