"""Synthetic failed-stage evidence only; never native execution or FINAL data.

The dedicated fixture binds a tiny frozen frame, fake source/release bytes and
native-shaped receipts. No test treats a generic pipeline error as a solve.
"""
from copy import deepcopy
from pathlib import Path
import socket
import subprocess

import pytest

from research.broadband56_nn import eucap15_final_failures as m
from tests.fixtures.eucap15_final_failures_fixture import FailureEvidence
from tests.fixtures.eucap15_selected_evidence_fixture import pin


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return FailureEvidence(tmp_path, monkeypatch)


def fingerprints(root):
    return {str(p.relative_to(root)):(p.read_bytes(),p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_bound_failure_is_readonly_preserves_main_audit_and_null_actual(chain,monkeypatch):
    mirror=chain.mirror()
    before=fingerprints(chain.root)
    frozen=deepcopy(chain.ctx)
    def forbidden(*a,**kw):
        pytest.fail('Synthetic reader must never execute native/network/model or write')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    monkeypatch.setattr(Path,'write_text',forbidden)
    result=chain.inspect(mirror)
    assert fingerprints(chain.root)==before and chain.ctx==frozen
    publication=result['publication']
    assert publication['state']=='GDS_FAIL'
    assert publication['candidate_id']==chain.candidate_id
    assert publication['frozen_record_sha256']==chain.binding['frozen_record_sha256']
    assert publication['candidate_geometry_identity_sha256']==chain.geometry_sha
    assert publication['actual'] is None and publication['touchstone_sha'] is None
    assert chain.binding['memberships']==['MAIN','AUDIT']
    assert result['q_emx'] is None and result['native_dispatch_authorized'] is False
    assert len({p['path'] for p in result['evidence_pins']})==len(result['evidence_pins'])
    assert all(x['original']['sha256']==x['resolved']['sha256'] and
               x['original']['path']!=x['resolved']['path'] for x in result['resolution_evidence'])


@pytest.mark.parametrize('key,field,value',[
    ('result','candidate_id','OTHER-q14'),('result','request_id','OTHER'),
    ('result','model_id','OTHER_MODEL'),('result','dataset_scope','FORMAL_10K'),
    ('result','q_requested',15),('result','q_proxy',10),('result','q_emx',14),
    ('result','memberships',['MAIN']),('result','original_request_denominator',64),
    ('result','schema','eucap15_selected_candidate_failure.v1'),
    ('result','status','PASS'),('result','error','UNKNOWN_TERMINAL_REASON'),
    ('binding','candidate_geometry_identity_sha256','0'*64),
    ('binding','frozen_record_sha256','0'*64),
])
def test_reclosed_identity_and_unknown_terminal_rejected(chain,key,field,value):
    chain.edit(key,lambda x:x.__setitem__(field,value))
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['missing','hash','size','symlink'])
def test_unpublished_or_changed_mirror_fails_closed(chain,kind):
    mirror=chain.mirror(); source=chain.p('process'); entry=mirror.entries[source['path']]
    if kind=='missing': del mirror.entries[source['path']]
    elif kind=='hash': Path(entry['resolved']['path']).write_bytes(b'CHANGED PROCESS\n')
    elif kind=='size': entry['resolved']['bytes']+=1
    else:
        link=chain.root/'SYMLINK_PROCESS'; link.symlink_to(entry['resolved']['path'])
        entry['resolved']['path']=str(link)
    with pytest.raises(m.SelectedEvidenceError): chain.inspect(mirror)


def test_wrong_config_rehashed_everywhere_still_not_frozen_owner_config(chain):
    chain.paths['config'].write_bytes(b'SYNTHETIC OTHER CONFIG\n')
    chain.repin_after('config')
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


def test_wrong_release_rehashed_everywhere_still_not_expected_release(chain):
    chain.edit('release',lambda x:x.__setitem__('schema','UNRELATED_RELEASE'))
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('stage,state', [('gds_audit','GDS_FAIL'),('calibre','DRC_FAIL'),
    ('gds_reject','GDS_FAIL'),('drc_reject','DRC_FAIL'),('solver','SOLVER_FAIL'),('feature','FEATURE_FAIL')])
def test_positive_closed_branch_uses_original_stage_proof_not_generic_error(tmp_path,monkeypatch,stage,state):
    c=FailureEvidence(tmp_path,monkeypatch,stage=stage)
    result=c.inspect()
    assert result['publication']['state']==state and result['publication']['actual'] is None
    assert result['native_attempts'] is None and result['native_executions_by_reader']==0
    if stage=='feature': assert result['publication']['touchstone_sha']==c.p('s4p')['sha256']
    else: assert result['publication']['touchstone_sha'] is None
    assert result['native_returncode']==(7 if stage=='solver' else None)
    assert result['q_emx'] is None


@pytest.mark.parametrize('order,q,memberships', [(0,10,['AUDIT']),(2,14,['MAIN'])])
def test_failed_audit_only_q_not_reselected_to_main(tmp_path,monkeypatch,order,q,memberships):
    c=FailureEvidence(tmp_path,monkeypatch,order=order,q=q)
    result=c.inspect()
    assert c.binding['memberships']==memberships
    assert c.read('result')['q_requested']==q and c.read('result')['q_proxy']==14
    assert result['publication']['candidate_id']==c.candidate_id and result['q_emx'] is None


def test_analytic_failed_original_cannot_acquire_native_failure(tmp_path,monkeypatch):
    c=FailureEvidence(tmp_path,monkeypatch,analytic=False)
    with pytest.raises(m.SelectedEvidenceError,match='analytic failure'): c.inspect()


@pytest.mark.parametrize('rc', [0,False,-9,-15])
@pytest.mark.parametrize('stage',['cadence','calibre','solver'])
def test_zero_bool_or_signal_wrapper_returncode_not_physical_failure(tmp_path,monkeypatch,rc,stage):
    c=FailureEvidence(tmp_path,monkeypatch,stage=stage)
    c.edit('process',lambda x:x.__setitem__('returncode',rc))
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


@pytest.mark.parametrize('marker',['RESOURCE_WAIT','NO_DISPATCH','Dispatch budget expired',
    'KeyboardInterrupt','OWNER_STOPPED','LEASE_BUSY','SOURCE_IDENTITY','PARTIAL_OUTPUT'])
def test_resource_interrupt_and_integrity_log_tail_remain_unresolved(chain,marker):
    chain.paths['process_log'].write_text('RuntimeError: '+marker+'\n')
    chain.repin_after('process_log')
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['saved_intent','release','output','completion','command','log','named_log'])
def test_closed_process_intent_command_and_log_are_exact(chain,kind):
    if kind=='saved_intent': chain.edit('process',lambda x:x['intent'].__setitem__('output','/OTHER'))
    elif kind in ('output','completion'): chain.edit('intent',lambda x:x.__setitem__(kind,'/OTHER'))
    elif kind=='release': chain.edit('intent',lambda x:x.__setitem__('release',chain.p('config')))
    elif kind=='command': chain.edit('intent',lambda x:x.__setitem__('command',['OTHER_PROGRAM',str(chain.paths['candidates'])]))
    elif kind=='log': chain.edit('process',lambda x:x.__setitem__('log',chain.p('config')))
    else: chain.edit('result',lambda x:x.__setitem__('error','STAGE_EXECUTION_FAILED: cadence; /OTHER.log'))
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


