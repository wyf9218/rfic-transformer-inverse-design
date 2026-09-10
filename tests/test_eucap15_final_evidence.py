"""New FINAL evidence integration tests; SYNTHETIC bytes, never native EMX.

Only this module is collected. Reused fixture classes do not execute old tests.
Count patches (35/3) and fake-deck SHA are explicit test-only boundaries.
"""
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import socket

import pytest

from research.broadband56_nn import eucap15_final_evidence as m
from research.broadband56_nn.eucap15_final_statistics import summarize_frame
from research.broadband56_nn.frequency_research_emx import GEOMETRY_CHECKS
from tests.fixtures.eucap15_final_evidence_fixture import FinalEvidence, pin


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return FinalEvidence(tmp_path, monkeypatch)


def fingerprints(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_shared_main_audit_one_publication_exact_sources_and_no_writes_or_execution(chain, monkeypatch):
    mirror = chain.mirror()
    before = fingerprints(chain.root)
    context_before = deepcopy(chain.ctx)
    def forbidden(*a, **kw):
        pytest.fail('No native, network, model or file-write action permitted')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(Path, 'write_bytes', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    result = chain.inspect(mirror)
    assert fingerprints(chain.root) == before and chain.ctx == context_before
    assert result['publication']['state'] == 'STRICT_VALID' and result['strict_joint_hit'] is True
    assert result['publication']['candidate_id'] == chain.candidate_id
    assert result['publication']['actual'] == chain.wanted
    assert result['publication']['frozen_record_sha256'] == chain.binding['frozen_record_sha256']
    assert result['publication']['candidate_geometry_identity_sha256'] == chain.geometry_sha
    assert result['publication']['touchstone_sha'] == chain.p('s4p')['sha256']
    assert result['q_emx'] is None and result['native_executions_by_reader'] == 0
    assert result['native_dispatch_authorized'] is False and result['final_model_selection_qa'] is False
    originals = {p['path']:p for p in result['evidence_pins']}
    assert len(originals) == len(result['evidence_pins']) == len(result['resolution_evidence'])
    assert all(originals[x['original']['path']] == x['original']
               and x['original']['path'] != x['resolved']['path']
               and x['original']['sha256'] == x['resolved']['sha256']
               for x in result['resolution_evidence'])
    assert all(p['path'] in originals for p in chain.binding['source_pins'].values())
    report = summarize_frame(chain.ctx['bundles'], [result['publication']],
        context=chain.ctx['context'], score_spans=chain.scale, tolerances=chain.tau)
    assert report['summary']['N_published_candidates'] == 1
    assert report['summary']['N_original_requests'] == 35
    assert report['summary']['N_strict_valid'] == 1
    assert chain.binding['memberships'] == ['MAIN', 'AUDIT']


@pytest.mark.parametrize('order,q,memberships', [(0,10,['AUDIT']), (2,14,['MAIN']), (34,20,['AUDIT'])])
def test_audit_q_uses_own_target_not_preselected_q(tmp_path, monkeypatch, order, q, memberships):
    c = FinalEvidence(tmp_path, monkeypatch, order=order, q=q)
    result = c.inspect()
    assert c.binding['memberships'] == memberships
    assert c.read('feature')['q_requested'] == q and c.read('feature')['q_proxy'] == 14
    assert result['publication']['actual'][2] == q
    assert result['q_emx'] is None


@pytest.mark.parametrize('kwargs', [dict(below_half_srf=False), dict(physics=False),
    dict(actual=[math.nan,1.,14.,.4]), dict(actual=[.7,math.inf,14.,.4]),
    dict(actual=[.7,1.,-math.inf,.4])])
def test_original56_invalid_results_preserved_not_zeroed_or_promoted(tmp_path, monkeypatch, kwargs):
    c = FinalEvidence(tmp_path, monkeypatch, **kwargs)
    result = c.inspect()
    assert result['publication']['state'] == 'EMX_INVALID' and result['strict_joint_hit'] is False
    assert result['publication']['actual'] == c.read('feature')['actual_fresh_emx']
    assert result['publication']['reason_code'] == 'ORIGINAL56_STRICT_OR_PHYSICS_INVALID'
    assert result['publication']['touchstone_sha'] == c.p('s4p')['sha256']
    assert c.p('csv') in result['evidence_pins'] and len(c.rows) == 56


def test_finite_strict_response_miss_is_not_invalid_extraction(tmp_path, monkeypatch):
    c = FinalEvidence(tmp_path, monkeypatch, actual=[.7,1.,14.,.45])
    result = c.inspect()
    assert result['publication']['state'] == 'STRICT_VALID' and result['strict_joint_hit'] is False


def test_analytic_fail_cannot_acquire_completed_evidence(tmp_path, monkeypatch):
    c = FinalEvidence(tmp_path, monkeypatch, analytic=False)
    with pytest.raises(m.SelectedEvidenceError, match='analytic failure'):
        c.inspect()


@pytest.mark.parametrize('field,value', [('candidate_geometry_identity_sha256','0'*64),
    ('frozen_record_sha256','0'*64), ('memberships',['MAIN']), ('q_target',15),
    ('model_id','OTHER_MODEL'), ('original_request_denominator',34)])
def test_rehashed_binding_cannot_change_original_canonical_identity(chain, field, value):
    chain.edit('binding', lambda x:x.__setitem__(field,value))
    with pytest.raises(m.SelectedEvidenceError, match='binding'):
        chain.inspect()


@pytest.mark.parametrize('key,field,value', [
    ('feature','q_requested',15), ('feature','q_proxy',13), ('feature','q_emx',14),
    ('feature','schema','eucap15_selected_fresh_features.v1'),
    ('feature','physical_selection','Q_PROXY_ONLY'), ('feature','dataset_scope','FORMAL_10K'),
    ('feature','original_request_denominator',64), ('feature','memberships',['AUDIT']),
    ('feature','normalized_response_score',.9), ('feature','strict_joint_hit',False),
    ('feature','emx_minus_target',[1.]*4), ('feature','production_membership',True),
    ('proof','schema','eucap15_selected_emx_preflight.v1'), ('proof','q_requested',10),
    ('proof','port_permutation',[0,1,2,3]), ('proof','reference_ohm',75),
    ('proof','candidate_id_sha256','0'*64), ('proof','geometry_sha256','0'*64),
    ('request','schema','eucap15_selected_emx_request.v1'),
    ('request','q_proxy',10), ('request','production_campaign_membership',True),
    ('solver','real_emx',False), ('solver','status','FAIL'), ('solver','q_emx',14),
    ('solver','production_modified',True),
])
def test_reclosed_native_identity_scope_and_numerical_claims_fail(chain,key,field,value):
    chain.edit(key, lambda x:x.__setitem__(field,value))
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


def test_wrong_config_even_when_all_native_references_rehashed_is_rejected(chain):
    chain.paths['config'].write_bytes(b'OTHER_SYNTHETIC_PHYSICS_CONFIG\n')
    chain.repin_after('config')
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('key,field,value', [
    ('geometry','gds_timestamp_normalized_sha256','0'*64),
    ('geometry','candidate_geometry_identity_sha256','0'*64),
    ('geometry','gds_sha256','0'*64), ('geometry','original_artifacts_unchanged',False),
    ('drc','gds_sha256','0'*64), ('drc','geometry_audit_sha256','0'*64),
    ('drc','gds_timestamp_normalized_sha256','0'*64), ('drc','process_token','OTHER'),
    ('drc','gds_top_cell','OTHER'), ('drc','drc_source_rule_deck_sha256','0'*64),
    ('drc','blocking_drc_violation_count',1), ('drc','drc_report_sha256','0'*64),
])
def test_same_gds_foundry_and_original_audit_links_rejected(chain,key,field,value):
    chain.edit(key,lambda x:x.__setitem__(field,value))
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('key', ['geometry','drc'])
@pytest.mark.parametrize('kind', ['missing_required','false','empty'])
def test_required_checks_not_merely_remaining_all_true(chain,key,kind):
    def change(x):
        if kind=='missing_required': x['checks'].pop(GEOMETRY_CHECKS[0])
        elif kind=='false': x['checks'][GEOMETRY_CHECKS[0]]=False
        else: x['checks']={}
    chain.edit(key,change)
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['candidate','summary_path','summary_sha','duplicate','malformed'])
def test_calibre_index_must_select_exact_one_summary(chain,kind):
    if kind=='candidate': chain.index_rows[0]['candidate_id_sha256']='0'*64
    elif kind=='summary_path': chain.index_rows[0]['drc_summary_path']=str(chain.paths['geometry'])
    elif kind=='summary_sha': chain.index_rows[0]['drc_summary_sha256']='0'*64
    elif kind=='duplicate': chain.index_rows.append(deepcopy(chain.index_rows[0]))
    chain.write_index()
    if kind=='malformed': chain.paths['index'].write_bytes(chain.paths['index'].read_bytes()+b',extra,fields,wrong,extra\n')
    chain.repin_after('index')
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['sources_missing','sources_duplicate','geometry_source_missing',
    'original_gds_missing','audit_duplicate','audit_other_candidate','gds_after','command',
    'solver_duplicate','touchstone_missing','command_missing','solver_outside',
    'manifest_duplicate','manifest_missing','inputs_changed'])
