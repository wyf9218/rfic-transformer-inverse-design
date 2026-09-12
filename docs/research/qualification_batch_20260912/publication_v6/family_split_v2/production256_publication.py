"""Publish qualified new256 members into the existing mixed15GHz union.

Consumes the pinned successful receiver, not a second physical-chain runner.
All IO is byte-identity verification; no extraction, QA, simulator or training.
Only the native owner calls publish(). Oldwide counts and frozen splits stay.
"""
import hashlib
import json
import math
from pathlib import Path

import admission as legacy
import history_publication as history
from atomic_primitives import atomic_json, lease

require=legacy.require
digest=legacy.digest
raw=history.raw
SCHEMA='eucap15_production256_qualified_increment.v1'
EVIDENCE_SCHEMA='eucap15_production256_qualified_evidence.v1'
INPUT_SCHEMA='eucap15_production256_qualification_inputs.v1'
STUDY='eucap15_production_doe_neighborhood_20260912_v1'
MANIFEST_SHA='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb'
INTENT_SHA='9de9dc38074df0dc1441dafeb9b8b6b1038e71ec40ffa653a1880854b6a51d42'
RECEIVER_SHA='0b53d6078b8648f34ca7d46652676844118bc73466921a58237259485dc4d196'
RELEASE_SHA='3d10ca46115b420e0c235ba6df4ddbcc6f84e020670cde66118ceb5e75021257'
CONTRACT_SHA='d47cad4fe145bc096e42db85b903747bf78982691b767924870e72af539faaff'
QUALIFICATION='PASS_RECORDED_CHAIN_AND_CURRENT_BYTE_IDENTITIES_NOT_NEW_PHYSICAL_QA'
RUNTIME_PINS={
 'controlled_result.py':'eb5036f6f0f830d30e81fb0f118f63999d87e8d90cacd11fa653a555eb354426',
 'controlled_metadata.py':'4ceed4e6e8db4bbf18565ab7f67b14000642c3ea82f446ffc4d8a307f828eaf0',
 'controlled_execution.py':'8718c94a73094422b7714cec6d489584b0df28fa1df465bb252c5d1abfea17b2',
 'native_birth.py':'e269d337f7892f94444efe4004f420dabfe26c85f1a75ad55147fffa913a41f2',
 'start_slots.py':'6c1b8e7c075282462bfd5eff7aafae53b311062f40ce3871806d76cdae274847',
 'native_resource_probe.py':'2b4e525ee3bd75c42a4182333f9e868d67fd2f251e8af16fecb163b9ad0b038c',
 'geometry_helpers.py':'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98',
}
GRID_CHECKS=('port_count_exact_four','frequency_count_exact_56','frequency_vector_exact',
 'frequency_strictly_increasing','s_matrix_shape_exact','s_matrix_finite','reference_impedance_valid')
DRC_CHECKS=('angle_45_135_pass','calibre_result_accounting_complete','foundry_bridge_connection_pass',
 'foundry_drc_pass','foundry_layout_audit_pass','foundry_power_line_contract_pass',
 'foundry_slotted_ground_frame_pass','foundry_via_stack_and_landing_pad_pass','geometry_range_pass',
 'ground_clearance_pass','line_width_sync_pass','manufacturing_grid_canonicalization_pass',
 'no_blocking_drc_violations','topology_pass')


def true_checks(checks, required, message):
    require(isinstance(checks,dict) and bool(required) and set(required)<=set(checks) and
            all(v is True for v in checks.values()),message)


def split_for_canonical_child(geometry_sha):
    """Exact bb56-split-v1 / seed17 thresholds used by frozen prepare.py."""
    require(isinstance(geometry_sha,str) and len(geometry_sha)==64 and
            all(c in '0123456789abcdef' for c in geometry_sha),'invalid canonical child identity')
    number=int.from_bytes(hashlib.sha256(f"bb56-split-v1:17:{geometry_sha}".encode()).digest()[:8],"big")
    return 'train' if number*10<6*(1<<64) else ('validation' if number*10<8*(1<<64) else 'test')


