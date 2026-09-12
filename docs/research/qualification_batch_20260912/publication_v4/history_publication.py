"""Append audited historical geometry to the existing15GHz qualified union.

No SSH, simulator, training, new labels or old broadband-ledger writes. Native
owner supplies actual pinned audit/source files. Read existing mixed-schema
records under their original WRITE.lock and publish one exclusive next record.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

import admission as legacy
from atomic_primitives import atomic_json, lease
from geometry_helpers import canonical_geometry_sha256, _production_geometry_fingerprint

SCHEMA = 'eucap15_historical_qualified_increment.v1'
EVIDENCE_SCHEMA = 'eucap15_historical_qualified_evidence.v1'
AUDIT_SOURCE_SHA = 'c2fa7380183dae8d58a95b6244134248619c1b24aeec821e511a6b17b0c43ea8'
PARTITION_SCHEMA = 'eucap15_history_partition_block_audit.v1'
# This authority is fixed from the owner's actual source, not caller evidence.
TRUSTED_PARTITION_DRIVER_SHA = '8f797da614743404a778bbcea46bacced97f9ef1c86c193322788f8a77be91a7'
TRUSTED_QUALIFICATION_SHA = '6e6ba71e7f35e5d78dbb0a194c08489389343c2e0c86b9584f793947e5fddf76'
TRUSTED_GATE_SCHEMA_SHA = 'c7ecf5d841dcd1d0de266117e815406a69655c4b369352642f81ca0d1d226ff7'
TRUSTED_PARENT_AUDIT_SHA = 'ff81e81b33242bee132f1829da824d1db415dd9ceda3bbbf717fb9a4cdbd1eaa'
PARTITION_CLASSIFICATIONS = ('qualified','duplicate','out_of_range','physical_invalid','missing_evidence','incompatible')

CURSOR_SCHEMA = 'eucap15_history_cursor_block_audit.v1'
TRUSTED_CURSOR_DRIVER_SHA = '86f9bc195bb01bb7db34806318f1553690a7a0115d2171d3467df0c4bba710e2'
TRUSTED_CURSOR_RELEASE_SHA = 'af3e5ed12569f762a619ba1b21b729abeb8a24555fbe935eab7d537064ba96e3'
TRUSTED_CURSOR_INPUT_SHA = '9f18ebcabae9129b4298e3e3c018f65912ff5b9f7b200295b4ed37907549ff6f'
TRUSTED_CURSOR_MANIFEST_SHA = 'dea1af9cd00d343eb1bf2bff7781e4772bafff659c660145b544432a6091f6bc'
TRUSTED_CURSOR_PARENT_SHA = 'ee3c4d5f0b0e90b931ed7455ecbb5af7affacde981939997d5ec5f615b6b2032'

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


def _partition_binding(audit, block_pin, reader):
    """Only the pinned owner's1001..2000 driver and its real100-row schema."""
    require(TRUSTED_PARTITION_DRIVER_SHA is not None and
            audit['source_pin']['sha256']==TRUSTED_PARTITION_DRIVER_SHA,
            'untrusted partition driver')
    reader(audit['source_pin'])
    require(audit['schema']==PARTITION_SCHEMA and
            audit['status']=='BOUNDED_SOURCE_CHAIN_AUDIT_COMPLETE_SHARED_PUBLICATION_PENDING' and
            type(audit['processed']) is int and audit['processed']==100 and len(audit['blocks'])==1,
            'wrong bounded partition audit')
    require(_same_pin(block_pin,audit['blocks'][0]), 'partition block not bound by audit')
    bounds=audit['source_range']
    require(isinstance(bounds,dict) and set(bounds)=={'first','last'} and
            type(bounds['first']) is int and type(bounds['last']) is int and
            1001<=bounds['first']<=bounds['last']<=21135 and
            bounds['last']-bounds['first']+1==100 and (bounds['first']-1001)%100==0,
            'invalid bounded source range')
    qp=audit['qualification_source_pin'];sp=audit['required_checks_schema_pin']
    require(qp['sha256']==TRUSTED_QUALIFICATION_SHA and sp['sha256']==TRUSTED_GATE_SCHEMA_SHA,
            'partition qualification/schema identity mismatch')
    reader(qp);schema=json.loads(reader(sp))
    inputs=json.loads(reader(audit['input_pin']))
    # The exact trusted driver authorizes only this assigned next partition.
    require(inputs['source_range']=={'first':1001,'last':2000} and
            inputs['source_range']['first']<=bounds['first']<=bounds['last']<=inputs['source_range']['last'],
            'block outside assigned input range')
    require(len(inputs['baseline_records'])==341, 'wrong frozen certified baseline')
    pp=inputs['parent_completed_audit']
    require(pp['sha256']==TRUSTED_PARENT_AUDIT_SHA, 'untrusted completed parent audit')
    parent=json.loads(reader(pp))
    require(parent['processed']==1000 and
            parent['status']=='1000_SOURCE_CHAIN_AUDIT_COMPLETE_SHARED_PUBLICATION_PENDING' and
            parent['source_pin']['sha256']==AUDIT_SOURCE_SHA, 'parent audit identity/status mismatch')
    reader(parent['source_pin'])
    original_inputs=json.loads(reader(parent['input_pin']))
    expected_contract=dict(original_inputs['contract'], qualification_required_checks_v2=schema['qualification_required_checks_v2'])
    require(inputs['contract']==expected_contract and inputs['source']==original_inputs['source'] and
            inputs['labels']==original_inputs['labels'], 'partition changed original scientific/source contract')
    required_remote=[]
    for name in ('emx','gds'):
        p=schema['actual_source_pins'][name]
        rp=dict(path=p['remote_path'],sha256=p['sha256'],bytes=p['bytes'])
        require(any(_same_pin(rp,pin) for pin in audit['shared_source_pins']),
                'source-backed gate exemplar absent from audited shared pins: '+name)
        reader(rp);required_remote.append(rp)
    # path_map is a local export map, not a MARS source file; do not read it remotely.
    block=json.loads(reader(block_pin))
    require(block['source_range']==bounds and len(block['rows'])==100 and
            [r['source_old_accepted_sequence'] for r in block['rows']]==list(range(bounds['first'],bounds['last']+1)) and
            all(type(r['source_old_accepted_sequence']) is int for r in block['rows']),
            'partition rows not unique ordered continuous100')
    classes=audit['classifications']
    require(set(classes)==set(PARTITION_CLASSIFICATIONS) and
            all(type(v) is int and v>=0 for v in classes.values()) and sum(classes.values())==100 and
            Counter(r['status'] for r in block['rows'])==Counter(classes), 'partition classifications mismatch')
    cumulative=block['cumulative_classifications']
    require(block['cumulative_processed']==bounds['last']-inputs['source_range']['first']+1 and
            set(cumulative)==set(PARTITION_CLASSIFICATIONS) and
            all(type(cumulative[k]) is int and cumulative[k]>=classes[k] for k in classes) and
            sum(cumulative.values())==block['cumulative_processed'] and
            block['baseline_qualified_ledger_count']==len(inputs['baseline_records']),
            'partition cumulative/baseline accounting mismatch')
    require(audit['current_qualified_ledger_added']==audit['simulator_actions']==audit['training_actions']==0 and
            audit['labels_recomputed'] is False and audit['full100k_qualified_total'] is None,
            'unexpected partition intervention or full-history claim')
    return inputs,block,bounds,[qp,sp,pp,parent['input_pin'],parent['source_pin'],*required_remote]



