"""Append audited historical geometry to the existing15GHz qualified union.

No SSH, simulator, training, new labels or old broadband-ledger writes. Native
owner supplies actual pinned audit/source files. Read existing mixed-schema
records under their original WRITE.lock and publish one exclusive next record.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

import admission as legacy
from atomic_primitives import atomic_json, lease
from geometry_helpers import canonical_geometry_sha256, _production_geometry_fingerprint

SCHEMA = 'eucap15_historical_qualified_increment.v1'
EVIDENCE_SCHEMA = 'eucap15_historical_qualified_evidence.v1'
AUDIT_SOURCE_SHA = 'c2fa7380183dae8d58a95b6244134248619c1b24aeec821e511a6b17b0c43ea8'
FP = legacy.FP
STATUS = legacy.STATUS
require = legacy.require
digest = legacy.digest


def raw(pin):
    p=Path(pin['path'])
    require(not any(x.is_symlink() for x in (p,*p.parents)), 'source symlink')
    return legacy.raw(pin)


def identities(geometry, fields):
    require(len(geometry)==len(fields)==10 and len(set(fields))==10 and
            all(type(v) in (int,float) and math.isfinite(v) for v in geometry), 'finite exact10 geometry required')
    return dict(canonical_9dp=canonical_geometry_sha256(dict(zip(fields,geometry))),
        production_1e6=_production_geometry_fingerprint(geometry),
        nominal_grid_5nm=canonical_geometry_sha256(dict(zip(fields,np.rint(np.asarray(geometry)/.005)*.005))))


def _normalized_pin(pin):
    return dict(path=pin['path'],sha256=pin['sha256'],bytes=pin.get('bytes',pin.get('size_bytes')))


def _same_pin(a,b):
    return _normalized_pin(a)==_normalized_pin(b)


def build_evidence(audit_receipt_pin, block_pin, source_old_accepted_sequence, *, split_pin=None, reader=raw):
    """Consume one already-qualified row, not rerun the1000-source-chain audit."""
    audit=json.loads(reader(audit_receipt_pin))
    require(audit['status']=='1000_SOURCE_CHAIN_AUDIT_COMPLETE_SHARED_PUBLICATION_PENDING'
            and audit['processed']==1000 and len(audit['blocks'])==10
            and sum(audit['classifications'].values())==1000, 'wrong closed1000 audit')
    require(audit['source_pin']['sha256']==AUDIT_SOURCE_SHA, 'unrecognized historical qualification source')
    reader(audit['source_pin'])
    require(any(_same_pin(block_pin,p) for p in audit['blocks']), 'block not bound by frozen audit')
    block=json.loads(reader(block_pin))
    matches=[r for r in block['rows'] if r['source_old_accepted_sequence']==source_old_accepted_sequence]
    require(len(matches)==1 and type(source_old_accepted_sequence) is int and
            1<=source_old_accepted_sequence<=1000, 'unique bounded historical source row required')
    row=matches[0]
    require(row['status']=='qualified' and row['qualification_sequence'] is None and
            row['qualified_ledger_committed'] is False and row['old_broadband_recounted'] is False,
            'row is not unpublished qualified source evidence')
    inputs=json.loads(reader(audit['input_pin']))
    contract=inputs['contract']
    require(block['metadata_source_pins']=={k:inputs['source'][k] for k in ('accepted','index')}
            and block['label_source']==inputs['labels'], 'audit block source-table binding mismatch')
    require(contract['scientific_fingerprint']==FP and
            row['geometry_fields']==contract['geometry_order'] and
            contract['label_frequency_hz']==15000000000 and
            contract['full_frequency_hz']==[n*1000000000 for n in range(5,61)], 'current scientific contract mismatch')
    require(all(lo<=v<=hi for v,lo,hi in zip(row['geometry'],contract['geometry_bounds']['lower'],
            contract['geometry_bounds']['upper'])), 'historical geometry outside current bounds')
    calculated=identities(row['geometry'],row['geometry_fields'])
    require(calculated==row['identities'] and calculated['canonical_9dp']==row['geometry_sha256'], 'historical identity mismatch')
    physical=row['physical15']
    require(all(math.isfinite(physical[k]) for k in ('lp_nh','ls_nh','qp','qs','qmin','signed_k','k_abs'))
            and physical['qmin']==min(physical['qp'],physical['qs']) and physical['k_abs']==abs(physical['signed_k'])
            and .5<=physical['lp_nh']<=2 and .5<=physical['ls_nh']<=2 and .2<=physical['k_abs']<=.85,
            'current15 core physical values not qualified')
    checked=row['checked_source_pins']
    require(checked and all(any(_same_pin(row[k],p) for p in checked)
            for k in ('s4p','gds','source_receipt','source_calibre')), 'required actual artifact bindings missing')
    require(block['current_qualified_ledger_added']==0 and block['simulator_actions']==0 and
            block['labels_recomputed'] is False, 'unexpected source audit intervention')
    split=None;split_status='UNKNOWN_NOT_ASSIGNED';split_evidence=None
    if split_pin is not None:
        splits=json.loads(reader(split_pin))
        require(splits['schema']=='bb_splits.v1' and splits['seed']==17 and
                isinstance(splits['by_geometry_sha256'],dict), 'unrecognized preserved split evidence')
        split=splits['by_geometry_sha256'].get(row['geometry_sha256'])
        require(split is None or split in ('train','validation','test'), 'invalid preserved split')
        if split is not None:split_status='PRESERVED_SOURCE_SPLIT_NOT_NEW_ASSIGNMENT'
        split_evidence=split_pin
    # These current-byte checks bind the already-completed chain; no extractor,
    # simulator, full input-table join or saved physical QA is executed again.
    all_pins=[audit_receipt_pin,block_pin,audit['input_pin'],audit['source_pin'],
              *audit['shared_source_pins'],*checked]
    if split_pin is not None:all_pins.append(split_pin)
    seen={}
    for p in all_pins:
        key=p['path'];norm=_normalized_pin(p)
        require(key not in seen or seen[key]==norm, 'conflicting source pin')
        if key not in seen:reader(p);seen[key]=norm
    return dict(schema=EVIDENCE_SCHEMA,qualification='PASS_EXISTING_AUDIT_AND_CURRENT_BYTE_BINDINGS',
        audit_receipt=audit_receipt_pin,source_block=block_pin,source_old_accepted_sequence=source_old_accepted_sequence,
        audited_row=row,source_pins=list(seen.values()),scientific_contract_fingerprint=FP,
        preserved_split=split,split_status=split_status,split_evidence=split_evidence,
        full_history_certified=False,labels_recomputed=False,simulator_actions=0)


def make_record(evidence, sequence):
    row=evidence['audited_row'];h=row['geometry_sha256']
    require(evidence['schema']==EVIDENCE_SCHEMA and evidence['scientific_contract_fingerprint']==FP and
            evidence['qualification']=='PASS_EXISTING_AUDIT_AND_CURRENT_BYTE_BINDINGS', 'not validated historical evidence')
    # A namespaced request identifier keeps historical source-row identity
    # separate from any research request id; original geometric ID is retained.
    request_id='HISTORICAL15-'+str(evidence['source_old_accepted_sequence'])+'-'+h
    return dict(request_id=request_id,candidate_id=h,candidate_id_sha256=h,
        geometry_sha256=h,geometry=row['geometry'],geometry_fields=row['geometry_fields'],geometry_units='um',
        source='HISTORICAL_OLD_BROADBAND_QUALIFICATION',frequency_hz=15000000000,
        split=evidence['preserved_split'],split_status=evidence['split_status'],split_evidence=evidence['split_evidence'],
        physical15=row['physical15'],original56=dict(s4p=row['s4p'],source_receipt=row['source_receipt']),
        increment_sequence=sequence,qualification_sequence=sequence,
        source_old_accepted_sequence=evidence['source_old_accepted_sequence'],
        production_accepted_sequence=evidence['source_old_accepted_sequence'],
        old_broadband_production_accepted=True,old_broadband_added=0,
        eucap15_qualified_accepted=True,scientific_contract_fingerprint=FP,
        source_geometry_id=h,source_candidate_id_sha256=h,
        physical_stage_intervention='HISTORICAL_REUSE_NO_NEW_SOLVER',
        research_member_created=False,gradient_training_membership_changed=False,
        identity_namespaces=row['identities'])


def ledger(root, first_sha=legacy.FIRST_SHA):
    """Existing continuous chain reader with one narrow historical schema branch."""
    root=Path(root);paths=sorted((root/'records').glob('*.json'))
    require(paths, 'existing sequence1 required')
    result=[];seen_request=set();seen={k:set() for k in ('canonical_9dp','production_1e6','nominal_grid_5nm')}
    for sequence,path in enumerate(paths,1):
        require(not path.is_symlink() and path.name==f'{sequence:06d}.json', 'ledger sequence/path conflict')
        data=path.read_bytes();sha=hashlib.sha256(data).hexdigest();value=json.loads(data)
        record=value['record'];checkpoint=value['checkpoint']
        require(value['status']==STATUS and record['eucap15_qualified_accepted'] is True and
                record['scientific_contract_fingerprint']==FP, 'unqualified or wrong-contract ledger')
        require(record['increment_sequence']==checkpoint['last_increment_sequence']==checkpoint['increment_accepted']==
                checkpoint['increment_15ghz_rows']==sequence and checkpoint['referenced_frequency_rows']==sequence*56,
                'ledger checkpoint sequence mismatch')
        require(value['evidence_digest']==digest(value['evidence']), 'stored evidence corrupt')
        if sequence==1:
            require(sha==first_sha and checkpoint['prior_commit'] is None, 'first record changed')
        else:
            require(checkpoint['prior_commit']==result[-1]['sha256'], 'broken prior commit chain')
        if value.get('schema')==SCHEMA:
            require(record==make_record(value['evidence'],sequence), 'historical record/evidence mismatch')
            require(value['counting']['old_broadband_added']==0 and value['counting']['this_certified_increment']==1,
                    'historical broadband recount forbidden')
        else:
            require(record['production_accepted_sequence'] is None and record['old_broadband_production_accepted'] is False,
                    'unknown mixed-ledger historical schema')
            if sequence>1:require(record==legacy.make_record(value['evidence'],sequence), 'legacy record/evidence mismatch')
        require(record['request_id'] not in seen_request, 'duplicate ledger request')
        keys=identities(record['geometry'],record['geometry_fields'])
        require(keys['canonical_9dp']==record['geometry_sha256'], 'ledger geometry mismatch')
        for kind,key in keys.items():
            require(key not in seen[kind], 'duplicate ledger '+kind)
            seen[kind].add(key)
        seen_request.add(record['request_id'])
        result.append(dict(path=str(path),sha256=sha,value=value,identities=keys))
    return result


def publish(root, evidence, utc, *, reader=raw, first_sha=legacy.FIRST_SHA):
    """One append or exact replay/duplicate result under the existing lock."""
    root=Path(root)
    require(root.name=='qualified15_single_member_v1' and
            not any(p.is_symlink() for p in (root,*root.parents,root/'records',root/'WRITE.lock')),
            'wrong existing qualified namespace')
    expected=make_record(evidence,1);keys=evidence['audited_row']['identities']
    require(keys==identities(expected['geometry'],expected['geometry_fields']), 'candidate identity mismatch')
    with lease(root/'WRITE.lock'):
        current=ledger(root,first_sha)
        for previous in current:
            r=previous['value']['record']
            if r['request_id']==expected['request_id']:
                require(previous['value']['evidence_digest']==digest(evidence), 'conflicting replay')
                return dict(status='ALREADY_COMMITTED_NO_COUNT_CHANGE',added=0,path=previous['path'],sha256=previous['sha256'])
            matching=[kind for kind,key in keys.items() if previous['identities'][kind]==key]
            if matching:
                return dict(status='DUPLICATE_CURRENT_QUALIFIED_UNION_NO_COMMIT',added=0,
                            matching_namespaces=matching,existing_record=previous['path'],existing_sha256=previous['sha256'])
        # Reconstruct from actual bound audit/block, not a caller-written PASS
        # string or edited evidence body, immediately before exclusive append.
        rebuilt=build_evidence(evidence['audit_receipt'],evidence['source_block'],
            evidence['source_old_accepted_sequence'],split_pin=evidence['split_evidence'],reader=reader)
        require(rebuilt==evidence, 'historical evidence differs from current frozen source')
        sequence=len(current)+1
        value=dict(schema=SCHEMA,status=STATUS,utc=utc,record=make_record(evidence,sequence),
            evidence=evidence,evidence_digest=digest(evidence),
            checkpoint=dict(increment_accepted=sequence,increment_15ghz_rows=sequence,referenced_frequency_rows=56*sequence,
                last_increment_sequence=sequence,prior_commit=current[-1]['sha256']),
            counting=dict(this_certified_increment=1,old_broadband_added=0,complete_100k_total=None,full_history_certified=False),
            limitations=['Bounded qualified union, not full historical certification.',
                        'Source old accepted sequence is provenance, not a new broadband acceptance.',
                        'Unknown split remains unassigned; no data/model/training membership update.',
                        'No fresh EMX or physical label re-extraction.'])
        path=root/'records'/f'{sequence:06d}.json'
        sha=atomic_json(path,value,immutable=True)
        require(json.loads(path.read_bytes())==value and hashlib.sha256(path.read_bytes()).hexdigest()==sha, 'readback failed')
        return dict(status=STATUS,added=1,path=str(path),sha256=sha,
                    qualification_sequence=sequence,source_old_accepted_sequence=evidence['source_old_accepted_sequence'],
                    old_broadband_added=0,split=evidence['preserved_split'])
