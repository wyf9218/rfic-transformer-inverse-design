"""Incremental research receiver, reusing pinned original owner readers only.

No SSH, native launch, extraction, physical re-QA, or qualification-ledger writes.
One spec invocation. Content-addressed successful receipts prevent reconsumption.
V2 only corrects declaration indexing: history can contain several versions at one
path; every requested read still verifies its exact original pin. A path-only pin
operation is permitted only if its declaration is unambiguous.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime,timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

SCHEMA='eucap15_production256_research_received.v1'
EXPECTED_RUNTIME={
 'controlled_result.py':'eb5036f6f0f830d30e81fb0f118f63999d87e8d90cacd11fa653a555eb354426',
 'controlled_metadata.py':'4ceed4e6e8db4bbf18565ab7f67b14000642c3ea82f446ffc4d8a307f828eaf0',
 'controlled_execution.py':'8718c94a73094422b7714cec6d489584b0df28fa1df465bb252c5d1abfea17b2',
 'native_birth.py':'e269d337f7892f94444efe4004f420dabfe26c85f1a75ad55147fffa913a41f2',
 'start_slots.py':'6c1b8e7c075282462bfd5eff7aafae53b311062f40ce3871806d76cdae274847',
 'native_resource_probe.py':'2b4e525ee3bd75c42a4182333f9e868d67fd2f251e8af16fecb163b9ad0b038c',
 'geometry_helpers.py':'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98',
}
def require(ok,message):
    if not ok:raise ValueError(message)
def sha(data):return hashlib.sha256(data).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return sha(encoded(value))
def pin(path):
    path=Path(path);data=path.read_bytes()
    return dict(path=str(path),sha256=sha(data),bytes=len(data))
def utc():return datetime.now(timezone.utc).isoformat()


def runtime(path):
    path=Path(path)
    for name,expected in EXPECTED_RUNTIME.items():
        p=path/name
        require(not any(x.is_symlink() for x in (p,*p.parents)) and sha(p.read_bytes())==expected,
                'PINNED_RUNTIME_CHANGED: '+name)
    sys.path.insert(0,str(path))
    modules=[importlib.import_module(name) for name in
             ('controlled_metadata','controlled_result','controlled_execution','start_slots')]
    for module in modules:
        require(Path(module.__file__).resolve().parent==path.resolve(),'FOREIGN_MODULE_IMPORT')
    return modules


class MappedReads:
    def __init__(self,metadata,path_map):
        self.original_read=metadata.read_pin
        self.metadata=metadata
        self.path_map=path_map
        self.checked={}
        self.pin_by_path={}
    def register(self,value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256','bytes'}:
                versions=self.pin_by_path.setdefault(value['path'],{})
                versions[(value['sha256'],value['bytes'])]=value
            for child in value.values():self.register(child)
        elif isinstance(value,list):
            for child in value:self.register(child)
    def read(self,identity):
        self.register(identity)
        data=self.original_read(identity,self.path_map)
        self.checked[identity['path']]=dict(original=identity,
            resolved=dict(identity,path=self.path_map.get(identity['path'],identity['path'])))
        return data
    def load(self,identity):
        value=self.metadata.parse(self.read(identity));self.register(value)
        return value
    def pin_original(self,path):
        # Equivalent to native pin(path), but only for a declared bound source.
        # No existence/hash check is removed by replacing an original path.
        key=str(path);require(key in self.pin_by_path,'UNDECLARED_PIN_PATH: '+key)
        versions=self.pin_by_path[key]
        require(len(versions)==1,'AMBIGUOUS_DECLARED_PIN_PATH: '+key)
        identity=next(iter(versions.values()));self.read(identity);return identity


def cache_key(spec,entry):
    return digest(dict(schema=SCHEMA,request_id=entry['request_id'],result=entry['result'],
        manifest=spec['manifest'],execution_release=spec['execution_release'],
        feature_manifest=entry.get('feature_manifest'),runtime=EXPECTED_RUNTIME))


def cached(cache,spec,entry):
    key=cache_key(spec,entry)
    paths=list(cache.glob(key+'-*.json'))
    require(len(paths)<=1,'AMBIGUOUS_CACHED_SUCCESS')
    if not paths:return None
    p=paths[0];data=p.read_bytes();suffix=p.stem[len(key)+1:]
    require(not p.is_symlink() and len(suffix)==64 and sha(data)==suffix,'CACHED_RECEIPT_CORRUPT')
    value=json.loads(data)
    require(value['schema']==SCHEMA and value['status']=='PASS_FROZEN_OWNER_CHAIN_RECEIVED' and
            value['cache_identity']==key and value['source_result']==entry['result'] and
            value['source_runtime']==EXPECTED_RUNTIME,'CACHED_SOURCE_CONFLICT')
    return dict(path=str(p),sha256=sha(data),bytes=len(data),request_id=entry['request_id'],
                status='REUSED_VERIFIED_RECEIPT_NO_CLOSED_SOURCE_READ')


def receive_one(spec,entry,batch,metadata,result,execution,reads):
    from geometry_helpers import canonical_geometry_sha256
    source=reads.load(entry['result'])
    require(source['request_id']==entry['request_id'] and source['original_proposal']==
        batch.candidate(entry['request_id'])['original'],'FROZEN_ROW_OR_REQUEST_CHANGED')
    row=source['original_proposal']
    require(canonical_geometry_sha256(dict(zip(row['geometry_fields'],row['geometry'])))==
            row['canonical_geometry_sha256']==source['candidate_geometry_identity_sha256'],
            'CANONICAL_GEOMETRY_IDENTITY_MISMATCH')
    if source['status']=='ANALYTIC_FAIL_NOT_DISPATCHED':
        computed=result.original_hold(batch,entry['request_id'])
        require(computed==source,'ORIGINAL_ANALYTIC_FALSE_CHANGED')
        return source,dict(feature_manifest=None,feature_csv=None)
    require(source['status']=='FRESH_EMX_EXTRACTED','UNSUPPORTED_TERMINAL_KEEP_UNCONSUMED')
    require(source['execution_release']==spec['execution_release'],'WRONG_ACTIVE_EXECUTION_RELEASE')
    # Original function body and recovery predicates are unchanged. In this
    # single-process read-only call, only pin/document IO is mapped to mirrors.
    with ExitStack() as stack:
        stack.enter_context(patch.object(metadata,'read_pin',lambda identity,*a,**k:reads.read(identity)))
        stack.enter_context(patch.object(result,'read_pin',reads.read))
        stack.enter_context(patch.object(result,'load',reads.load))
        stack.enter_context(patch.object(execution,'document',reads.load))
        stack.enter_context(patch.object(execution,'pin',reads.pin_original))
        computed=result.fresh_candidate(batch,entry['request_id'],
            feature_pin=source['feature'],observation_pin=source['native_observation'],
            plan_pin=source['plan'],execution_release=source['execution_release'])
    require(computed==source,'RESULT_DIFFERS_FROM_ORIGINAL_FRESH_CANDIDATE')
    # Keep the full56 saved-label identity. Do not parse/re-extract S4P or CSV.
    fp=entry['feature_manifest']
    require(Path(fp['path'])==Path(source['feature']['path']).parent/'MANIFEST.json',
            'FOREIGN_FEATURE_MANIFEST')
    manifest=reads.load(fp)
    require(manifest['inputs_unchanged'] is True and source['feature'] in manifest['artifacts'],
            'FEATURE_NOT_BOUND_BY_MANIFEST')
    csv=[p for p in manifest['artifacts'] if Path(p['path']).name=='features_56.csv']
    require(len(csv)==1,'UNIQUE_ORIGINAL56_CSV_REQUIRED')
    for p in manifest['artifacts']:reads.read(p)
    active=reads.load(source['execution_release'])
    require(len(active['endpoint_sources'])==6,'EXACT_SOURCE6_CLOSURE_REQUIRED')
    for item in active['endpoint_sources']:reads.read(item['new'])
    return source,dict(feature_manifest=fp,feature_csv=csv[0])


def consume(spec_pin,out,cache):
    # No output or source consumption starts unless the caller supplied an
    # exact spec pin and a new no-clobber run directory.
    raw=Path(spec_pin['path']).read_bytes()
    require(sha(raw)==spec_pin['sha256'] and len(raw)==spec_pin['bytes'],'SPEC_CHANGED')
    spec=json.loads(raw)
    require(spec['schema']=='eucap15_production256_receive_spec.v1','WRONG_SPEC_SCHEMA')
    out=Path(out).absolute();cache=Path(cache).absolute()
    require(not out.exists() and out!=cache and out not in cache.parents and cache not in out.parents,
            'NEW_RUN_AND_SEPARATE_CACHE_DIRECTORIES_REQUIRED')
    for p in (out,cache):
        require(not any(x.is_symlink() for x in (p,*p.parents)),'RECEIVER_SYMLINK')
    out.mkdir(parents=True,exist_ok=False);cache.mkdir(parents=True,exist_ok=True)
    # Reuse the existing project atomic/lease helper; no new native controller.
    from atomic_primitives import atomic_json,lease
    outcomes=[]
    with lease(cache/'RECEIVER.lock'):
        pending=[]
        require(len({e['request_id'] for e in spec['entries']})==len(spec['entries']),'DUPLICATE_SPEC_REQUEST')
        for entry in spec['entries']:
            prior=cached(cache,spec,entry)
            if prior is not None:outcomes.append(prior)
            else:pending.append(entry)
        if pending:
            metadata,result,execution,slots=runtime(spec['runtime_dir'])
            mapping={}
            for mp in spec['path_map_pins']:
                doc=metadata.parse(metadata.read_pin(mp))
                require(isinstance(doc,dict),'PATH_MAP_OBJECT_REQUIRED')
                mapping.update(doc)
            reads=MappedReads(metadata,mapping)
            # load_batch checks frozen metadata only, once per nonempty delta.
            batch=metadata.load_batch(spec['manifest'],mapping)
            for p in batch.verified_inputs:reads.register(p)
            for entry in pending:
                reads.checked={}
                try:
                    value,full=receive_one(spec,entry,batch,metadata,result,execution,reads)
                    receipt=dict(schema=SCHEMA,status='PASS_FROZEN_OWNER_CHAIN_RECEIVED',utc=utc(),
                        request_id=entry['request_id'],cache_identity=cache_key(spec,entry),
                        receiver=pin(Path(__file__).resolve()),source_runtime=EXPECTED_RUNTIME,
                        source_spec=spec_pin,source_result=entry['result'],frozen_manifest=spec['manifest'],
                        execution_release=spec['execution_release'],verified_result=value,full56=full,
                        source_pins=list(reads.checked.values()),original_split=value['original_proposal']['assigned_development_split'],
                        original_target=value['original_proposal']['target'],original_q_proxy=value['q_proxy'],
                        no_physical_qa_rerun=True,no_label_extraction=True,new_native_starts=0,
                        production_accepted_added=0,global_denominator=256)
                    # Content-addressed exclusive receipt is the durable cache;
                    # the digest is checked on restart, not the closed source.
                    body=json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()+b'\n'
                    cp=cache/(receipt['cache_identity']+'-'+sha(body)+'.json')
                    atomic_json(cp,receipt,immutable=True)
                    require(cp.read_bytes()==body,'CACHE_WRITER_ENCODING_DIFFERENCE')
                    outcome=dict(request_id=entry['request_id'],status='NEW_CLOSED_RESULT_RECEIVED',
                                 receipt=pin(cp),physical_status=value['status'],source_files_read=len(reads.checked))
                except (ValueError,KeyError,OSError) as error:
                    outcome=dict(request_id=entry['request_id'],status='HOLD_NOT_CONSUMED',
                                 error_type=type(error).__name__,error=str(error),source_pins=list(reads.checked.values()))
                atomic_json(out/(sha(entry['request_id'].encode())+'.json'),outcome,immutable=True)
                outcomes.append(outcome)
        receipt=dict(schema='eucap15_production256_receive_delta.v1',utc=utc(),spec=spec_pin,
            outcomes=outcomes,new_received=sum(x['status']=='NEW_CLOSED_RESULT_RECEIVED' for x in outcomes),
            reused=sum(x['status']=='REUSED_VERIFIED_RECEIPT_NO_CLOSED_SOURCE_READ' for x in outcomes),
            held=sum(x['status']=='HOLD_NOT_CONSUMED' for x in outcomes),
            native_actions=0,extraction_calls=0,physical_qa_calls=0,production_accepted_added=0)
        atomic_json(out/'RECEIPT.json',receipt,immutable=True)
    return pin(out/'RECEIPT.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--spec-sha256',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    args=parser.parse_args()
    require(args.spec.is_absolute(),'ABSOLUTE_SPEC_REQUIRED')
    p=pin(args.spec);require(p['sha256']==args.spec_sha256,'SPEC_SHA_MISMATCH')
    print(json.dumps(consume(p,args.out,args.cache)))


if __name__=='__main__':main()