def validate_neighborhood_family(p):
    """Preserve deterministic child group; a train-family child is not independent."""
    base=p['base_metadata']
    require(isinstance(base,dict) and base['assigned_development_split']=='train' and
            p['final_independent_test_eligible'] is False and
            p['family_warning']=='NEIGHBOR_CHILD_OF_TRAIN_NOT_INDEPENDENT_FINAL_TEST',
            'neighborhood family provenance or independent-test restriction drift')
    require(p['assigned_development_split']==split_for_canonical_child(p['canonical_geometry_sha256']),
            'frozen canonical child seed17 split drift')
    require(isinstance(base['geometry_id'],str) and bool(base['geometry_id']) and
            p['base_train_id']==base['geometry_id'] and
            p['base_geometry_hash']==base['geometry_sha256'] and
            p['base_train_geometry']==base['geometry'] and
            history.identities(base['geometry'],p['geometry_fields'])['canonical_9dp']==base['geometry_sha256'],
            'neighborhood parent identity/geometry binding drift')


def validate_member(result,feature,contract,receipt):
    """Saved-label qualification only, including exact original geometry/split."""
    p=result['original_proposal'];f=feature['original_frequency_row']
    rid=p['request_id'];cid=p['candidate_id']
    require(rid.startswith(STUDY+'-') and rid==cid==result['request_id']==result['candidate_id']==
            feature['candidate_id']==receipt['request_id'],'foreign production request')
    require(p['recipe_sha256']==INTENT_SHA and p['source'] in ('GEOMETRY_DOE','TRAIN_NEIGHBORHOOD') and
            p['arm']==p['source']+'_PRODUCTION' and p['analytic_pass'] is True and
            p['local_dispatch_eligible'] is True and p['duplicate_reasons']==[], 'wrong original proposal')
    require(feature['original_proposal']==p and p['assigned_development_split']==receipt['original_split'] and
            p['assigned_development_split'] in ('train','validation','test'),'original proposal/split drift')
    if p['source']=='TRAIN_NEIGHBORHOOD':
        validate_neighborhood_family(p)
    require(all(p[k] is None for k in ('target','proxy','q_proxy','q_emx','model_id','requested_triple')) and
            all(feature[k] is None for k in ('target','proxy_self','q_proxy','q_requested','q_emx','model_id','candidate_model_id')) and
            all(result[k] is None for k in ('q_proxy','q_emx','candidate_model_id','strict_joint_hit')) and
            receipt['original_target'] is None and receipt['original_q_proxy'] is None and
            feature['model_used_for_proposal'] is False and result['model_used_for_proposal'] is False,
            'DOE/neighborhood is not a target or model/Q selection experiment')
    require(p['geometry_fields']==contract['geometry_order'] and p['geometry_units']=='um' and
            p['frequency_hz']==15000000000,'geometry field/unit/frequency changed')
    keys=history.identities(p['geometry'],p['geometry_fields']);bounds=contract['geometry_bounds']
    require(keys['canonical_9dp']==p['canonical_geometry_sha256']==result['candidate_geometry_identity_sha256'] and
            keys['production_1e6']==p['production_geometry_fingerprint'] and
            len(bounds['lower'])==len(bounds['upper'])==10 and
            all(lo<=x<=hi for x,lo,hi in zip(p['geometry'],bounds['lower'],bounds['upper'])),
            'geometry identity or current bounds mismatch')
    require(result['status']=='FRESH_EMX_EXTRACTED' and result['actual_native_starts']==1 and
            result['production_accepted'] is False and result['core15_eligible'] is True and
            result['valid_for_strict_comparison'] is True and
            feature['schema']=='eucap15_production_geometry_fresh_features.v1' and
            feature['status']=='PASS_EXTRACTION' and feature['dataset_scope']=='DEVELOPMENT_PRODUCTION_GEOMETRY256' and
            feature['frequency_ghz']==15 and feature['production_membership'] is False and
            all(feature[k] is True for k in ('descriptor_valid','physics_qa_pass','strict_lumped_valid',
                                             'valid_for_strict_comparison','core15_eligible')),
            'not a strict/core saved physical member')
    grid=feature['original_56_summary']
    require(grid['port_count']==4 and grid['frequency_points']==56 and grid['frequency_start_hz']==5000000000 and
            grid['frequency_stop_hz']==60000000000 and grid['frequency_step_hz']==1000000000 and
            grid['passivity_fail_frequency_count']==grid['reciprocity_fail_frequency_count']==0,
            'original56 saved QA scope changed')
    true_checks(grid['checks'],GRID_CHECKS,'missing/false original56 QA predicates')
    physical={k:f[k] for k in ('lp_nh','ls_nh','qp','qs','qmin','signed_k','k_abs')}
    require(all(type(v) in (int,float) and math.isfinite(v) for v in physical.values()) and
            physical['qmin']==min(physical['qp'],physical['qs']) and physical['k_abs']==abs(physical['signed_k']) and
            f['frequency_hz']==15000000000 and f['below_half_srf']=='true' and f['strict_lumped_valid']=='true' and
            .5<=f['lp_nh']<=2 and .5<=f['ls_nh']<=2 and .2<=f['k_abs']<=.85,
            'invalid saved label, half-SRF, definition or range')
    actual=[f[k] for k in ('lp_nh','ls_nh','qmin','k_abs')]
    require(actual==feature['actual_fresh_emx']==result['actual_response'],'saved physical values drift')
    return keys,physical


