"""New controlled6 qualification callback; no native launch or physical re-QA."""
import hashlib
import json
import math
from pathlib import Path
import types

import admission as legacy
import history_publication as history
import controlled_result as original_result
from atomic_primitives import atomic_json, lease

require=legacy.require
digest=legacy.digest
raw=history.raw
SCHEMA='eucap15_controlled6_qualified_increment.v1'
EVIDENCE_SCHEMA='eucap15_controlled6_qualified_evidence.v1'
CONSUMER_RECEIPT_SHA='83d355a3f6f00d120c03200b17c6f8639733a0d7a33f83e34cf0b2eb88e3b47f'
QUALIFICATION='PASS_RECORDED_CHAIN_AND_CURRENT_BYTE_IDENTITIES_NOT_NEW_PHYSICAL_QA'
RUNTIME_PINS={
 'controlled_result.py':'410d9cb813d5ad619e95b19ed067c23105e36afb378a91d6c4f3e84a3c20f23a',
 'controlled_metadata.py':'820e7b87d51d5929a217b1b415088d8188c25a355b7057f779c6e0dc2b66082f',
 'native_birth.py':'88e81f081f29aafdeda1357e1c1d8b9949a746710a9ecf1f503c5d12c6327463',
 'start_slots.py':'7c00b71dee5edf83fab152f5d806f39e9793c86583e9ac7bd24615bd23713831',
}


def check_runtime():
    """Verify the four imported existing sources; do not execute their runners."""
    import controlled_metadata,native_birth,start_slots
    modules=(original_result,controlled_metadata,native_birth,start_slots)
    for module in modules:
        path=Path(module.__file__)
        require(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==RUNTIME_PINS[path.name],
                'wrong existing RESULT-chain implementation')


def qualified_rows(rows):
    require(len(rows)==64 and len({r['request_id'] for r in rows})==64, 'wrong original64 denominator')
    chosen=[r for r in rows if r['core_eligible'] is True]
    require(len(chosen)==6 and sum(r['split']=='train' for r in chosen)==5 and
            sum(r['split']=='test' for r in chosen)==1, 'six frozen core members/splits changed')
    return sorted(chosen,key=lambda r:r['global_order'])


def validate_row(row, proposal, feature, fields, bounds):
    """Validate saved predicates/labels, without numerical extraction or new QA."""
    require(row['state']=='STRICT_VALID' and all(row[k] is True for k in
        ('strict_valid','core_eligible','physical_chain_verified','terminal_publication_verified',
         'native_birth_identity_verified','below_half_srf','descriptor_valid','physics_qa_pass')),
         'frozen consumer qualification incomplete')
    require(row['source_validation']=='PINNED_CANDIDATE_CHAIN_AND_ORIGINAL56_RECONCILED' and
            row['production_admission'] is False, 'wrong saved admission scope')
    require(proposal['request_id']==row['request_id'] and proposal['candidate_id']==row['candidate_id'] and
            proposal['canonical_geometry_sha256']==row['geometry_sha256'] and
            proposal['assigned_development_split']==row['split'] and row['split'] in ('train','validation','test'),
            'original identity or split changed')
    require(proposal['geometry_fields']==fields and proposal['frequency_hz']==15000000000 and
            proposal['geometry_units']=='um' and proposal['target']==row['target'] and
            proposal['proxy']==row['frozen_proxy'] and proposal['q_proxy']==row['q_proxy'] and
            proposal['q_emx'] is row['q_emx'] is None, 'original geometry/Q/target changed')
    keys=history.identities(proposal['geometry'],fields)
    require(keys['canonical_9dp']==row['geometry_sha256'] and
            len(bounds['lower'])==len(bounds['upper'])==10 and
            all(lo<=v<=hi for v,lo,hi in zip(proposal['geometry'],bounds['lower'],bounds['upper'])),
            'geometry identity/bounds mismatch')
    require(proposal['duplicate_reasons']==[], 'original known-pool collision')
    grid=feature['original_56_summary']
    require(grid['port_count']==4 and grid['frequency_points']==56 and
            grid['frequency_start_hz']==5000000000 and grid['frequency_stop_hz']==60000000000 and
            grid['frequency_step_hz']==1000000000 and grid['checks'] and
            all(v is True for v in grid['checks'].values()) and
            grid['passivity_fail_frequency_count']==grid['reciprocity_fail_frequency_count']==0,
            'saved original56 QA invalid')
    f=feature['original_frequency_row']
    physical={k:f[k] for k in ('lp_nh','ls_nh','qp','qs','qmin','signed_k','k_abs')}
    require(all(type(v) in (int,float) and math.isfinite(v) for v in physical.values()) and
            physical['qmin']==min(physical['qp'],physical['qs']) and
            physical['k_abs']==abs(physical['signed_k']), 'physical label definition mismatch')
    require(feature['strict_lumped_valid'] is True and feature['core15_eligible'] is True and
            feature['production_membership'] is False and
            f['below_half_srf']=='true' and f['strict_lumped_valid']=='true' and
            .5<=f['lp_nh']<=2 and .5<=f['ls_nh']<=2 and .2<=f['k_abs']<=.85 and
            [f[k] for k in ('lp_nh','ls_nh','qmin','k_abs')]==row['actual'],
            'saved strict/core labels do not match accepted consumer')
    return keys,physical