def test_calibre_loader_cannot_be_replaced_with_arbitrary_program(tmp_path,monkeypatch):
    c=FailureEvidence(tmp_path,monkeypatch,stage='calibre')
    c.edit('intent',lambda x:x.__setitem__('command',['OTHER_PROGRAM',str(c.paths['calibre_request'])]))
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


@pytest.mark.parametrize('kind',['candidate','geometry','duplicate'])
def test_cadence_single_original_geometry_required(chain,kind):
    import csv
    rows=list(csv.DictReader(chain.paths['candidates'].open()))
    if kind=='candidate': rows[0]['candidate_id']='OTHER-q14'
    elif kind=='geometry': rows[0][chain.binding['geometry_fields'][0]]='9.0'
    else: rows.append(deepcopy(rows[0]))
    chain.write_rows('candidates',rows); chain.repin_after('candidates')
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['missing_required','true_status','wrong_failed_list'])
def test_gds_gate_requires_actual_same_candidate_failed_checks(tmp_path,monkeypatch,kind):
    c=FailureEvidence(tmp_path,monkeypatch,stage='gds_reject')
    if kind=='missing_required': c.edit('geometry',lambda x:x['checks'].pop(m.GEOMETRY_CHECKS[1]))
    elif kind=='true_status': c.edit('geometry',lambda x:x['checks'].__setitem__(m.GEOMETRY_CHECKS[0],True))
    else: c.edit('audit',lambda x:x['records'][0].__setitem__('failed_checks',['UNRELATED_CHECK']))
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