def build_evidence(inputs,request_id,*,reader=raw):
    require(inputs['schema']==INPUT_SCHEMA,'wrong qualification input schema')
    checked={}
    def read(pin):
        p=history._normalized_pin(pin);key=(p['path'],p['sha256'])
        data=reader(p);require(len(data)==p['bytes'] and hashlib.sha256(data).hexdigest()==p['sha256'],
                              'source identity drift')
        checked[key]=p;return data
    load=lambda p:json.loads(read(p))
    cp=inputs['consumer_receipt'];receipt=load(cp)
    require(receipt['schema']=='eucap15_production256_research_received.v1' and
            receipt['status']=='PASS_FROZEN_OWNER_CHAIN_RECEIVED' and receipt['request_id']==request_id and
            receipt['global_denominator']==256 and receipt['no_physical_qa_rerun'] is True and
            receipt['no_label_extraction'] is True and receipt['new_native_starts']==receipt['production_accepted_added']==0 and
            receipt['receiver']['sha256']==RECEIVER_SHA and receipt['source_runtime']==RUNTIME_PINS,
            'wrong or unsuccessful pinned production256 receiver')
    read(receipt['receiver'])
    runtime=inputs['runtime_source_pins']
    require(len(runtime)==7 and {Path(p['path']).name:p['sha256'] for p in runtime}==RUNTIME_PINS,
            'wrong pinned recovery runtime sources')
    for p in runtime:read(p)
    require(receipt['frozen_manifest']['sha256']==MANIFEST_SHA and
            receipt['execution_release']['sha256']==RELEASE_SHA,'foreign frozen256 identity')
    # Recheck already-read bytes, without rerunning fresh_candidate or physics.
    for entry in receipt['source_pins']:read(entry['original'])
    result=load(receipt['source_result'])
    require(result==receipt['verified_result'],'source RESULT changed since accepted receiver')
    spec=load(receipt['source_spec'])
    entries=[e for e in spec['entries'] if e['request_id']==request_id]
    require(spec['schema']=='eucap15_production256_receive_spec.v1' and len(entries)==1 and
            entries[0]['result']==receipt['source_result'] and
            entries[0]['feature_manifest']==receipt['full56']['feature_manifest'] and
            spec['manifest']==receipt['frozen_manifest'] and spec['execution_release']==receipt['execution_release'],
            'receiver spec/member binding changed')
    cache_id=digest(dict(schema=receipt['schema'],request_id=request_id,result=receipt['source_result'],
        manifest=receipt['frozen_manifest'],execution_release=receipt['execution_release'],
        feature_manifest=receipt['full56']['feature_manifest'],runtime=RUNTIME_PINS))
    require(cache_id==receipt['cache_identity'],'receiver content identity changed')
    require(result['schema']=='eucap15_production256_result.v1' and
            result['original_proposal_denominator']==result['original_request_denominator']==256 and
            result['controlled_manifest']==receipt['frozen_manifest'] and
            result['controlled_intent']['sha256']==INTENT_SHA and result['execution_release']==receipt['execution_release'],
            'source RESULT/frozen input binding changed')
    manifest=load(result['controlled_manifest']);read(result['controlled_intent'])
    require(manifest['study_id']==STUDY and manifest['intent']==result['controlled_intent'],'wrong frozen study')
    proposals=[json.loads(line) for line in read(manifest['files']['SELECTED_CANDIDATES.jsonl']).splitlines()]
    require(len(proposals)==256 and len({p['request_id'] for p in proposals})==256 and
            [p for p in proposals if p['request_id']==request_id]==[result['original_proposal']],
            'source proposal absent or changed in original256')
    contract_pin=inputs['current_contract_source']
    require(contract_pin['sha256']==CONTRACT_SHA,'wrong current contract source')
    contract=load(contract_pin)['contract']
    require(contract['scientific_fingerprint']==legacy.FP and contract['label_frequency_hz']==15000000000 and
            contract['full_frequency_hz']==[n*10**9 for n in range(5,61)],'wrong current15 scientific contract')
    feature=load(result['feature']);pre=load(feature['preflight']);solver=load(feature['solver_receipt'])
    keys,physical=validate_member(result,feature,contract,receipt)
    require(pre['schema']=='eucap15_production_geometry_emx_preflight.v1' and pre['status']=='PASS' and
            pre['original_proposal']==result['original_proposal']==feature['original_proposal'] and
            pre['candidate_id']==solver['candidate_id']==result['candidate_id'] and pre['request_id']==request_id and
            pre['geometry_sha256']==keys['canonical_9dp'] and pre['port_order']==['P001','P002','P003','P004'] and
            pre['port_permutation']==[0,1,3,2] and pre['reference_ohm']==50 and
            pre['frequency_grid_hz']==contract['full_frequency_hz'] and
            solver['status']=='PASS' and solver['real_emx'] is True and solver['preflight']==feature['preflight'] and
            solver['source_gds_before']==solver['source_gds_after']==pre['gds'], 'saved actual native chain mismatch')
    for p in pre['source_pins']:read(p)
    # Shared contract sources remain immutable; a relocated patched geometry
    # source is identified separately by the receiver's fixed recovery release.
    for p in contract['pins']:
        matches=[s for s in pre['source_pins'] if s['path']==p['path']]
        require(all(history._same_pin(s,p) for s in matches),'shared physical source version changed')
        read(p)
    for p in (pre['gds'],pre['port_manifest'],solver['touchstone']):read(p)
    require(pre['command'][1]==pre['gds']['path'] and
            any(p['path']==pre['command'][3] for p in pre['source_pins']) and
            any(p['path']==pre['command'][3] for p in contract['pins']), 'process/GDS command not source-bound')
    calibre=load(pre['calibre'])
    require(calibre['blocking_drc_violation_count']==0,'blocking DRC recorded')
    true_checks(calibre['checks'],DRC_CHECKS,'missing/false saved DRC predicates')
    fp=receipt['full56']['feature_manifest'];fm=load(fp)
    require(Path(fp['path'])==Path(result['feature']['path']).parent/'MANIFEST.json' and
            fm['inputs_unchanged'] is True and result['feature'] in fm['artifacts'],'saved56 feature manifest mismatch')
    csv=[p for p in fm['artifacts'] if Path(p['path']).name=='features_56.csv']
    require(len(csv)==1 and csv[0]==receipt['full56']['feature_csv'],'saved56 CSV not bound')
    for p in fm['artifacts']:read(p)
    p=result['original_proposal']
    full={name:dict(original=pin) for name,pin in [('MANIFEST.json',fp),('feature_receipt',result['feature']),
         ('features_56.csv',csv[0]),('s4p',solver['touchstone'])]}
    member=dict(schema='eucap15_production256_qualification_member.v1',request_id=request_id,
        candidate_id=p['candidate_id'],geometry=p['geometry'],geometry_fields=p['geometry_fields'],
        geometry_sha256=keys['canonical_9dp'],geometry_units='um',frequency_hz=15000000000,
        split=p['assigned_development_split'],source=p['source'],arm=p['arm'],target=None,proxy=None,q_proxy=None,
        model_used_for_proposal=False,actual=result['actual_response'],full56_evidence=full,
        original_result=receipt['source_result'],original_consumer_receipt=cp,original_proposal=p,
        membership='FORMAL_QUALIFICATION_READY_NOT_YET_COMMITTED',production_accepted=False,
        evidence_class='FRESH_REAL_EMX',training_performed=False)
    return dict(schema=EVIDENCE_SCHEMA,member=member,physical15=physical,identities=keys,
        source_pins=list(checked.values()),source_inputs=inputs,source_request_id=request_id,
        qualification=QUALIFICATION,scientific_contract_fingerprint=legacy.FP,
        physical_qa_repeated=False,extraction_repeated=False,simulator_actions=0,
        full_history_certified=False,split_preserved=True,training_membership_changed=False)