class FrozenOne:
    """Narrow view of the exact frozen proposal required by original RESULT reader."""
    def __init__(self,result):
        self.result=result
        self.manifest_pin=result['controlled_manifest']
        self.intent_pin=result['controlled_intent']
    def candidate(self,rid):
        row=self.result['original_proposal']
        require(rid==row['request_id'], 'wrong frozen request')
        return dict(original=row,model_used_for_proposal=row['source']=='SPARSE_TARGETED')


def build_evidence(inputs,request_id,*,reader=raw):
    check_runtime()
    checked={}
    def read(pin):
        normalized=history._normalized_pin(pin)
        require(pin['path'] not in checked or checked[pin['path']]==normalized,'conflicting source pin')
        data=reader(pin);checked[pin['path']]=normalized
        return data
    load=lambda p:json.loads(read(p))
    cp=inputs['consumer_receipt']
    require(cp['sha256']==CONSUMER_RECEIPT_SHA,'wrong frozen accepted consumer')
    receipt=load(cp)
    require(receipt['status']=='PASS_SCOPED_INCREMENTAL_CLOSED64' and receipt['source_bytes_unchanged'] is True,
            'consumer not accepted')
    table=load(receipt['artifacts']['REQUEST_RESULTS.json'])
    matches=[r for r in qualified_rows(table['rows']) if r['request_id']==request_id]
    require(len(matches)==1,'not one of the six frozen core members')
    row=matches[0]
    spec=load(receipt['spec'])
    known=load(receipt['artifacts']['READ_SOURCE_PINS.json'])
    pinmap={}
    for entry in known:
        p=entry['original'];old=pinmap.get(p['path'])
        require(old is None or history._same_pin(old,p), 'conflicting previously read source')
        pinmap[p['path']]=p
    result=load(row['source_result'])
    batch=FrozenOne(result)
    # Copy only the existing pure function globals. Original modules/files remain unchanged.
    env=dict(original_result.fresh_candidate.__globals__)
    env['read_pin']=read
    env['load']=lambda p:original_result.parse(read(p))
    fresh=types.FunctionType(original_result.fresh_candidate.__code__,env)(
        batch,request_id,feature_pin=row['feature'],observation_pin=row['native_observation_pin'],
        plan_pin=result['plan'])
    require(all(result.get(k)==v for k,v in fresh.items()),'saved RESULT differs from original chain reader')
    require(result['release']==spec['expected_release'],'foreign frozen owner release')
    feature=load(row['feature']);pre=load(row['preflight']);solver=load(row['solver'])
    require(feature['preflight']==row['preflight'] and feature['solver_receipt']==row['solver'],
            'feature source chain mismatch')
    frozen=load(batch.manifest_pin)
    proposals=[json.loads(line) for line in read(frozen['files']['SELECTED_CANDIDATES.jsonl']).splitlines()]
    originals=[p for p in proposals if p['request_id']==request_id]
    require(len(originals)==1 and originals[0]==result['original_proposal'],'proposal not in original frozen set')
    contract=load(inputs['current_contract_source'])['contract']
    require(contract['scientific_fingerprint']==legacy.FP and
            contract['label_frequency_hz']==15000000000 and
            contract['full_frequency_hz']==[n*1000000000 for n in range(5,61)],'wrong current15 contract')
    for p in pre['source_pins']:read(p)
    for p in contract['pins']:
        matches=[s for s in pre['source_pins'] if s['path']==p['path']]
        if matches:require(matches[0]['sha256']==p['sha256'],'physical source version changed')
        read(p)
    read(pre['port_manifest'])
    calibre=load(pre['calibre'])
    require(calibre['blocking_drc_violation_count']==0 and calibre['checks'] and
            all(v is True for v in calibre['checks'].values()),'saved Calibre not zero-blocking')
    keys,physical=validate_row(row,result['original_proposal'],feature,
                              contract['geometry_order'],contract['geometry_bounds'])
    manifest_path=str(Path(row['feature']['path']).parent/'MANIFEST.json')
    require(manifest_path in pinmap,'missing accepted56 manifest binding: '+manifest_path)
    fp=pinmap[manifest_path];fm=load(fp)
    require(fm['inputs_unchanged'] is True and row['feature'] in fm['artifacts'],'unbound saved feature manifest')
    csv_pins=[p for p in fm['artifacts'] if Path(p['path']).name=='features_56.csv']
    require(len(csv_pins)==1,'missing unique full56 CSV')
    csvpin=csv_pins[0];read(csvpin)
    require(history._same_pin(solver['touchstone'],row['s4p']),'S4P pin mismatch')
    full={name:dict(original=p) for name,p in
          [('MANIFEST.json',fp),('feature_receipt',row['feature']),('features_56.csv',csvpin),('s4p',row['s4p'])]}
    proposal=result['original_proposal']
    member=dict(schema='eucap15_controlled6_qualification_member.v1',request_id=request_id,
        candidate_id=proposal['candidate_id'],geometry=proposal['geometry'],geometry_fields=proposal['geometry_fields'],
        geometry_sha256=row['geometry_sha256'],geometry_units='um',frequency_hz=15000000000,split=row['split'],
        source=row['source'],arm=row['arm'],target=row['target'],proxy=row['frozen_proxy'],q_proxy=row['q_proxy'],
        actual=row['actual'],full56_evidence=full,original_result=row['source_result'],
        original_consumer_receipt=cp,membership='FORMAL_QUALIFICATION_READY_NOT_YET_COMMITTED',
        production_accepted=False,evidence_class='FRESH_REAL_EMX',training_performed=False)
    return dict(schema=EVIDENCE_SCHEMA,member=member,physical15=physical,identities=keys,
        source_pins=list(checked.values()),source_inputs=inputs,source_request_id=request_id,
        qualification=QUALIFICATION,scientific_contract_fingerprint=legacy.FP,
        physical_qa_repeated=False,extraction_repeated=False,simulator_actions=0,
        full_history_certified=False,split_preserved=True,training_membership_changed=False)