def test_required_closure_cannot_be_shortened_or_duplicated(chain,kind):
    if kind in ('sources_missing','sources_duplicate','geometry_source_missing'):
        def change(x):
            if kind=='sources_duplicate': x['source_pins'].append(deepcopy(x['source_pins'][0]))
            else:
                key='geometry' if kind=='geometry_source_missing' else 'config'
                x['source_pins']=[p for p in x['source_pins'] if p['path']!=str(chain.paths[key])]
        chain.edit('proof',change)
    elif kind=='original_gds_missing': chain.edit('geometry',lambda x:x.__setitem__('original_artifacts',[chain.p('ports')]))
    elif kind=='audit_duplicate': chain.edit('audit',lambda x:x['records'].append(deepcopy(x['records'][0])))
    elif kind=='audit_other_candidate': chain.edit('audit',lambda x:x['records'][0].__setitem__('candidate_id','OTHER'))
    elif kind=='gds_after': chain.edit('solver',lambda x:x.__setitem__('source_gds_after',chain.p('s4p')))
    elif kind=='command':
        chain.put('command',chain.paths['command'],['SYNTHETIC_DIFFERENT_COMMAND'])
        chain.repin_after('command')
    elif kind.startswith('manifest') or kind=='inputs_changed':
        def change(x):
            if kind=='manifest_duplicate': x['artifacts'].append(deepcopy(x['artifacts'][0]))
            elif kind=='manifest_missing': x['artifacts'].pop()
            else: x['inputs_unchanged']=False
        chain.edit('manifest',change)
    else:
        def change(x):
            if kind=='solver_duplicate': x['artifacts'].append(deepcopy(x['artifacts'][0]))
            elif kind=='solver_outside': x['artifacts'].append(chain.p('gds'))
            else:
                suffix='.s4p' if kind=='touchstone_missing' else 'emx_command.json'
                x['artifacts']=[p for p in x['artifacts'] if not p['path'].endswith(suffix)]
        chain.edit('solver',change)
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['hash','size','missing','symlink'])
def test_exact_mirror_resolution_rejects_wrong_or_unpublished_source(chain,kind):
    mirror=chain.mirror(); source=chain.p('csv'); item=mirror.entries[source['path']]
    if kind=='missing': del mirror.entries[source['path']]
    elif kind=='hash': Path(item['resolved']['path']).write_bytes(b'OTHER SAME-ORDER DATA\n')
    elif kind=='size': item['resolved']['bytes']+=1
    else:
        link=chain.root/'CSV_SYMLINK'; link.symlink_to(item['resolved']['path'])
        item['resolved']['path']=str(link)
    with pytest.raises(m.SelectedEvidenceError): chain.inspect(mirror)


