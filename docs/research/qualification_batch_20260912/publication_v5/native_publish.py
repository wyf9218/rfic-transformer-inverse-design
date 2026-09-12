"""One-shot native-side terminal receiver + existing qualified-ledger callback.

No simulator, extraction, training, transport, polling, or source mutation.
The sole native owner invokes this after RESULT.json publication (or on resume).
Only the frozen owner's CONFIG.path_map may resolve immutable source aliases.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

RELEASE_SHA='3d10ca46115b420e0c235ba6df4ddbcc6f84e020670cde66118ceb5e75021257'
CONFIG_SHA='a9f7f97ac9afa965814ad628e047735a41913fe3d891983b54f4ec105f005d52'
CONTRACT_SHA='d47cad4fe145bc096e42db85b903747bf78982691b767924870e72af539faaff'
LIBRARY={
 'production256_publication.py':'8d10bc0e8fa57c272c42a3c97a7e75a58d2efc21e1aafe29e7b1f562bd472905',
 'prepare_callback.py':'79c9e93139eb968fffaee1d9387a7a2e37635b031b7f14ca9f0e9f5fd919fcc1',
 'consume_spec_v2.py':'0b53d6078b8648f34ca7d46652676844118bc73466921a58237259485dc4d196',
 'history_publication.py':'885f794ada737faad7deed9c4cef3bc56ac92d7e7a631fd98c5381eb3b5a2811',
 'admission.py':'75276646b91dc1783e53f79253cde018d557c484d3c4b1171b1a554f82ab0ab3',
 'atomic_primitives.py':'f397cd6390a146c41b2563f10f3430590fc632c2e23fc059468a80ebe36da4ca',
 'geometry_helpers.py':'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98',
}


class PublicEvidenceError(RuntimeError):
    """Shared authority/library/ledger faults stop the entire invocation."""


def require(ok,message):
    if not ok:raise ValueError(message)


def utc():return datetime.now(timezone.utc).isoformat()


def safe_path(path):
    p=Path(path).absolute()
    require('..' not in p.parts and not any(x.is_symlink() for x in (p,*p.parents)), 'unsafe/symlink path')
    return p


def pin(path):
    p=safe_path(path);before=p.stat();raw=p.read_bytes();after=p.stat()
    require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'file changed while reading')
    return dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def read_exact(p,mapping=None):
    path=safe_path((mapping or {}).get(p['path'],p['path']));data=path.read_bytes()
    require(len(data)==p.get('bytes',p.get('size_bytes')) and hashlib.sha256(data).hexdigest()==p['sha256'],
            'source bytes changed: '+p['path'])
    return data


def save_once(ctx,path,value):
    path=safe_path(path)
    if path.exists():
        require(json.loads(path.read_bytes())==value,'immutable local checkpoint conflict: '+str(path))
    else:ctx.atomic_json(path,value,immutable=True)
    return pin(path)


def library(path):
    root=safe_path(path)
    for name,sha in LIBRARY.items():
        if pin(root/name)['sha256']!=sha:raise PublicEvidenceError('pinned library changed: '+name)
    sys.path.insert(0,str(root))
    c=importlib.import_module('production256_publication')
    prepared=importlib.import_module('prepare_callback')
    spec=importlib.util.spec_from_file_location('native_original_receiver',root/'consume_spec_v2.py')
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    for module in (c,prepared,c.history,c.legacy,sys.modules['atomic_primitives'],sys.modules['geometry_helpers']):
        if Path(module.__file__).resolve().parent!=root.resolve():raise PublicEvidenceError('foreign library import')
    return root,c,prepared,base


def bootstrap(args):
    lib,c,prepared,base=library(args.library_dir)
    rp=pin(args.release);require(rp['sha256']==RELEASE_SHA,'wrong active execution release')
    release=json.loads(read_exact(rp));require(release['config']['sha256']==CONFIG_SHA,'wrong active configuration')
    config=json.loads(read_exact(release['config']));owner=safe_path(config['out'])
    require(Path(rp['path'])==owner/'RELEASE.json','release not at original owner root')
    mapping=config['path_map']
    require(isinstance(mapping,dict) and len(mapping)==9 and all(isinstance(k,str) and isinstance(v,str) for k,v in mapping.items()),
            'wrong frozen source-alias mapping')
    for destination in mapping.values():
        require(safe_path(destination).is_relative_to(Path(config['budget_root'])/'inputs'),
                'alias does not resolve to original native input package')
    cp=pin(args.contract_inputs);require(cp['sha256']==CONTRACT_SHA,'wrong current qualification contract')
    contract=json.loads(read_exact(cp))['contract']
    require(contract['scientific_fingerprint']==c.legacy.FP and contract['label_frequency_hz']==15000000000 and
            contract['full_frequency_hz']==[n*10**9 for n in range(5,61)],'wrong scientific contract')
    metadata,result,execution,slots=base.runtime(config['code_root'])
    batch=metadata.load_batch(config['original_manifest'],mapping)
    # This is the existing read-only plan/recovery predicate, not load_execution
    # or a resource probe/native admission. Never call an execution entrypoint.
    execution.plan_release(release,config)
    runtime=[pin(Path(config['code_root'])/name) for name in c.RUNTIME_PINS]
    require({Path(p['path']).name:p['sha256'] for p in runtime}==c.RUNTIME_PINS,'runtime closure mismatch')
    union=safe_path(Path(config['budget_root']).parent/'qualified15_single_member_v1')
    state=safe_path(args.state)
    require(state.parent==union.parent and state.name.startswith('native_candidate_publication_') and
            state!=union and not state.is_relative_to(owner),'dedicated native publication state required')
    ctx=SimpleNamespace(c=c,base=base,prepared=prepared,metadata=metadata,result=result,execution=execution,
        atomic_json=c.atomic_json,lease=c.lease,lib=lib,release=release,release_pin=rp,config=config,owner=owner,
        mapping=mapping,contract=contract,contract_pin=cp,runtime=runtime,batch=batch,union=union,state=state)
    ctx.busy=sys.modules['atomic_primitives'].BusyStudy
    ctx.shared_pins=[rp,release['config'],cp,*runtime,*release['sources'],
                     *[x['new'] for x in release['endpoint_sources']],*contract['pins']]
    check_public(ctx)
    return ctx


def check_public(ctx):
    """No shared source failure may become an individual candidate failure."""
    try:
        for name,sha in LIBRARY.items():
            require(pin(ctx.lib/name)['sha256']==sha,'publication library drift: '+name)
        for p in ctx.shared_pins:read_exact(p,ctx.mapping)
    except (ValueError,KeyError,TypeError,OSError) as error:
        raise PublicEvidenceError(str(error)) from error


def reads_for(ctx,rid):
    candidate=ctx.owner/rid
    class Reads(ctx.base.MappedReads):
        def read(self,identity):
            try:return super().read(identity)
            except (ValueError,KeyError,TypeError,OSError) as error:
                # Actual candidate artifacts may fail independently; shared
                # contract, executable and state metadata faults must stop.
                resolved=Path(ctx.mapping.get(identity.get('path',''),identity.get('path','')))
                if not resolved.is_relative_to(candidate):raise PublicEvidenceError(str(error)) from error
                raise
        def load(self,identity):
            try:return super().load(identity)
            except (ValueError,KeyError,TypeError,OSError) as error:
                resolved=Path(ctx.mapping.get(identity.get('path',''),identity.get('path','')))
                if not resolved.is_relative_to(candidate):raise PublicEvidenceError(str(error)) from error
                raise
    return Reads(ctx.metadata,ctx.mapping)


def existing_submission(ctx,current,row,result_pin):
    """Original two submissions may refer to old research receipt paths."""
    found=[x for x in current if x['value']['record']['request_id']==row['request_id']]
    if not found:return None
    require(len(found)==1,'multiple formal records for request')
    item=found[0];value=item['value'];member=value['evidence']['member'];record=value['record']
    require(value['schema']==ctx.c.SCHEMA and value['evidence']['qualification']==ctx.c.QUALIFICATION and
            record['scientific_contract_fingerprint']==ctx.c.legacy.FP and
            member['original_result']==result_pin and member['original_proposal']==row and
            member['geometry']==row['geometry'] and member['geometry_fields']==row['geometry_fields'] and
            member['geometry_sha256']==row['canonical_geometry_sha256'] and
            member['split']==row['assigned_development_split'] and record['split']==member['split'],
            'existing formal submission conflicts with exact original RESULT/geometry/split/contract')
    return dict(status='ALREADY_FORMALLY_COMMITTED_EXACT_SOURCE',added=0,path=item['path'],sha256=item['sha256'])


def receipt_for(ctx,row,result_pin,work,reads):
    """Delegate original frozen-reader functions; no extraction or physical QA."""
    cp=work/'RECEIVER.json'
    if cp.exists():
        value=json.loads(read_exact(pin(cp)))
        require(value['source_result']==result_pin and value['native_entry']['sha256']==pin(__file__)['sha256'],
                'cached native receiver source conflict')
        return pin(cp),value
    original=reads.load(result_pin)
    require(original['request_id']==row['request_id'] and original['original_proposal']==row,'frozen proposal changed')
    entry=dict(request_id=row['request_id'],result=result_pin)
    if original['status']=='FRESH_EMX_EXTRACTED':
        entry['feature_manifest']=pin(Path(original['feature']['path']).parent/'MANIFEST.json')
    spec=dict(schema='eucap15_production256_receive_spec.v1',entries=[entry],manifest=ctx.batch.manifest_pin,
        execution_release=ctx.release_pin,runtime_dir=ctx.config['code_root'],path_map_pins=[],
        io_mode='NATIVE_SAME_HOST_FROZEN_CONFIG_SOURCE_ALIASES',source_alias_authority=ctx.release['config'])
    sp=save_once(ctx,work/'SPEC.json',spec)
    for p in ctx.batch.verified_inputs:reads.register(p)
    value,full=ctx.base.receive_one(spec,entry,ctx.batch,ctx.metadata,ctx.result,ctx.execution,reads)
    receipt=dict(schema=ctx.base.SCHEMA,status='PASS_FROZEN_OWNER_CHAIN_RECEIVED',utc=utc(),
        request_id=row['request_id'],cache_identity=ctx.base.cache_key(spec,entry),
        receiver=pin(ctx.base.__file__),native_entry=pin(__file__),source_runtime=ctx.base.EXPECTED_RUNTIME,
        source_spec=sp,source_result=result_pin,frozen_manifest=ctx.batch.manifest_pin,execution_release=ctx.release_pin,
        verified_result=value,full56=full,source_pins=list(reads.checked.values()),
        original_split=row['assigned_development_split'],original_target=row['target'],original_q_proxy=value['q_proxy'],
        no_physical_qa_rerun=True,no_label_extraction=True,new_native_starts=0,production_accepted_added=0,
        global_denominator=256,io_mode=spec['io_mode'])
    return save_once(ctx,cp,receipt),receipt


def retained_failure(ctx,row,source,reads):
    status=source['status'];rid=row['request_id']
    with patch.object(ctx.result,'read_pin',reads.read),patch.object(ctx.result,'load',reads.load):
        if status=='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION':
            result=ctx.result.failed_candidate(ctx.batch,rid,error=source['error'],stage_evidence=source['stage_evidence'])
        elif status=='INTAKE_DUPLICATE_NOT_DISPATCHED':
            require(len(source['stage_evidence'])==1,'unique intake receipt required')
            result=ctx.result.intake_hold(ctx.batch,rid,source['stage_evidence'][0])
        elif status in ('NOT_DISPATCHED_DEADLINE','NOT_DISPATCHED_STORAGE_CAP','NOT_DISPATCHED_ARM_START_CAP','EXISTING_SLOT_NO_AUTOMATIC_REDISPATCH'):
            result=ctx.result.budget_hold(ctx.batch,rid,decision_pin=source['budget_decision'],plan_pin=source['plan'])
        else:raise ValueError('unrecognized terminal state retained without qualification: '+status)
    require(result==source,'original failure/hold serialization mismatch')
    return dict(status='ORIGINAL_TERMINAL_RETAINED_NO_QUALIFICATION',reason=source.get('error',status),
                original_status=status,actual_native_starts=source['actual_native_starts'],added=0)


def process_one(ctx,row,current):
    rid=row['request_id'];path=ctx.owner/rid/'RESULT.json'
    if not path.exists():return dict(request_id=rid,status='NO_CLOSED_RESULT_OBSERVED',added=0)
    rp=pin(path);work=ctx.state/'requests'/rid;work.mkdir(parents=True,exist_ok=True)
    op=work/'OUTCOME.json'
    # The current mixed ledger has been verified under its original WRITE.lock.
    # Conflicting existing identity is an authority fault, never an individual skip.
    try:existing=existing_submission(ctx,current,row,rp)
    except (ValueError,KeyError,TypeError) as error:raise PublicEvidenceError(str(error)) from error
    if op.exists():
        prior=json.loads(read_exact(pin(op)))
        require(prior['source_result']==rp and prior['source']==pin(__file__),'terminal RESULT/source changed after checkpoint')
        if prior.get('added')==1 or prior['status'].startswith('ALREADY_FORMALLY_COMMITTED'):
            if existing is None or (prior.get('path'),prior.get('sha256'))!=(existing['path'],existing['sha256']):
                raise PublicEvidenceError('committed checkpoint is absent/conflicting in current qualified ledger')
        return dict(request_id=rid,status='REUSED_TERMINAL_CHECKPOINT',added=0,checkpoint=pin(op),prior_status=prior['status'])
    if existing is not None:
        result=existing
    else:
        reads=reads_for(ctx,rid)
        try:
            source=reads.load(rp)
            if source['status'] not in ('FRESH_EMX_EXTRACTED','ANALYTIC_FAIL_NOT_DISPATCHED'):
                result=retained_failure(ctx,row,source,reads)
            else:
                cp,received=receipt_for(ctx,row,rp,work,reads)
                source=received['verified_result']
                if source['status']=='ANALYTIC_FAIL_NOT_DISPATCHED':
                    result=dict(status='ORIGINAL_ANALYTIC_FAILURE_RETAINED',added=0,actual_native_starts=0)
                elif source['core15_eligible'] is not True or source['valid_for_strict_comparison'] is not True:
                    feature=reads.load(source['feature'])
                    result=dict(status='FRESH_NOT_STRICT_CORE_NO_QUALIFICATION',added=0,
                        core15_eligible=source['core15_eligible'],strict_valid=source['valid_for_strict_comparison'],
                        original_frequency_row=feature['original_frequency_row'],actual_native_starts=source['actual_native_starts'],
                        reason='Original saved strict/core predicates not both true; no clipping, substitution or extra Q cut.')
                else:
                    inputs=dict(schema=ctx.c.INPUT_SCHEMA,consumer_receipt=cp,current_contract_source=ctx.contract_pin,
                                runtime_source_pins=ctx.runtime)
                    evidence=ctx.c.build_evidence(inputs,rid,reader=reads.read)
                    save_once(ctx,work/'EVIDENCE.json',evidence)
                    check_public(ctx)
                    try:result=ctx.c.publish(ctx.union,evidence,utc(),reader=reads.read)
                    except ctx.busy:raise
                    except (ValueError,KeyError,TypeError,OSError) as error:
                        # The shared union/rebuild/append is an authority boundary;
                        # never reclassify its failure as a candidate phenotype.
                        raise PublicEvidenceError('shared publication: '+str(error)) from error
        except ctx.busy:
            return dict(request_id=rid,status='RETRYABLE_LEDGER_BUSY',added=0,source_result=rp)
        except (ValueError,KeyError,TypeError,OSError) as error:
            result=dict(status='CANDIDATE_EVIDENCE_HOLD_NO_QUALIFICATION',added=0,error_type=type(error).__name__,error=str(error))
    outcome=dict(request_id=rid,source_result=rp,source=pin(__file__),utc=utc(),
        physical_qa_calls=0,extraction_calls=0,new_native_starts=0,**result)
    if result['status']=='CANDIDATE_EVIDENCE_HOLD_NO_QUALIFICATION':
        # A subsequently restored missing artifact can be retried at the next
        # normal invocation. Preserve each attempt; do not freeze a false FAIL.
        name='HOLD_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'
        save_once(ctx,work/name,outcome)
    else:save_once(ctx,op,outcome)
    return outcome


def run(ctx,request_id=None):
    ctx.state.mkdir(parents=True,exist_ok=True)
    (ctx.state/'requests').mkdir(exist_ok=True);(ctx.state/'runs').mkdir(exist_ok=True)
    binding=dict(schema='eucap15_native_terminal_publication_binding.v1',source=pin(__file__),
        execution_release=ctx.release_pin,contract=ctx.contract_pin,qualified_root=str(ctx.union),
        library={k:pin(ctx.lib/k) for k in LIBRARY},source_alias_authority=ctx.release['config'])
    outcomes=[]
    with ctx.lease(ctx.state/'RECEIVER.lock'):
        save_once(ctx,ctx.state/'BINDING.json',binding)
        try:
            check_public(ctx)
            with ctx.lease(ctx.union/'WRITE.lock'):current=ctx.c.history.ledger(ctx.union)
            rows=[row for row in ctx.batch.rows if request_id is None or row['request_id']==request_id]
            require(rows and (request_id is None or len(rows)==1),'unknown requested frozen candidate')
            for row in rows:outcomes.append(process_one(ctx,row,current))
            status='RETRYABLE_LEDGER_BUSY' if any(x['status']=='RETRYABLE_LEDGER_BUSY' for x in outcomes) else 'CLOSED_TERMINAL_DELTA_PROCESSED'
            if status=='RETRYABLE_LEDGER_BUSY':
                fault=dict(error_type='BusyStudy',error='One or more immutable candidate receipts await the original WRITE.lock.')
        except ctx.busy as error:
            status='RETRYABLE_LEDGER_BUSY'
            fault=dict(error_type=type(error).__name__,error=str(error))
        except (PublicEvidenceError,ValueError,KeyError,TypeError,OSError) as error:
            status='STOPPED_SHARED_AUTHORITY_OR_CHECKPOINT_FAULT'
            fault=dict(error_type=type(error).__name__,error=str(error))
        receipt=dict(schema='eucap15_native_terminal_publication_delta.v1',utc=utc(),status=status,
            outcomes=outcomes,formally_added=sum(x.get('added',0) for x in outcomes),
            candidate_count=len(outcomes),native_actions=0,physical_qa_calls=0,extraction_calls=0,
            training_actions=0,old_broadband_added=0,no_local_transport_dependency=True,
            source_aliases='ONLY_ORIGINAL_NATIVE_RELEASE_BOUND_CONFIG_PATH_MAP',automatic_polling_installed=False)
        if status!='CLOSED_TERMINAL_DELTA_PROCESSED':receipt['fault']=fault
        name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'
        return save_once(ctx,ctx.state/'runs'/name,receipt),receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library-dir',type=Path,required=True)
    p.add_argument('--release',type=Path,required=True)
    p.add_argument('--contract-inputs',type=Path,required=True)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--request-id')
    args=p.parse_args()
    try:
        ctx=bootstrap(args);receipt,value=run(ctx,args.request_id)
        print(json.dumps(dict(receipt=receipt,status=value['status'],formally_added=value['formally_added'])))
        return 0 if value['status']=='CLOSED_TERMINAL_DELTA_PROCESSED' else (75 if value['status']=='RETRYABLE_LEDGER_BUSY' else 2)
    except getattr(sys.modules.get('atomic_primitives'), 'BusyStudy', BlockingIOError) as error:
        print(json.dumps(dict(status='RETRYABLE_RECEIVER_OR_LEDGER_BUSY',error=str(error))))
        return 75
    except (PublicEvidenceError,ValueError,KeyError,TypeError,OSError) as error:
        print(json.dumps(dict(status='PUBLIC_PREFLIGHT_FAILED_NO_CANDIDATE_PROCESSING',error_type=type(error).__name__,error=str(error))))
        return 2


if __name__=='__main__':raise SystemExit(main())