def _cursor_binding(audit, block_pin, reader):
    """Bind the executed cursor release; never rejoin or recertify source tables."""
    authorities=(('source_pin',TRUSTED_CURSOR_DRIVER_SHA),
        ('release_pin',TRUSTED_CURSOR_RELEASE_SHA),('input_pin',TRUSTED_CURSOR_INPUT_SHA),
        ('source_manifest',TRUSTED_CURSOR_MANIFEST_SHA),
        ('qualification_source_pin',TRUSTED_QUALIFICATION_SHA),
        ('required_checks_schema_pin',TRUSTED_GATE_SCHEMA_SHA))
    for key,sha in authorities:
        require(sha is not None and audit[key]['sha256']==sha, 'untrusted cursor '+key)
    require(audit['schema']==CURSOR_SCHEMA and
            audit['status']=='BOUNDED_SOURCE_CHAIN_AUDIT_COMPLETE_SHARED_PUBLICATION_PENDING' and
            len(audit['blocks'])==1 and _same_pin(block_pin,audit['blocks'][0]), 'wrong closed cursor block')
    bounds=audit['source_range']
    require(isinstance(bounds,dict) and set(bounds)=={'first','last'} and
            all(type(bounds[k]) is int for k in bounds) and
            2001<=bounds['first']<=bounds['last']<=21135 and
            (bounds['first']-2001)%100==0 and bounds['last']==min(bounds['first']+99,21135) and
            type(audit['processed']) is int and audit['processed']==bounds['last']-bounds['first']+1,
            'invalid cursor block range')
    rp=audit['release_pin'];ip=audit['input_pin'];mp=audit['source_manifest']
    release=json.loads(reader(rp));inputs=json.loads(reader(ip));manifest=json.loads(reader(mp))
    require(release['schema']=='eucap15_history_cursor_release.v1' and
            release['native_actions_allowed'] is False and _same_pin(release['input_pin'],ip) and
            _same_pin(release['source_manifest'],mp), 'cursor release input/manifest mismatch')
    files=release['files'];required={'partition_audit.py','history_qualification_v2.py',
        'REQUIRED_CHECKS_ACTUAL_SCHEMA.json','history_publication.py','admission.py',
        'geometry_helpers.py','atomic_primitives.py','test_cursor.py','TEST_RECEIPT.json'}
    require(set(files)==required, 'cursor executed release files differ')
    release_root=Path(rp['path']).parent
    for name,pin in files.items():
        require(Path(pin['path'])==release_root/name, 'cursor source outside executed release')
        reader(pin)
    for name,key in (('partition_audit.py','source_pin'),
                     ('history_qualification_v2.py','qualification_source_pin'),
                     ('REQUIRED_CHECKS_ACTUAL_SCHEMA.json','required_checks_schema_pin')):
        require(_same_pin(files[name],audit[key]), 'cursor source not release-bound: '+name)
    require(inputs['schema']=='eucap15_history_cursor_inputs.v1' and
            inputs['source_range']=={'first':2001,'last':21135} and
            _same_pin(inputs['source_manifest'],mp) and len(inputs['baseline_records'])==650,
            'cursor frozen input/scope/baseline changed')
    pp=inputs['parent_completed_audit']
    require(pp['sha256']==TRUSTED_CURSOR_PARENT_SHA, 'cursor parent audit changed')
    parent=json.loads(reader(pp))
    require(parent['processed']==1000 and parent['source_range']=={'first':1001,'last':2000} and
            parent['baseline_records_unchanged'] is True and parent['qualified_added']==0 and
            parent['native_actions']==parent['training_actions']==0 and parent['labels_recomputed'] is False,
            'cursor predecessor incomplete or intervened')
    parent_receipt=json.loads(reader(parent['blocks'][0]))
    require(_same_pin(parent_receipt['input_pin'],inputs['parent_inputs']), 'cursor parent inputs not bound')
    parent_inputs=json.loads(reader(inputs['parent_inputs']))
    gates=json.loads(reader(audit['required_checks_schema_pin']))
    require(inputs['contract']==parent_inputs['contract'] and
            inputs['contract']['qualification_required_checks_v2']==gates['qualification_required_checks_v2'],
            'cursor scientific contract or gate set changed')
    require(manifest['schema']=='eucap15_history_cursor_sources.v1' and
            manifest['scientific_contract_fingerprint']==FP and manifest['source_last_sequence']==21135 and
            manifest['simulator_actions']==0 and manifest['labels_recomputed'] is False,
            'cursor source manifest contract mismatch')
    sources=manifest['sources'];next_sequence=1;names=set()
    for source in sources:
        require(set(source)=={'source_name','first','last','accepted','index','labels','receipt'} and
                type(source['first']) is int and type(source['last']) is int and
                source['first']==next_sequence and source['last']>=source['first'] and
                source['source_name'] not in names, 'cursor source intervals overlap or have gaps')
        next_sequence=source['last']+1;names.add(source['source_name'])
    require(len(sources)==8 and next_sequence==21136, 'cursor original source sequence coverage changed')
    # Aliases preserve byte identity; read only the resolved actual file, never
    # follow the old symlink or silently reinterpret its original provenance.
    extra=[rp,mp,pp,inputs['parent_inputs'],parent['blocks'][0],*files.values()]
    for binding in manifest['path_resolution_bindings']:
        declared=binding['declared'];resolved=binding['resolved']
        require(declared['sha256']==resolved['sha256'] and declared['bytes']==resolved['bytes'] and
                any(_same_pin(resolved,s['receipt']) for s in sources), 'cursor alias changes source identity')
        reader(resolved);extra.append(resolved)
    tail_pin=manifest['tail_selection_receipt'];tail=json.loads(reader(tail_pin));last=sources[-1]
    require(tail['schema']=='eucap15_exact_existing_frequency_selection.v1' and
            tail['frequency_hz']==15000000000 and tail['selected_rows']==last['last']-last['first']+1==162 and
            tail['source_frequency_rows']==162*56 and tail['numeric_values_transcribed_without_recalculation'] is True and
            tail['physical_qa_rerun'] is False and tail['simulator_actions']==0 and
            _same_pin(tail['output'],last['labels']) and _same_pin(tail['source_progress'],last['receipt']) and
            _same_pin(tail['inputs']['accepted_geometry_increment'],last['accepted']) and
            _same_pin(tail['inputs']['s4p_artifact_index'],last['index']), 'cursor tail source selection changed')
    extra.append(tail_pin)
    block=json.loads(reader(block_pin));rows=block['rows'];n=audit['processed']
    require(_same_pin(block['input_pin'],ip) and _same_pin(block['source_manifest'],mp) and
            block['source_range']==bounds and len(rows)==n and
            all(type(r['source_old_accepted_sequence']) is int for r in rows) and
            [r['source_old_accepted_sequence'] for r in rows]==list(range(bounds['first'],bounds['last']+1)),
            'cursor rows not original unique continuous sequence')
    used_sources={}
    for row in rows:
        seq=row['source_old_accepted_sequence']
        matched=[s for s in sources if s['first']<=seq<=s['last']]
        require(len(matched)==1 and row['source_binding']==matched[0], 'cursor per-member source binding mismatch')
        used_sources[matched[0]['source_name']]=matched[0]
    # Large accepted/index/label tables were hash-verified once by the pinned
    # classifier. Their original identities remain in each bound row/manifest;
    # do not repeat full-table hashing/joins for every publication.
    for source in used_sources.values():
        reader(source['receipt']);extra.append(source['receipt'])
    classes=audit['classifications'];cumulative=block['cumulative_classifications']
    require(set(classes)==set(PARTITION_CLASSIFICATIONS) and
            all(type(v) is int and v>=0 for v in classes.values()) and sum(classes.values())==n and
            Counter(r['status'] for r in rows)==Counter(classes), 'cursor class counts mismatch')
    require(block['cumulative_processed']==bounds['last']-2000 and
            set(cumulative)==set(PARTITION_CLASSIFICATIONS) and
            all(type(cumulative[k]) is int and cumulative[k]>=classes[k] for k in classes) and
            sum(cumulative.values())==block['cumulative_processed'] and
            block['baseline_qualified_ledger_count']==650, 'cursor cumulative/baseline mismatch')
    require(block['shared_source_pins']==audit['shared_source_pins'], 'cursor shared source set differs')
    for name in ('emx','gds'):
        source=gates['actual_source_pins'][name]
        pin=dict(path=source['remote_path'],sha256=source['sha256'],bytes=source['bytes'])
        require(any(_same_pin(pin,p) for p in audit['shared_source_pins']), 'cursor required gate source absent: '+name)
        reader(pin);extra.append(pin)
    require(audit['current_qualified_ledger_added']==audit['simulator_actions']==audit['training_actions']==0 and
            audit['labels_recomputed'] is False and audit['full100k_qualified_total'] is None,
            'cursor intervention/full-history claim forbidden')
    return inputs,block,bounds,extra