def publish(root,evidence,utc,*,reader=raw,first_sha=legacy.FIRST_SHA):
    """Native owner only: use the original lease and dynamic mixed-union head."""
    root=Path(root)
    require(root.name=='qualified15_single_member_v1' and
        not any(p.is_symlink() for p in (root,*root.parents,root/'records',root/'WRITE.lock')),'wrong existing union')
    require(evidence['schema']==EVIDENCE_SCHEMA and evidence['qualification']==QUALIFICATION and
            evidence['scientific_contract_fingerprint']==legacy.FP,'not qualified production256 evidence')
    m=evidence['member'];keys=history.identities(m['geometry'],m['geometry_fields'])
    require(keys==evidence['identities'],'production256 identity mismatch')
    with lease(root/'WRITE.lock'):
        current=history.ledger(root,first_sha)
        for item in current:
            r=item['value']['record']
            if r['request_id']==m['request_id']:
                require(item['value']['evidence_digest']==digest(evidence),'conflicting production256 replay')
                return dict(status='ALREADY_COMMITTED_NO_COUNT_CHANGE',added=0,path=item['path'],sha256=item['sha256'])
            matches=[k for k in keys if keys[k]==item['identities'][k]]
            if matches:return dict(status='DUPLICATE_CURRENT_QUALIFIED_UNION_NO_COMMIT',added=0,
                                   matching_namespaces=matches,existing_record=item['path'])
        rebuilt=build_evidence(evidence['source_inputs'],evidence['source_request_id'],reader=reader)
        require(rebuilt==evidence,'source changed after production256 preparation')
        seq=len(current)+1
        value=dict(schema=SCHEMA,status=legacy.STATUS,utc=utc,record=legacy.make_record(evidence,seq),
            evidence=evidence,evidence_digest=digest(evidence),
            checkpoint=dict(increment_accepted=seq,increment_15ghz_rows=seq,referenced_frequency_rows=seq*56,
                            last_increment_sequence=seq,prior_commit=current[-1]['sha256']),
            counting=dict(this_certified_increment=1,old_broadband_added=0,complete_100k_total=None,
                          full_history_certified=False,prior_research_member_count_changed=False),
            limitations=['Known certified union only; all historical sources not yet certified.',
                         'New256 original DOE/neighborhood geometry, split and budget unchanged.',
                         'No target/Q search, physical re-QA, extraction, simulator or training.'])
        path=root/'records'/f'{seq:06d}.json';sha=atomic_json(path,value,immutable=True)
        require(json.loads(path.read_bytes())==value and hashlib.sha256(path.read_bytes()).hexdigest()==sha,
                'production256 append readback failed')
        return dict(status=legacy.STATUS,added=1,path=str(path),sha256=sha,
                    qualification_sequence=seq,split=m['split'],old_broadband_added=0)

