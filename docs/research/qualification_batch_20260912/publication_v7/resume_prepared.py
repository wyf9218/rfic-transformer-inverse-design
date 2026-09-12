"""Retry a pinned TRAIN002 prepared admission, never prepare/receive/solve again."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

BRIDGE_SHA='7e3325df307cc8c8ce4ad7c2636ceca27d9a6a16fa96495d0bcf7ae3e2dc26e4'
CALLBACK_SHA='97a2b62cd213d85e09b327af4e0bc21cd160973aa3a54cd91af7684567f03201'
FAILED_SHA='6cd2229e955690409411d150879da8b1ad80ad0fc445c85f508d2fb9269ac66b'
BACKFILL_SHA='6d3311547da022b857a756b6341095d76472a705f711ab25c8a00f620f7b5368'
PREPARE_SHA='79c9e93139eb968fffaee1d9387a7a2e37635b031b7f14ca9f0e9f5fd919fcc1'
RECEIVER_SHA='4c6d2e08d3c906ca2b225620045d4d7b015368b0238b8135ea1a2da92b1bcb72'
REQUEST_ID='eucap15_production_doe_neighborhood_20260912_v1-TRAIN_NEIGHBORHOOD-002'
CHILD_SHA='39abe71fab401819e2cf790cf3ef704179246d1bdee052062000cbd3fe1b78c1'
PARENT_SHA='adec27ca2c8152875fdb1eca9c5f8388fc02ae777d7367a19cb05a9229a5e7cc'
UNION=Path('/volumes/research-localdata/ywang3652/eucap15_native_owner_20260909T062500Z/qualified15_single_member_v1')
STATE_NAME='native_candidate_publication_train002_prepared_'+FAILED_SHA[:12]


def require(ok,message):
    if not ok:raise ValueError(message)


def utc():return datetime.now(timezone.utc).isoformat()


def load_pinned(name,path,sha):
    path=Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path,*path.parents)) and
            hashlib.sha256(path.read_bytes()).hexdigest()==sha,'pinned loader/source changed')
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def load_chain(args):
    b=load_pinned('unchanged_prepared_resume_bridge',args.legacy_bridge,BRIDGE_SHA)
    lib,old,prepared,_=b.library(args.library_dir)
    fp=b.pin(args.failed_receipt)
    require(fp['sha256']==args.failed_receipt_sha256==FAILED_SHA,'not the original6cd busy receipt')
    load=lambda p:json.loads(b.read_exact(p))
    failed=load(fp)
    require(failed['schema']=='eucap15_production256_family_split_backfill.v2' and
            failed['request_id']==REQUEST_ID and failed['requested_publish'] is True and
            failed['outcome']['status']=='RETRYABLE_LEDGER_BUSY' and failed['outcome']['added']==0 and
            failed['child_split_preserved']=='validation' and failed['final_independent_test_eligible'] is False and
            failed['native_starts']==0,'not the existing prepared-but-ledger-busy backfill')
    for key,sha in [('source',BACKFILL_SHA),('callback',CALLBACK_SHA),('unchanged_bridge',BRIDGE_SHA),
                    ('unchanged_prepare',PREPARE_SHA)]:
        require(failed[key]['sha256']==sha,'original executed source identity changed: '+key)
        b.read_exact(failed[key])
    c=load_pinned('unchanged97a_prepared_publication',failed['callback']['path'],CALLBACK_SHA)
    require(c.history is old.history and c.legacy is old.legacy,'foreign qualification dependencies')
    pp=failed['preparation'];prep=load(pp)
    require(prep['schema']=='eucap15_production256_qualification_preparation.v1' and
            prep['request_id']==REQUEST_ID and prep['source']['sha256']==PREPARE_SHA and
            prep['callback']['sha256']==CALLBACK_SHA and
            prep['outcome']['status']=='READY_FOR_NATIVE_CURRENT_BYTES_AND_UNION_CHECK' and
            prep['outcome']['split']=='validation' and prep['actual_admissions']==prep['new_native_starts']==0,
            'prepared receipt is not the original ready metadata')
    for key in ('source','callback','read_source_pins'):b.read_exact(prep[key])
    inputs=load(prep['source_inputs'])
    require(load(prep['output_inputs'])==inputs,'prepared input copies differ')
    ep=prep['outcome']['evidence'];e=load(ep);member=e['member'];p=member['original_proposal']
    require(e['schema']==c.EVIDENCE_SCHEMA and e['qualification']==c.QUALIFICATION and
            e['scientific_contract_fingerprint']==c.legacy.FP and e['source_inputs']==inputs and
            e['source_request_id']==member['request_id']==member['candidate_id']==REQUEST_ID and
            member['split']==p['assigned_development_split']=='validation' and
            p['canonical_geometry_sha256']==member['geometry_sha256']==CHILD_SHA and
            p['base_geometry_hash']==PARENT_SHA and p['final_independent_test_eligible'] is False and
            e['identities']==prep['outcome']['identities'] and
            inputs['schema']==c.INPUT_SCHEMA and
            inputs['consumer_receipt']==member['original_consumer_receipt']==failed['original_receiver'] and
            inputs['consumer_receipt']['sha256']==RECEIVER_SHA and
            inputs['current_contract_source']['sha256']==c.CONTRACT_SHA,
            'prepared evidence/request/receiver/family/contract binding changed')
    c.validate_neighborhood_family(p)
    require(len(inputs['runtime_source_pins'])==7 and
            {Path(x['path']).name:x['sha256'] for x in inputs['runtime_source_pins']}==c.RUNTIME_PINS,
            'prepared original seven-runtime identity mismatch')
    rp=failed['original_release'];require(rp['sha256']==b.RELEASE_SHA,'original3d10 release required')
    release=load(rp);require(release['config']['sha256']==b.CONFIG_SHA,'original3d10 config required')
    config=load(release['config']);mapping=config['path_map']
    require(isinstance(mapping,dict) and len(mapping)==9 and
            all(isinstance(k,str) and isinstance(v,str) for k,v in mapping.items()) and
            Path(config['budget_root']).parent/UNION.name==UNION,'original native aliases/union changed')
    for dest in mapping.values():
        require(b.safe_path(dest).is_relative_to(Path(config['budget_root'])/'inputs'),'foreign original alias')
    state=b.safe_path(args.state)
    require(state.parent==UNION.parent and state.name==STATE_NAME and
            not state.is_relative_to(Path(fp['path']).parent),'isolated fixed metadata resume state required')
    binding=dict(schema='eucap15_train002_prepared_resume_binding.v1',source=b.pin(__file__),
        request_id=REQUEST_ID,failed_receipt=fp,prepared_receipt=pp,evidence=ep,
        source_inputs=prep['source_inputs'],original_receiver=failed['original_receiver'],
        original_release=rp,config=release['config'],callback=failed['callback'],
        unchanged_library={name:b.pin(Path(lib)/name) for name in b.LIBRARY},qualified_root=str(UNION))
    return SimpleNamespace(b=b,c=c,evidence=e,evidence_pin=ep,failed_pin=fp,prepared_pin=pp,
        mapping=mapping,union=UNION,state=state,binding=binding,busy=sys.modules['atomic_primitives'].BusyStudy)


def read_commit(ctx,result):
    path=ctx.b.safe_path(result['path'])
    require(path.parent==ctx.union/'records' and path.stem.isdigit(),'commit outside existing record namespace')
    identity=ctx.b.pin(path)
    require(identity['sha256']==result['sha256'],'saved formal commit bytes changed')
    record=json.loads(ctx.b.read_exact(identity))
    require(record['schema']==ctx.c.SCHEMA and record['status']==ctx.c.legacy.STATUS and
            record['record']['request_id']==REQUEST_ID and record['record']['split']=='validation' and
            record['record']['scientific_contract_fingerprint']==ctx.c.legacy.FP and
            record['evidence_digest']==ctx.c.digest(ctx.evidence) and record['evidence']==ctx.evidence,
            'formal readback is not the exact prepared TRAIN002 admission')
    return identity


def attempt(ctx):
    """One shared-lock attempt; ordinary existing worker may retry exit75."""
    for path in (ctx.state,ctx.state/'attempts',ctx.state/'RESUME.lock',
                 ctx.state/'BINDING.json',ctx.state/'SUCCESS.json'):
        ctx.b.safe_path(path)
    ctx.state.mkdir(parents=True,exist_ok=True)
    (ctx.state/'attempts').mkdir(exist_ok=True)
    binding_path=ctx.state/'BINDING.json'
    result=None;completed_pin=None;error=None;publication_attempted=False
    try:
        with ctx.c.lease(ctx.state/'RESUME.lock'):
            if binding_path.exists():
                require(json.loads(ctx.b.read_exact(ctx.b.pin(binding_path)))==ctx.binding,'immutable resume binding conflict')
            else:ctx.c.atomic_json(binding_path,ctx.binding,immutable=True)
            success_path=ctx.state/'SUCCESS.json'
            if success_path.exists():
                saved=json.loads(ctx.b.read_exact(ctx.b.pin(success_path)))
                require(saved['schema']=='eucap15_train002_prepared_resume_success.v1' and
                        saved['binding']==ctx.b.pin(binding_path) and saved['evidence']==ctx.evidence_pin,
                        'saved completion binding changed')
                commit=read_commit(ctx,saved['result'])
                result=dict(status='ALREADY_PROVEN_COMMITTED_NO_COUNT_CHANGE',added=0,
                            path=commit['path'],sha256=commit['sha256'])
                completed_pin=ctx.b.pin(success_path)
            else:
                try:
                    publication_attempted=True
                    result=ctx.c.publish(ctx.union,ctx.evidence,utc(),
                                        reader=lambda p:ctx.b.read_exact(p,ctx.mapping))
                    if result['status'] in (ctx.c.legacy.STATUS,'ALREADY_COMMITTED_NO_COUNT_CHANGE'):
                        read_commit(ctx,result)
                        saved=dict(schema='eucap15_train002_prepared_resume_success.v1',
                            binding=ctx.b.pin(binding_path),evidence=ctx.evidence_pin,result=result)
                        ctx.c.atomic_json(success_path,saved,immutable=True);completed_pin=ctx.b.pin(success_path)
                    else:
                        require(result['status']=='DUPLICATE_CURRENT_QUALIFIED_UNION_NO_COMMIT' and result['added']==0,
                                'unexpected qualification publisher outcome')
                except ctx.busy as exc:
                    result=dict(status='RETRYABLE_LEDGER_BUSY',added=0,error=str(exc))
    except ctx.busy as exc:
        result=dict(status='RETRYABLE_METADATA_BUSY',added=0,error=str(exc))
    except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
        error=exc;known_added=result.get('added') if result is not None else (None if publication_attempted else 0)
        result=dict(status='SHARED_AUTHORITY_OR_READBACK_STOP',added=known_added,
                             error_type=type(exc).__name__,error=str(exc))
    value=dict(schema='eucap15_train002_prepared_resume_attempt.v1',utc=utc(),request_id=REQUEST_ID,
        source=ctx.binding['source'],failed_receipt=ctx.failed_pin,prepared_receipt=ctx.prepared_pin,
        evidence=ctx.evidence_pin,binding=None if not binding_path.exists() else ctx.b.pin(binding_path),
        outcome=result,completion=completed_pin,publication_attempted=publication_attempted,
        receiver_reexecuted=False,preparation_reexecuted=False,
        physical_qa_repeated=False,extraction_repeated=False,new_native_starts=0,old_receipts_overwritten=False)
    target=ctx.state/'attempts'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex+'.json')
    ctx.c.atomic_json(target,value,immutable=True)
    code=75 if result['status'].startswith('RETRYABLE_') else (2 if error is not None else 0)
    return ctx.b.pin(target),value,code


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-bridge','library-dir','failed-receipt','state'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--failed-receipt-sha256',required=True)
    args=parser.parse_args()
    try:
        receipt,value,code=attempt(load_chain(args))
        print(json.dumps(dict(receipt=receipt,status=value['outcome']['status'],
                             outcome=value['outcome'],formally_added=value['outcome'].get('added',0))))
        return code
    except getattr(sys.modules.get('atomic_primitives'),'BusyStudy',BlockingIOError) as exc:
        print(json.dumps(dict(status='RETRYABLE_METADATA_OR_LEDGER_BUSY',error=str(exc))));return 75
    except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
        print(json.dumps(dict(status='PINNED_PREPARED_CHAIN_STOP',error_type=type(exc).__name__,error=str(exc))));return 2


if __name__=='__main__':raise SystemExit(main())
