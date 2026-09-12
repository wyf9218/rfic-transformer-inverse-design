"""Failure-only extension of the existing production256 receiver.

No native work, retry, physics, extraction or successful-result consumption.
Reuses the exact original failed_candidate function and pinned v2 mapping IO.
Original actual_native_starts=None is retained; wrapper exit is not EMX birth.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import types

BASE_SHA='0b53d6078b8648f34ca7d46652676844118bc73466921a58237259485dc4d196'
STATUS='FAILED_TERMINAL_METADATA_RECEIVED_NOT_FULL_PHYSICAL_VERIFICATION'
MANIFEST_SHA='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb'
RELEASE_SHA='3d10ca46115b420e0c235ba6df4ddbcc6f84e020670cde66118ceb5e75021257'


def require(ok,message):
    if not ok:raise ValueError(message)


def load_base(path):
    path=Path(path)
    require(hashlib.sha256(path.read_bytes()).hexdigest()==BASE_SHA,'wrong original receiver source')
    spec=importlib.util.spec_from_file_location('production256_original_receiver_v2',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def receive_failure(spec,entry,batch,result,reads):
    """Only replay original failure serialization, then verify its stage metadata."""
    source=reads.load(entry['result']);rid=entry['request_id'];root=Path(entry['result']['path']).parent
    require(source['status']=='CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION' and
            source['request_id']==rid and source['original_proposal']==batch.candidate(rid)['original'],
            'not the exact frozen failed candidate')
    require(source['controlled_manifest']==batch.manifest_pin and
            batch.manifest_pin['sha256']==MANIFEST_SHA and spec['execution_release']['sha256']==RELEASE_SHA,
            'foreign frozen batch or execution release')
    require(source['stage_evidence'],'failure needs bound process evidence')
    env=dict(result.failed_candidate.__globals__);env['read_pin']=reads.read
    replay=types.FunctionType(result.failed_candidate.__code__,env)(
        batch,rid,error=source['error'],stage_evidence=source['stage_evidence'])
    require(replay==source,'failure RESULT differs from original failed_candidate')
    match=re.fullmatch(r'STAGE_EXECUTION_FAILED: ([a-z_]+); (.+)',source['error'])
    require(match is not None,'unrecognized failure error; keep metadata unconsumed')
    stage,error_log=match.groups();stages=[];seen=set()
    for identity in source['stage_evidence']:
        pp=Path(identity['path'])
        require(pp.parent==root and pp.name.endswith('_PROCESS.json'),'foreign process evidence')
        name=pp.name.removesuffix('_PROCESS.json')
        require(name in ('cadence','gds_audit','calibre','emx') and name not in seen,'unknown/duplicate process stage')
        seen.add(name);p=reads.load(identity)
        require(p['intent']['release']==spec['execution_release'] and type(p['returncode']) is int and
                Path(p['intent']['output']).is_relative_to(root) and
                Path(p['log']['path']).parent==root and
                Path(p['log']['path']).name.startswith(name+'_'),'stage release/output/log mismatch')
        log=reads.read(p['log']).decode('utf-8',errors='replace')
        if p['completion'] is not None:
            require(p['completion']['path']==p['intent']['completion'] and
                    Path(p['completion']['path']).is_relative_to(root),'foreign completion metadata')
            reads.read(p['completion'])
        stages.append(dict(stage=name,process=identity,returncode=p['returncode'],utc=p['utc'],
                           log=p['log'],completion=p['completion']))
        if name==stage:require(p['log']['path']==error_log,'error log not bound to failed process')
    failures=[p for p in stages if p['returncode']!=0]
    require(len(failures)==1 and failures[0]['stage']==stage,'stage failure not uniquely evidenced')
    return dict(schema='eucap15_production256_failed_received.v1',status=STATUS,request_id=rid,
        source_result=entry['result'],verified_original_result=source,failed_stage=stage,stages=stages,
        original_denominator=256,actual_native_starts=source['actual_native_starts'],
        native_start_evidence_scope='UNKNOWN_IN_ORIGINAL_RESULT_NOT_INFERRED_FROM_WRAPPER_EXIT',
        strict_qualification=None,actual_response=None,new_native_starts=0,physical_qa_calls=0,
        extraction_calls=0,production_accepted_added=0,automatic_retry_allowed=False,
        original_geometry_target_q_failure_unchanged=True)


def consume(spec_pin,out):
    base=load_base(Path(__file__).resolve().parent.parent/'production256_research_receiver_v1/consume_spec_v2.py')
    raw=Path(spec_pin['path']).read_bytes()
    require(hashlib.sha256(raw).hexdigest()==spec_pin['sha256'] and len(raw)==spec_pin['bytes'],'failure spec changed')
    spec=json.loads(raw)
    require(spec['schema']=='eucap15_production256_failed_receive_spec.v1' and
            len({e['request_id'] for e in spec['entries']})==len(spec['entries']),'wrong failure-only spec')
    out=Path(out).absolute();require(not out.exists(),'new no-clobber output required')
    metadata,result,execution,slots=base.runtime(spec['runtime_dir'])
    mapping={}
    for mp in spec['path_map_pins']:mapping.update(json.loads(metadata.read_pin(mp)))
    reads=base.MappedReads(metadata,mapping);batch=metadata.load_batch(spec['manifest'],mapping)
    out.mkdir(parents=True,exist_ok=False)
    from atomic_primitives import atomic_json
    outcomes=[]
    for entry in spec['entries']:
        reads.checked={}
        try:
            value=receive_failure(spec,entry,batch,result,reads)
            value.update(source=base.pin(__file__),original_receiver_sha256=BASE_SHA,
                         original_runtime=base.EXPECTED_RUNTIME,source_spec=spec_pin,
                         source_pins=list(reads.checked.values()))
            path=out/(entry['request_id']+'.json');atomic_json(path,value,immutable=True)
            outcomes.append(dict(request_id=entry['request_id'],status=STATUS,receipt=base.pin(path),
                                 failed_stage=value['failed_stage'],actual_native_starts=value['actual_native_starts']))
        except (ValueError,KeyError,OSError,TypeError) as error:
            outcomes.append(dict(request_id=entry['request_id'],status='HOLD_FAILURE_NOT_CONSUMED',
                error_type=type(error).__name__,error=str(error),source_pins=list(reads.checked.values())))
    summary=dict(schema='eucap15_production256_failure_receive_delta.v1',utc=base.utc(),source=base.pin(__file__),
        spec=spec_pin,outcomes=outcomes,received=sum(o['status']==STATUS for o in outcomes),
        full_physical_verification=False,new_native_starts=0,physical_qa_calls=0,extraction_calls=0,
        production_accepted_added=0,successful_fresh_results_reconsumed=0)
    atomic_json(out/'RECEIPT.json',summary,immutable=True)
    return base.pin(out/'RECEIPT.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();b=a.spec.read_bytes()
    print(json.dumps(consume(dict(path=str(a.spec.absolute()),sha256=a.sha256,bytes=len(b)),a.out)))
