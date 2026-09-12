"""Thin registered-successor adapter; legacy fixed48 execution is unchanged.

No simulator/controller, candidate generation, physical QA or registry writer.
New batches are admitted only by the sole native owner's frozen registration
validator and metadata reader. Source anchors match the staged native runtime.
"""
import argparse
from contextlib import ExitStack
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

FIXED48_ENTRY_SHA='1a86f380c3f1932d430e916f21b3ca993866cf8ec1a5070e1df40197cd59b4fc'
TRUSTED_SUCCESSOR_RUNTIME={
    'controlled_metadata.py':'1d57e2d1b5f1460037816ebc980f81e6073a3eadfa4c24c1925126303b3c9330',
    'controlled_result.py':'eb5036f6f0f830d30e81fb0f118f63999d87e8d90cacd11fa653a555eb354426',
    'controlled_execution.py':'5f5d1a12b662fa074117de417e5f5e36ea1066e7a74baef4ea2b5043b676b858',
    'native_birth.py':'e269d337f7892f94444efe4004f420dabfe26c85f1a75ad55147fffa913a41f2',
    'start_slots.py':'a6206fc9969e118997003391fd82c8531fab81f0eade99ac48a7e9a12f393ffc',
    'native_resource_probe.py':'2b4e525ee3bd75c42a4182333f9e868d67fd2f251e8af16fecb163b9ad0b038c',
    'geometry_helpers.py':'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98',
}
TRUSTED_REGISTRATION_SHA='8f1e35b08a9229062d5791c1a7b73328827bd22e434b6d0dac18b8048d9a232c'
AMENDMENT_SOURCE_SHA='aa6d854316335aa362b33c76d64e47a5784ba943c7eb3b03505090153f0406c5'
SHARED_RECORDS='/volumes/research-localdata/ywang3652/eucap15_native_owner_20260909T062500Z/qualified15_single_member_v1/records'


def require(ok,message):
    if not ok:raise ValueError(message)


