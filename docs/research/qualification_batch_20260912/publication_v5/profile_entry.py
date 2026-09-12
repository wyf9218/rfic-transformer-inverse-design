"""Serial profile adapter around the unchanged native publication bridge.

No solver, scheduler, extraction, source rewriting or arbitrary SHA admission.
The native owner freezes execution releases. This file only recognizes the
actual registered release/runtime tuple; an absent new profile fails closed.
"""
import argparse
from contextlib import contextmanager,ExitStack
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

BRIDGE_SHA='7e3325df307cc8c8ce4ad7c2636ceca27d9a6a16fa96495d0bcf7ae3e2dc26e4'
OLD_RELEASE_SHA='3d10ca46115b420e0c235ba6df4ddbcc6f84e020670cde66118ceb5e75021257'
# Derived at runtime only after the frozen owner validator verifies the actual
# release. No CLI accepts a caller-supplied authority SHA or an arbitrary tuple.
FIXED48_PROFILES={}
# Exact 8 source pins checked against the owner's final STAGE_RECEIPT inventory
# and local bytes, 2026-09-12. These are source trust anchors, not per-release
# manual approval. Physics/data predicates remain in the existing readers.
TRUSTED_FIXED48_RUNTIME={
    'controlled_metadata.py':'4ceed4e6e8db4bbf18565ab7f67b14000642c3ea82f446ffc4d8a307f828eaf0',
    'controlled_result.py':'eb5036f6f0f830d30e81fb0f118f63999d87e8d90cacd11fa653a555eb354426',
    'controlled_execution.py':'294279ba9dd99b9dcbf2bf6cd336e787bfe034c30f3b78865720b9b5d8ab9ac0',
    'native_birth.py':'e269d337f7892f94444efe4004f420dabfe26c85f1a75ad55147fffa913a41f2',
    'start_slots.py':'6c1b8e7c075282462bfd5eff7aafae53b311062f40ce3871806d76cdae274847',
    'native_resource_probe.py':'2b4e525ee3bd75c42a4182333f9e868d67fd2f251e8af16fecb163b9ad0b038c',
    'geometry_helpers.py':'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98',
}
TRUSTED_AMENDMENT_SOURCE_SHA='aa6d854316335aa362b33c76d64e47a5784ba943c7eb3b03505090153f0406c5'


def require(ok,message):
    if not ok:raise ValueError(message)


def pin(path):
    path=Path(path).absolute()
    require(not any(p.is_symlink() for p in (path,*path.parents)) and '..' not in path.parts,'unsafe source path')
    raw=path.read_bytes()
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


def bytes_for(identity):
    path=Path(identity['path']).absolute()
    require(not any(p.is_symlink() for p in (path,*path.parents)) and '..' not in path.parts,'unsafe source path')
    raw=path.read_bytes()
    require(dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))==identity,
            'source pin changed: '+identity['path'])
    return raw


def read(identity):return json.loads(bytes_for(identity))


