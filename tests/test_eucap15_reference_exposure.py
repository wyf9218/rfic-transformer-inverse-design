"""Pure synthetic membership joins; not checkpoint/native/data validation.

The fixture explicitly assumes an upstream audited seen-subset proof. These
tests validate only derive() arithmetic and guards, not that external proof.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import socket
import subprocess

import pytest

from research.broadband56_nn.eucap15_reference_exposure import derive


def fixture():
    hashes={letter:hashlib.sha256(('SYNTHETIC-'+letter).encode()).hexdigest() for letter in 'ABCDEF'}
    membership=[dict(geometry_sha256=hashes[key],split=split,
        strict_lumped_valid=str(strict),four_labels_finite='True',old_strict_eligible=str(strict))
        for key,split,strict in [('A','train',True),('B','train',True),('C','train',False),
            ('D','validation',True),('E','test',True)]]
    members=[dict(geometry_sha256=h,source_group='SYNTHETIC_ONLY',
        parent_geometry_sha256='UNKNOWN',prototype_id='UNKNOWN') for h in hashes.values()]
    splits={hashes[k]:v for k,v in zip('ABCDEF',('train','validation','test','validation','test','train'))}
    reference=dict(reference_label='FORMAL10K_REFERENCE',status='REFERENCE_LOADED_NOT_FINAL',
        model_id='SYNTHETIC_SINGLE_REFERENCE',source_snapshot_geometries=5,
        eligible_rows={split:dict(eligible_geometries=count) for split,count in
            [('train',2),('validation',1),('test',1)]},
        roles={role:{choice:dict(unique_gradient_geometries=2) for choice in ('best','last')}
            for role in ('forward','inverse')})
    return reference,membership,members,splits,hashes


@pytest.fixture(autouse=True)
def prohibit_external_activity(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('derive must not read files, models, data or execute network/native work')
    monkeypatch.setattr(Path,'open',forbidden)
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    monkeypatch.setattr(Path,'read_text',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)


def test_audited_complete_train_join_exact_seen_conflict_and_no_mutation():
    ref,old,core,splits,h=fixture();before=deepcopy((ref,old,core,splits))
    rows,cross,conflicts=derive(ref,old,core,splits)
    assert (ref,old,core,splits)==before
    assert {r['geometry_sha256'] for r in rows if r['reference_gradient_seen_f_best_last_i_best_last']}=={h['A'],h['B']}
    assert conflicts==[h['B']] and sum(r['n'] for r in cross)==len(core)==6
    assert all(r['all_historical_model_exposure']=='UNKNOWN_NOT_AUDITED' and
        r['FINAL_independence']=='NOT_CERTIFIED' for r in rows)
    assert {r['reference_model_id'] for r in rows}=={'SYNTHETIC_SINGLE_REFERENCE'}


def test_old_holdouts_not_gradient_seen_but_never_claim_never_selected():
    ref,old,core,splits,h=fixture();rows,_,_=derive(ref,old,core,splits)
    byhash={r['geometry_sha256']:r for r in rows}
    for key in ('D','E'):
        row=byhash[h[key]]
        assert row['reference_gradient_seen_f_best_last_i_best_last'] is False
        assert row['reference_exposure_evidence']=='AUDITED_NON_TRAIN_SPLIT'
        assert row['reference_strict_eligible'] is True
        assert row['all_historical_model_exposure']=='UNKNOWN_NOT_AUDITED'
    assert byhash[h['C']]['reference_exposure_evidence']=='AUDITED_TRAIN_INELIGIBLE'


def test_outside_reference_and_excluded_current_member_remain_distinct():
    ref,old,core,splits,h=fixture();del splits[h['F']]
    rows,_,_=derive(ref,old,core,splits);outside=next(r for r in rows if r['geometry_sha256']==h['F'])
    assert outside['reference_source_split']=='NOT_IN_REFERENCE_SNAPSHOT'
    assert outside['reference_exposure_evidence']=='OUTSIDE_AUDITED_SOURCE_SNAPSHOT'
    assert outside['current_development_split']=='EXCLUDED_FROM_DEVELOPMENT6329'
    assert outside['reference_gradient_seen_f_best_last_i_best_last'] is False


def test_duplicate_source_or_owner_membership_never_silently_deduplicated():
    for duplicate_old in (True,False):
        ref,old,core,splits,_=fixture()
        target=old if duplicate_old else core;target.append(deepcopy(target[0]))
        with pytest.raises(ValueError,match='duplicate geometry'):
            derive(ref,old,core,splits)


def test_any_partial_seen_ledger_cannot_establish_exact_member_exposure():
    for role in ('forward','inverse'):
        for choice in ('best','last'):
            ref,old,core,splits,_=fixture()
            ref['roles'][role][choice]['unique_gradient_geometries']=1
            with pytest.raises(ValueError,match='exact row exposure cannot be inferred'):
                derive(ref,old,core,splits)


def test_every_mask_flag_validated_even_when_strict_false():
    for field,value in [('four_labels_finite','MALFORMED'),('four_labels_finite',True),
        ('strict_lumped_valid','false'),('old_strict_eligible','0')]:
        ref,old,core,splits,_=fixture()
        old[2][field]=value
        with pytest.raises(ValueError,match='invalid audited membership boolean'):
            derive(ref,old,core,splits)


def test_false_eligibility_or_reference_counts_fail_closed():
    ref,old,core,splits,_=fixture();old[2]['old_strict_eligible']='True'
    with pytest.raises(ValueError,match='eligibility mask differs'):derive(ref,old,core,splits)
    ref,old,core,splits,_=fixture();ref['eligible_rows']['train']['eligible_geometries']=3
    with pytest.raises(ValueError,match='eligible membership differs'):derive(ref,old,core,splits)
    ref,old,core,splits,_=fixture();ref['source_snapshot_geometries']=6
    with pytest.raises(ValueError,match='source membership count differs'):derive(ref,old,core,splits)


def test_unknown_member_or_invalid_split_not_assumed_outside_or_train():
    for attack in ('unknown','old_split','current_split'):
        ref,old,core,splits,h=fixture()
        if attack=='unknown':splits['f'*64]='train'
        elif attack=='old_split':old[0]['split']='HOLDOUT_UNKNOWN'
        else:splits[h['A']]='HOLDOUT_UNKNOWN'
        with pytest.raises(ValueError):derive(ref,old,core,splits)