def load_fixed48(path):
    import hashlib
    path=Path(path).absolute()
    require(not any(p.is_symlink() for p in (path,*path.parents)) and
            hashlib.sha256(path.read_bytes()).hexdigest()==FIXED48_ENTRY_SHA,
            'unchanged deployed fixed48 profile adapter required')
    spec=importlib.util.spec_from_file_location('unchanged_fixed48_profile_entry',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def registered_successor(fixed,bridge,args,rp,release):
    """Invoke actual native registration/metadata predicates, never replace them."""
    require(bool(TRUSTED_SUCCESSOR_RUNTIME) and TRUSTED_REGISTRATION_SHA is not None,
            'SUCCESSOR_REGISTRATION_SOURCE_NOT_FROZEN_NO_RUNTIME_IMPORT')
    config=fixed.read(release['config']);root=Path(config['code_root'])
    require('successor_registration' in release and
            release['successor_registration']==config['successor_registration'],
            'successor registration/release/config binding changed')
    sources={p['path']:p for p in release['sources']}
    for name,sha in {**TRUSTED_SUCCESSOR_RUNTIME,'batch_registration.py':TRUSTED_REGISTRATION_SHA,
                     'fixed48_amendment.py':AMENDMENT_SOURCE_SHA}.items():
        identity=sources.get(str(root/name))
        require(identity is not None and identity['sha256']==sha,'untrusted successor source: '+name)
        fixed.bytes_for(identity)
    _,_,_,base=bridge.library(args.library_dir)
    with patch.object(base,'EXPECTED_RUNTIME',TRUSTED_SUCCESSOR_RUNTIME):
        metadata,result,execution,slots=base.runtime(root)
    registration=importlib.import_module('batch_registration')
    require(Path(registration.__file__).resolve().parent==root.resolve(),'foreign registration module import')
    # Existing successor plan_release runs validate_successor_release and returns
    # None: this new batch owns its own plan/release, not the original256 origin.
    require(execution.plan_release(release,config) is None,
            'successor incorrectly reused a parent budget origin')
    selected=registration.registered_profile(config['original_manifest'])
    binding=release['successor_registration'];registered=fixed.read(binding['inputs'])
    registry_path=registration.registry()/(config['original_manifest']['sha256']+'.json')
    require(fixed.pin(registry_path)==binding['inputs'] and
            registered['schema']=='eucap15_registered_successor_inputs.v1' and
            registered['manifest']==config['original_manifest'] and registered['profile']==selected and
            registered['path_map']==config['path_map'],
            'declared registration differs from actual immutable registry entry')
    require(binding['formal_ledger']==SHARED_RECORDS,'foreign shared qualification ledger')
    batch=metadata.load_batch(config['original_manifest'],config['path_map'])
    manifest=metadata.parse(metadata.read_pin(batch.manifest_pin,config['path_map']))
    require(len(batch.rows)==256 and manifest['study_id']==selected['study'] and
            batch.intent_pin['sha256']==selected['intent_sha256'] and
            batch.proposals_pin['sha256']==selected['proposals_sha256'] and
            batch.preparation_pin['sha256']==selected['preparation_sha256'] and
            batch.intent['seeds']==selected['seeds'] and batch.intent['schema']==selected['intent_schema'],
            'registered profile/actual manifest/seed identity mixing')
    require(batch.manifest_pin['sha256']!='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb' and
            selected['study']!='eucap15_production_doe_neighborhood_20260912_v1',
            'successor cannot relabel or reconsume original256')
    return SimpleNamespace(config=config,metadata=metadata,result=result,execution=execution,
        slots=slots,batch=batch,manifest=manifest,registered_profile=selected,
        registration_source=sources[str(root/'batch_registration.py')],registration=registered)


def successor_profile(fixed,rp,release,checked):
    binding=release['successor_registration'];authorities={}
    def collect(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256','bytes'}:authorities[(value['path'],value['sha256'])]=value
            for name in sorted(value):collect(value[name])
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(binding);collect(checked.registration)
    adapter=fixed.pin(__file__);collect(adapter)
    for identity in authorities.values():
        # Registered original input aliases are owner-bound, not a new map.
        path=checked.config['path_map'].get(identity['path'],identity['path'])
        fixed.bytes_for(dict(identity,path=path))
    return dict(kind='FROZEN_SUCCESSOR256',release=rp,config=release['config'],
        parent_release=binding['parent_release'],runtime=copy.deepcopy(TRUSTED_SUCCESSOR_RUNTIME),
        manifest_sha256=checked.batch.manifest_pin['sha256'],intent_sha256=checked.batch.intent_pin['sha256'],
        study=checked.registered_profile['study'],intent_schema=checked.registered_profile['intent_schema'],
        authority_pins=list(authorities.values()),budget_origin_release=rp,
        owner_validator=checked.registration_source,prior_closed_results=[],successor_adapter=adapter,
        registered_profile=copy.deepcopy(checked.registered_profile),registration_inputs=binding['inputs'],
        new_original_denominator=256,old_candidates_reused=0)


def successor_bootstrap(fixed,bridge,args,rp,release,checked,profile):
    """Only actual registered-map and nested-budget layout differ from 7e."""
    lib,c,prepared,base=bridge.library(args.library_dir)
    config=checked.config;owner=bridge.safe_path(config['out'])
    require(Path(rp['path'])==owner/'RELEASE.json','successor release is not at its exact registered owner root')
    mapping=config['path_map']
    require(isinstance(mapping,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in mapping.items()) and
            mapping==checked.registration['path_map'],'registered source-alias mapping changed')
    # Native-owned identity and relocated edges are both valid. Unlike the
    # first Mac-origin batch, the registered successor map has no fixed size.
    for destination in mapping.values():bridge.safe_path(destination)
    cp=bridge.pin(args.contract_inputs)
    require(cp['sha256']==bridge.CONTRACT_SHA,'current scientific contract changed')
    contract=json.loads(bridge.read_exact(cp))['contract']
    require(contract['scientific_fingerprint']==c.legacy.FP and contract['label_frequency_hz']==15000000000 and
            contract['full_frequency_hz']==[n*10**9 for n in range(5,61)],'scientific label contract changed')
    require(config['max_native_concurrency']==48 and config['resource_budget']['cpu_per_solver']==2,
            'registered execution resource contract changed')
    require(checked.execution.plan_release(release,config) is None,'registered successor plan origin changed')
    runtime=[bridge.pin(Path(config['code_root'])/name) for name in c.RUNTIME_PINS]
    require({Path(p['path']).name:p['sha256'] for p in runtime}==c.RUNTIME_PINS,'successor runtime closure changed')
    union=bridge.safe_path(Path(release['successor_registration']['formal_ledger']).parent)
    require(str(union/'records')==SHARED_RECORDS,'shared union is not the existing qualified namespace')
    state=bridge.safe_path(args.state)
    require(state.parent==union.parent and state.name=='native_candidate_publication_fixed48_'+rp['sha256'][:12] and
            state!=union and not state.is_relative_to(owner),'separate release-specific metadata state required')
    ctx=SimpleNamespace(c=c,base=base,prepared=prepared,metadata=checked.metadata,result=checked.result,
        execution=checked.execution,atomic_json=c.atomic_json,lease=c.lease,lib=lib,release=release,
        release_pin=rp,config=config,owner=owner,mapping=mapping,contract=contract,contract_pin=cp,
        runtime=runtime,batch=checked.batch,union=union,state=state,busy=sys.modules['atomic_primitives'].BusyStudy)
    ctx.shared_pins=[rp,release['config'],cp,*runtime,*release['sources'],
        *[x['new'] for x in release['endpoint_sources']],*contract['pins'],*checked.batch.verified_inputs,
        *profile['authority_pins']]
    bridge.check_public(ctx)
    return ctx


def execute(args):
    fixed=load_fixed48(args.fixed48_entry)
    rp=fixed.pin(args.release);release=fixed.read(rp)
    if release.get('successor_registration') is None:
        # The actual 7f1b and original3d10 branches keep their old code, source
        # labels and state. Never attach a successor identity to their results.
        return fixed.execute(args)
    bridge=fixed.load_bridge(args.legacy_bridge)
    checked=registered_successor(fixed,bridge,args,rp,release)
    profile=successor_profile(fixed,rp,release,checked)
    # Keep the deployed scope manager and unchanged callback/reader functions.
    # Only bootstrap's original 9-alias and budget_root.parent assumptions are
    # replaced with the actual validated registration and explicit shared ledger.
    with fixed.parameterized_bridge(bridge,args,profile):
        with patch.object(bridge,'bootstrap',lambda a:successor_bootstrap(fixed,bridge,a,rp,release,checked,profile)):
            ctx=bridge.bootstrap(args)
            return bridge.run(ctx,args.request_id)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('fixed48-entry','legacy-bridge','library-dir','release','contract-inputs','state'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--request-id')
    args=parser.parse_args()
    try:
        receipt,value=execute(args)
        print(json.dumps(dict(receipt=receipt,status=value['status'],formally_added=value['formally_added'])))
        return 0 if value['status']=='CLOSED_TERMINAL_DELTA_PROCESSED' else (75 if value['status']=='RETRYABLE_LEDGER_BUSY' else 2)
    except getattr(sys.modules.get('atomic_primitives'),'BusyStudy',BlockingIOError) as error:
        print(json.dumps(dict(status='RETRYABLE_METADATA_LOCK_BUSY',error=str(error))));return 75
    except (ValueError,KeyError,TypeError,OSError,RuntimeError) as error:
        print(json.dumps(dict(status='SUCCESSOR_REGISTRATION_OR_PUBLIC_AUTHORITY_STOP',
            error_type=type(error).__name__,error=str(error))));return 2


if __name__=='__main__':raise SystemExit(main())