def build_evidence(audit_receipt_pin, block_pin, source_old_accepted_sequence, *, split_pin=None, reader=raw):
    """Consume one already-qualified row, not rerun the1000-source-chain audit."""
    audit=json.loads(reader(audit_receipt_pin))
    partition_pins=[]
    if audit.get('schema')==CURSOR_SCHEMA:
        inputs,block,bounds,partition_pins=_cursor_binding(audit,block_pin,reader)
    elif audit.get('schema')==PARTITION_SCHEMA:
        inputs,block,bounds,partition_pins=_partition_binding(audit,block_pin,reader)
    else:
        require(audit['status']=='1000_SOURCE_CHAIN_AUDIT_COMPLETE_SHARED_PUBLICATION_PENDING'
                and audit['processed']==1000 and len(audit['blocks'])==10
                and sum(audit['classifications'].values())==1000, 'wrong closed1000 audit')
        require(audit['source_pin']['sha256']==AUDIT_SOURCE_SHA, 'unrecognized historical qualification source')
        reader(audit['source_pin'])
        require(any(_same_pin(block_pin,p) for p in audit['blocks']), 'block not bound by frozen audit')
        block=json.loads(reader(block_pin))
        inputs=json.loads(reader(audit['input_pin']))
        bounds=dict(first=1,last=1000)
    matches=[r for r in block['rows'] if r['source_old_accepted_sequence']==source_old_accepted_sequence]
    require(len(matches)==1 and type(source_old_accepted_sequence) is int and
            bounds['first']<=source_old_accepted_sequence<=bounds['last'], 'unique bounded historical source row required')
    row=matches[0]
    require(row['status']=='qualified' and row['qualification_sequence'] is None and
            row['qualified_ledger_committed'] is False and row['old_broadband_recounted'] is False,
            'row is not unpublished qualified source evidence')
    contract=inputs['contract']
    if audit.get('schema')!=CURSOR_SCHEMA:
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
              *audit['shared_source_pins'],*checked,*partition_pins]
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