@pytest.mark.parametrize('kind',['candidate','wrong_summary','duplicate','wrapper','zero_all_pass','normalized','top_cell'])
def test_drc_gate_original_input_output_and_rejection_are_bound(tmp_path,monkeypatch,kind):
    c=FailureEvidence(tmp_path,monkeypatch,stage='drc_reject')
    if kind in ('candidate','wrong_summary','duplicate'):
        if kind=='candidate': c.index_rows[0]['candidate_id_sha256']='0'*64
        elif kind=='wrong_summary': c.index_rows[0]['drc_summary_sha256']='0'*64
        else: c.index_rows.append(deepcopy(c.index_rows[0]))
        c.write_index(); c.repin_after('index')
    elif kind=='wrapper': c.edit('wrapper',lambda x:x.__setitem__('input_request',c.p('gds_request')))
    elif kind=='zero_all_pass':
        def change(x):
            x['blocking_drc_violation_count']=0; x['checks']={k:True for k in x['checks']}
        c.edit('drc',change)
    else:
        c.calibre_rows[0]['top_cell' if kind=='top_cell' else 'gds_timestamp_normalized_sha256']='OTHER'
        c.write_rows('calibre_input',c.calibre_rows); c.repin_after('calibre_input')
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


@pytest.mark.parametrize('kind',['missing_failure','both','feature_receipt','feature_manifest','solver_success'])
def test_generic_or_contradictory_emx_closure_is_not_classified(tmp_path,monkeypatch,kind):
    c=FailureEvidence(tmp_path,monkeypatch,stage='solver')
    mirror=c.mirror()
    if kind=='missing_failure': del mirror.entries[str(c.paths['subfailure'])]
    else:
        if kind=='both':
            p=c.native/'emx_selected'/'FEATURE_FAILURE.json'
            p.write_bytes(c.paths['subfailure'].read_bytes())
        else: p=c.hidden[{'feature_receipt':'feature','feature_manifest':'manifest','solver_success':'solver'}[kind]]
        mirror.add(pin(p),pin(p))
    with pytest.raises(m.SelectedEvidenceError): c.inspect(mirror)


@pytest.mark.parametrize('kind',['negative','zero','missing_stderr','wrong_path','unknown','wrong_command','tail_mismatch'])
def test_solver_failure_requires_exact_positive_native_exit_log_chain(tmp_path,monkeypatch,kind):
    c=FailureEvidence(tmp_path,monkeypatch,stage='solver')
    if kind=='missing_stderr':
        mirror=c.mirror(); del mirror.entries[str(c.paths['native_stderr'])]
    else:
        if kind=='wrong_command': c.edit('native_command',lambda x:x.append('WRONG'))
        else:
            error=c.read('subfailure')['error']
            if kind=='negative': error=error.replace('code 7','code -9')
            elif kind=='zero': error=error.replace('code 7','code 0')
            elif kind=='wrong_path': error=error.replace(c.p('native_stderr')['path'],'/OTHER/emx_stderr.log')
            elif kind=='unknown': error='RuntimeError: failed before any solver invocation'
            c.edit('subfailure',lambda x:x.__setitem__('error',error))
            if kind!='tail_mismatch':
                c.paths['process_log'].write_text(error+'\n'); c.repin_after('process_log')
            else: c.paths['process_log'].write_text('OTHER FINAL ERROR\n'); c.repin_after('process_log')
        mirror=c.mirror()
    with pytest.raises(m.SelectedEvidenceError): c.inspect(mirror)