def load_bridge(path):
    require(pin(path)['sha256']==BRIDGE_SHA,'unchanged original bridge required')
    spec=importlib.util.spec_from_file_location('unchanged_native_publication_bridge',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def select_profile(release_pin,release):
    """Exact declared tuple, not architecture/name/number-of-threads matching."""
    require(release_pin['sha256'] in FIXED48_PROFILES,
            'UNSUPPORTED_OR_NOT_FROZEN_RELEASE_PROFILE_NO_CANDIDATE_CONSUMPTION')
    profile=copy.deepcopy(FIXED48_PROFILES[release_pin['sha256']])
    require(profile['release']==release_pin and release['config']==profile['config'],
            'registered release/config identity mismatch')
    require(profile['kind'] in ('SCHEDULING_ONLY_FIXED48','FROZEN_SUCCESSOR256') and
            profile['parent_release']['sha256'] in (OLD_RELEASE_SHA,*FIXED48_PROFILES),
            'wrong registered native-owner lineage')
    require(set(profile['runtime'])=={'controlled_metadata.py','controlled_result.py','controlled_execution.py',
        'native_birth.py','start_slots.py','native_resource_probe.py','geometry_helpers.py'},'incomplete runtime profile')
    if profile['kind']=='SCHEDULING_ONLY_FIXED48':
        require(profile['manifest_sha256']=='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb' and
            profile['intent_sha256']=='9de9dc38074df0dc1441dafeb9b8b6b1038e71ec40ffa653a1880854b6a51d42' and
            profile['study']=='eucap15_production_doe_neighborhood_20260912_v1',
            'scheduling-only amendment cannot introduce another dataset')
    else:
        # A later batch is a separately registered tuple with its actual frozen
        # metadata-reader source, not a caller renaming the current256 inputs.
        require(profile['manifest_sha256']!='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb' and
                profile['study']!='eucap15_production_doe_neighborhood_20260912_v1',
                'successor must have its own immutable dataset identity')
    return profile


def profile_evidence(profile):
    return dict(schema='eucap15_native_reader_runtime_profile.v1',adapter=pin(__file__),
        profile=copy.deepcopy(profile),scientific_contract_unchanged=True,
        scope='SERIAL_METADATA_ONLY_EXISTING_OWNER_RELEASE_NO_NATIVE_DISPATCH')


def construct_owner_profile(bridge,args,rp,release):
    """Use the sole owner's existing plan_release validator, not a new gate."""
    require(bool(TRUSTED_FIXED48_RUNTIME) and TRUSTED_AMENDMENT_SOURCE_SHA is not None,
            'NATIVE_FIXED48_VALIDATOR_NOT_FROZEN_NO_RUNTIME_IMPORT')
    config=read(release['config'])
    amendment=release['concurrency_amendment']
    require(isinstance(amendment,dict) and config['concurrency_amendment']==amendment,
            'embedded amendment/config binding changed')
    root=Path(config['code_root'])
    sources={p['path']:p for p in release['sources']}
    for name,sha in {**TRUSTED_FIXED48_RUNTIME,'fixed48_amendment.py':TRUSTED_AMENDMENT_SOURCE_SHA}.items():
        identity=sources.get(str(root/name))
        require(identity is not None and identity['sha256']==sha,'untrusted native validation source: '+name)
        bytes_for(identity)
    _,_,_,base=bridge.library(args.library_dir)
    with patch.object(base,'EXPECTED_RUNTIME',TRUSTED_FIXED48_RUNTIME):
        metadata,_,execution,_=base.runtime(root)
    # This existing owner function validates source replacements, enumerated
    # scheduling-only deltas, handoff and original budget origin. No dispatch,
    # native resource probe, solver or independent validator is invoked here.
    origin=execution.plan_release(release,config)
    require(origin is not None,'owner amendment did not bind an original plan origin')
    batch=metadata.load_batch(config['original_manifest'],config['path_map'])
    require(len(batch.rows)==256,'this adapter preserves the existing256 protocol')
    manifest=json.loads(metadata.read_pin(batch.manifest_pin,config['path_map']))
    authorities={}
    def collect(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256','bytes'}:
                authorities[(value['path'],value['sha256'])]=value
            for name in sorted(value):collect(value[name])
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(amendment)
    for identity in authorities.values():bytes_for(identity)
    profile=dict(kind='SCHEDULING_ONLY_FIXED48' if batch.manifest_pin['sha256']==
        'f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb' else 'FROZEN_SUCCESSOR256',
        release=rp,config=release['config'],parent_release=amendment['parent_release'],
        runtime=copy.deepcopy(TRUSTED_FIXED48_RUNTIME),manifest_sha256=batch.manifest_pin['sha256'],
        intent_sha256=batch.intent_pin['sha256'],study=manifest['study_id'],
        authority_pins=list(authorities.values()),budget_origin_release=origin,
        owner_validator=sources[str(root/'fixed48_amendment.py')],
        prior_closed_results=copy.deepcopy(amendment['prior_results']))
    # A profile is derived only after the existing trusted owner validator and
    # actual frozen metadata reader succeeded; no CLI accepts authority SHAs.
    FIXED48_PROFILES[rp['sha256']]=copy.deepcopy(profile)
    return select_profile(rp,release)


@contextmanager
def parameterized_bridge(bridge,args,profile):
    """Scoped constants only; original predicates and function bodies remain."""
    lib,c,prepared,base=bridge.library(args.library_dir)
    receipt_profile=profile_evidence(profile)
    expected_state='native_candidate_publication_fixed48_'+profile['release']['sha256'][:12]
    require(Path(args.state).name==expected_state,'new release requires its own immutable receiver state')
    original_save=bridge.save_once;original_build=c.build_evidence;original_process=bridge.process_one

    def process(ctx,row,current):
        prior=[identity for identity in profile['prior_closed_results']
               if Path(identity['path']).parent.name==row['request_id']]
        if prior:
            require(len(prior)==1 and str(Path(prior[0]['path']).parent)==ctx.config['candidate_roots'][row['request_id']],
                    'ambiguous prior-release closed candidate binding')
            return dict(request_id=row['request_id'],status='PRIOR_RELEASE_CLOSED_RETAINED_NOT_RECONSUMED',
                added=0,original_result=prior[0],formal_membership_not_reassessed=True,
                next_reader='UNCHANGED_PARENT_RELEASE_BRIDGE_AND_ITS_EXISTING_STATE')
        return original_process(ctx,row,current)

    def save(ctx,path,value):
        if Path(path).name in ('BINDING.json','RECEIVER.json') or value.get('schema')=='eucap15_native_terminal_publication_delta.v1':
            value['runtime_profile']=copy.deepcopy(receipt_profile)
        return original_save(ctx,path,value)

    def build(inputs,rid,*,reader=c.raw):
        inputs=copy.deepcopy(inputs)
        if 'native_runtime_profile' in inputs:
            require(inputs['native_runtime_profile']==receipt_profile,'foreign qualification profile')
        else:inputs['native_runtime_profile']=copy.deepcopy(receipt_profile)
        cached=json.loads(reader(c.history._normalized_pin(inputs['consumer_receipt'])))
        require(cached.get('runtime_profile')==receipt_profile and
                cached['execution_release']==profile['release'] and
                cached['source_runtime']==profile['runtime'],
                'receiver/profile/release mixing forbidden')
        reader(pin(__file__))
        return original_build(inputs,rid,reader=reader)

    with ExitStack() as stack:
        for module,name,value in (
            (bridge,'RELEASE_SHA',profile['release']['sha256']),
            (bridge,'CONFIG_SHA',profile['config']['sha256']),
            (base,'EXPECTED_RUNTIME',profile['runtime']),
            (c,'RUNTIME_PINS',profile['runtime']),
            (c,'RELEASE_SHA',profile['release']['sha256']),
            (c,'MANIFEST_SHA',profile['manifest_sha256']),
            (c,'INTENT_SHA',profile['intent_sha256']),
            (c,'STUDY',profile['study']),
            (bridge,'library',lambda p:(lib,c,prepared,base)),
            (bridge,'save_once',save),(bridge,'process_one',process),(c,'build_evidence',build)):
            stack.enter_context(patch.object(module,name,value))
        yield bridge


def execute(args):
    bridge=load_bridge(args.legacy_bridge)
    rp=pin(args.release);release=read(rp)
    if rp['sha256']==OLD_RELEASE_SHA:
        # Preserve original state/cache identity, original reader and all old
        # constants. No new profile is retroactively attached to old evidence.
        ctx=bridge.bootstrap(args)
        return bridge.run(ctx,args.request_id)
    profile=select_profile(rp,release) if rp['sha256'] in FIXED48_PROFILES else construct_owner_profile(bridge,args,rp,release)
    # The exact owner profile and amendment documents must exist before new
    # runtime code is imported; the owner's plan_release validates its own
    # dispatch handoff. This adapter never implements that control predicate.
    for identity in profile['authority_pins']:bytes_for(identity)
    config=read(profile['config'])
    require(config['original_manifest']['sha256']==profile['manifest_sha256'] and
            config['max_native_concurrency']==48 and config['resource_budget']['cpu_per_solver']==2,
            'actual registered config/profile mismatch')
    source_by_name={Path(p['path']).name:p for p in release['sources']
        if Path(p['path']).parent==Path(config['code_root'])}
    for name,sha in profile['runtime'].items():
        require(name in source_by_name and source_by_name[name]['sha256']==sha and
                pin(source_by_name[name]['path'])==source_by_name[name], 'unbound runtime: '+name)
    with parameterized_bridge(bridge,args,profile):
        ctx=bridge.bootstrap(args)
        # Existing bridge validates CONFIG, physical contract and full source
        # closure. Keep the amendment/profile authority in all future rechecks.
        ctx.shared_pins.extend(profile['authority_pins'])
        return bridge.run(ctx,args.request_id)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-bridge','library-dir','release','contract-inputs','state'):
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
        print(json.dumps(dict(status='PROFILE_OR_PUBLIC_AUTHORITY_STOP_NO_NATIVE_ACTION',
            error_type=type(error).__name__,error=str(error))));return 2


if __name__=='__main__':raise SystemExit(main())