def publish(root,evidence,utc,*,reader=raw,first_sha=legacy.FIRST_SHA):
    """Native owner only: append make_record-compatible entry to original union."""
    root=Path(root)
    require(root.name=='qualified15_single_member_v1' and
        not any(p.is_symlink() for p in (root,*root.parents,root/'records',root/'WRITE.lock')),'wrong existing union')
    require(evidence['schema']==EVIDENCE_SCHEMA and evidence['qualification']==QUALIFICATION and
            evidence['scientific_contract_fingerprint']==legacy.FP,'not qualified controlled6 evidence')
    m=evidence['member'];keys=history.identities(m['geometry'],m['geometry_fields'])
    require(keys==evidence['identities'],'controlled6 identity mismatch')
    with lease(root/'WRITE.lock'):
        current=history.ledger(root,first_sha)
        for item in current:
            r=item['value']['record']
            if r['request_id']==m['request_id']:
                require(item['value']['evidence_digest']==digest(evidence),'conflicting controlled6 replay')
                return dict(status='ALREADY_COMMITTED_NO_COUNT_CHANGE',added=0,path=item['path'],sha256=item['sha256'])
            matches=[k for k in keys if keys[k]==item['identities'][k]]
            if matches:return dict(status='DUPLICATE_CURRENT_QUALIFIED_UNION_NO_COMMIT',added=0,
                                   matching_namespaces=matches,existing_record=item['path'])
        rebuilt=build_evidence(evidence['source_inputs'],evidence['source_request_id'],reader=reader)
        require(rebuilt==evidence,'source changed after controlled6 preparation')
        seq=len(current)+1
        value=dict(schema=SCHEMA,status=legacy.STATUS,utc=utc,record=legacy.make_record(evidence,seq),
            evidence=evidence,evidence_digest=digest(evidence),
            checkpoint=dict(increment_accepted=seq,increment_15ghz_rows=seq,referenced_frequency_rows=seq*56,
                            last_increment_sequence=seq,prior_commit=current[-1]['sha256']),
            counting=dict(this_certified_increment=1,old_broadband_added=0,complete_100k_total=None,
                          full_history_certified=False,prior_research_member_count_changed=False),
            limitations=['Known certified union only; no claim all historical sources are certified.',
                         'Original controlled64 targets, Q, split, negatives and total budget unchanged.',
                         'No fresh solver, extraction, physical QA or training.'])
        path=root/'records'/f'{seq:06d}.json';sha=atomic_json(path,value,immutable=True)
        require(json.loads(path.read_bytes())==value and hashlib.sha256(path.read_bytes()).hexdigest()==sha,
                'controlled6 append readback failed')
        return dict(status=legacy.STATUS,added=1,path=str(path),sha256=sha,
                    qualification_sequence=seq,split=m['split'],old_broadband_added=0)