def test_final_recheck_detects_source_mutation_during_read(chain,monkeypatch):
    mirror=chain.mirror(); original=m.validate_feature_values
    def changed(*a,**kw):
        result=original(*a,**kw)
        Path(mirror.entries[str(chain.paths['gds'])]['resolved']['path']).write_bytes(b'CHANGED AFTER READ')
        return result
    monkeypatch.setattr(m,'validate_feature_values',changed)
    with pytest.raises(m.SelectedEvidenceError,match='changed'): chain.inspect(mirror)


def test_mixed_candidate_feature_tree_rejected(chain):
    other=chain.root/'original_native'/chain.request_id/'OTHER-q14'/'emx_selected'/'features'/'MANIFEST.json'
    other.parent.mkdir(parents=True); other.write_bytes(chain.paths['manifest'].read_bytes())
    mirror=chain.mirror(); mirror.add(pin(other),pin(other))
    with pytest.raises(m.SelectedEvidenceError,match='directory'):
        m.inspect_features(chain.ctx,chain.candidate_id,pin(other),mirror,expected_private_config=chain.expected_config)


def test_malformed_gds_is_a_selected_evidence_error(chain):
    chain.paths['gds'].write_bytes(b'NOT A VALID GDS STREAM')
    chain.repin_after('gds')
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()


@pytest.mark.parametrize('kind',['short','wrong_frequency','original_row','qmin','strict','percent'])
def test_saved_original56_and_derived_claims_still_validated(chain,kind):
    if kind=='short': chain.rows.pop()
    elif kind=='wrong_frequency': chain.rows[10]['frequency_hz']+=1
    elif kind=='qmin': chain.rows[10]['qmin']+=1
    elif kind=='strict': chain.rows[10]['below_half_srf']=False
    if kind in ('short','wrong_frequency','qmin','strict'):
        chain.write_csv(chain.rows); chain.repin_after('csv')
    if kind in ('qmin','strict'): chain.edit('feature',lambda x:x.__setitem__('original_frequency_row',chain.rows[10]))
    elif kind=='original_row': chain.edit('feature',lambda x:x['original_frequency_row'].__setitem__('lp_nh',99.))
    elif kind=='percent': chain.edit('feature',lambda x:x.__setitem__('target_relative_absolute_percent',[99.]*4))
    with pytest.raises(m.SelectedEvidenceError): chain.inspect()