@pytest.mark.parametrize('kind',['missing','foreign','wrong_repo','missing_proof'])
def test_right_release_source_copy_does_not_replace_actual_emx_runtime(tmp_path,monkeypatch,kind):
    c=FailureEvidence(tmp_path,monkeypatch,stage='solver')
    if kind=='missing_proof': c.edit('proof',lambda x:x.__setitem__('source_pins',[
        p for p in x['source_pins'] if p['path']!=str(c.paths['simulation'])]))
    else:
        def change(x):
            if kind=='missing': x['runtime']['source_pins'].pop()
            elif kind=='foreign': x['runtime']['source_pins'][0]=c.p('calibre_wrapper')
            else: x['runtime']['repo']=str(c.root/'OTHER_RUNTIME')
        c.edit('request',change)
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


@pytest.mark.parametrize('message',[
    'Broadband56S4pQaError: Touchstone parse failed: SYNTHETIC_BAD_FORMAT',
    'Broadband56S4pQaError: fresh-EMX S4P exact contract failed: SYNTHETIC_GRID',
    'Broadband56S4pQaError: Z-to-S roundtrip failed: SYNTHETIC_ERROR',
    'Broadband56S4pQaError: SRF reactance series is incomplete or non-finite'])
def test_explicit_extractor_whitelist_preserves_s4p_but_no_labels(tmp_path,monkeypatch,message):
    c=FailureEvidence(tmp_path,monkeypatch,stage='feature')
    c.edit('subfailure',lambda x:x.__setitem__('error',message))
    c.paths['process_log'].write_text(message+'\n'); c.repin_after('process_log')
    result=c.inspect()
    assert result['publication']['state']=='FEATURE_FAIL'
    assert result['publication']['actual'] is None and result['publication']['touchstone_sha']==c.p('s4p')['sha256']


@pytest.mark.parametrize('message',['FileNotFoundError: no file','ValueError: parse failed',
    'Broadband56S4pQaError: Touchstone parse failed: ',
    'Broadband56S4pQaError: SOURCE_IDENTITY_CHANGED',
    'Broadband56S4pQaError: unknown implementation exception'])
def test_unrecognized_extraction_error_not_relabelled_as_physical_failure(tmp_path,monkeypatch,message):
    c=FailureEvidence(tmp_path,monkeypatch,stage='feature')
    c.edit('subfailure',lambda x:x.__setitem__('error',message))
    c.paths['process_log'].write_text(message+'\n'); c.repin_after('process_log')
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


@pytest.mark.parametrize('kind',['gds_after','missing_s4p','duplicate_command'])
def test_extractor_failure_still_requires_complete_own_solver_chain(tmp_path,monkeypatch,kind):
    c=FailureEvidence(tmp_path,monkeypatch,stage='feature')
    def change(x):
        if kind=='gds_after': x['source_gds_after']=c.p('s4p')
        elif kind=='missing_s4p': x['artifacts']=[p for p in x['artifacts'] if not p['path'].endswith('.s4p')]
        else: x['artifacts'].append(c.p('command'))
    c.edit('solver',change)
    with pytest.raises(m.SelectedEvidenceError): c.inspect()


def test_published_stage_completion_conflicts_with_generic_failed_process(tmp_path,monkeypatch):
    c=FailureEvidence(tmp_path,monkeypatch,stage='gds_audit')
    mirror=c.mirror(); mirror.add(c.p('audit'),c.p('audit'))
    with pytest.raises(m.SelectedEvidenceError): c.inspect(mirror)


@pytest.mark.parametrize('key',['audit','proof','solver'])
def test_published_downstream_positive_evidence_conflicts_with_cadence_failure(chain,key):
    mirror=chain.mirror(); mirror.add(chain.p(key),chain.p(key))
    with pytest.raises(m.SelectedEvidenceError,match='downstream'): chain.inspect(mirror)


def test_candidate_source_mutation_during_final_publication_fails(chain,monkeypatch):
    mirror=chain.mirror(); original=m._publication
    def mutate(*a,**kw):
        result=original(*a,**kw)
        Path(mirror.entries[str(chain.paths['process_log'])]['resolved']['path']).write_bytes(b'CHANGED AFTER CLASSIFICATION')
        return result
    monkeypatch.setattr(m,'_publication',mutate)
    with pytest.raises(m.SelectedEvidenceError,match='changed'): chain.inspect(mirror)
