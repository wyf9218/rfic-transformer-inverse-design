"""Family-safe serial metadata consumer around unchanged owner profile readers.

No scheduling, solver, extraction or registry writes. Existing state and source
files remain immutable. This wrapper records the actual97a callback and itself.
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

SUCCESSOR_SHA='d5ca6452e05ea651ad7906472f361266ff28f6f9b8ad5e0c9a316c0ed3b34b0d'
CALLBACK_SHA='97a2b62cd213d85e09b327af4e0bc21cd160973aa3a54cd91af7684567f03201'
# A new owner recovery source is not admitted from a caller's self-asserted hash.
# Populate only from the owner's actual frozen source inventory, if it changes.
RECOVERY_AMENDMENT_SHA='7204cd57fc70d9e20eb48a44dede2e413dad1fc7ddd475c3a7eb6558fd755e27'
RECOVERY_RUNTIME={"controlled_metadata.py":"1d57e2d1b5f1460037816ebc980f81e6073a3eadfa4c24c1925126303b3c9330","controlled_result.py":"eb5036f6f0f830d30e81fb0f118f63999d87e8d90cacd11fa653a555eb354426","controlled_execution.py":"5f5d1a12b662fa074117de417e5f5e36ea1066e7a74baef4ea2b5043b676b858","native_birth.py":"e269d337f7892f94444efe4004f420dabfe26c85f1a75ad55147fffa913a41f2","start_slots.py":"a6206fc9969e118997003391fd82c8531fab81f0eade99ac48a7e9a12f393ffc","native_resource_probe.py":"2b4e525ee3bd75c42a4182333f9e868d67fd2f251e8af16fecb163b9ad0b038c","geometry_helpers.py":"b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98"}


def require(ok,message):
    if not ok:raise ValueError(message)


def load_pinned(name,path,sha):
    p=Path(path).absolute()
    require('..' not in p.parts and not any(x.is_symlink() for x in (p,*p.parents)),
            'unsafe source path')
    require(hashlib.sha256(p.read_bytes()).hexdigest()==sha,'source identity changed: '+str(p))
    spec=importlib.util.spec_from_file_location(name,p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@contextmanager
def family_library(bridge,args,callback):
    """Keep original seven-library closure; explicitly replace only its callback."""
    original=bridge.library
    lib,old,prepared,base=original(args.library_dir)
    require(callback.history is old.history and callback.legacy is old.legacy,
            'family callback dependencies differ from verified original library')
    def load(path):
        require(Path(path).absolute()==Path(lib).absolute(),'foreign library requested')
        return lib,callback,prepared,base
    with patch.object(bridge,'library',load),patch.object(prepared,'c',callback):
        yield


def recovery_anchors(fixed,release):
    config=fixed.read(release['config']);root=Path(config['code_root'])
    source={p['path']:p for p in release['sources']}.get(str(root/'fixed48_amendment.py'))
    require(source is not None,'amendment source is not bound to actual runtime')
    if source['sha256']==fixed.TRUSTED_AMENDMENT_SOURCE_SHA:
        return fixed.TRUSTED_FIXED48_RUNTIME,fixed.TRUSTED_AMENDMENT_SOURCE_SHA
    require(RECOVERY_AMENDMENT_SHA is not None and RECOVERY_RUNTIME is not None and
            source['sha256']==RECOVERY_AMENDMENT_SHA,
            'OWNER_RECOVERY_VALIDATOR_ANCHOR_NOT_FROZEN_NO_RUNTIME_IMPORT')
    return RECOVERY_RUNTIME,RECOVERY_AMENDMENT_SHA


def owner_validated_profile(fixed,bridge,args,rp,release):
    runtime,amendment_sha=recovery_anchors(fixed,release)
    def after_native_validator(identity,document):
        # construct_owner_profile stores this only after trusted plan_release
        # validated the complete actual chain. Parent identity does not depend
        # on this process having previously cached a profile for that parent.
        profile=copy.deepcopy(fixed.FIXED48_PROFILES[identity['sha256']])
        require(identity==rp==profile['release'] and document==release and
                document['config']==profile['config'] and
                profile['parent_release']==document['concurrency_amendment']['parent_release'],
                'owner-validated profile/actual parent identity mismatch')
        fixed.bytes_for(profile['parent_release'])
        require(profile['kind']=='SCHEDULING_ONLY_FIXED48' and
                profile['manifest_sha256']=='f50721383ef51e5c2c3fa0b7dac67c421d8a9c3e9434600ffaa464c0ef25ebfb' and
                profile['intent_sha256']=='9de9dc38074df0dc1441dafeb9b8b6b1038e71ec40ffa653a1880854b6a51d42' and
                profile['study']=='eucap15_production_doe_neighborhood_20260912_v1' and
                profile['runtime']==runtime,'same-batch recovery changed frozen scientific batch')
        return profile
    with patch.object(fixed,'TRUSTED_FIXED48_RUNTIME',runtime),\
         patch.object(fixed,'TRUSTED_AMENDMENT_SOURCE_SHA',amendment_sha),\
         patch.object(fixed,'select_profile',after_native_validator):
        return fixed.construct_owner_profile(bridge,args,rp,release)


def decorate(fixed,args,profile,callback):
    profile=copy.deepcopy(profile);rp=profile['release']
    expected='native_candidate_publication_family_v3_'+rp['sha256'][:12]
    require(Path(args.state).name==expected,'new release-scoped family_v3 state required')
    pins=[fixed.pin(__file__),fixed.pin(args.successor_entry),fixed.pin(callback.__file__)]
    require(pins[1]['sha256']==SUCCESSOR_SHA and pins[2]['sha256']==CALLBACK_SHA,
            'active family loader/source identity changed')
    profile['authority_pins']=[*profile['authority_pins'],*pins]
    profile['family_publication']=dict(schema='eucap15_family_callback_binding.v3',
        wrapper=pins[0],successor_reader=pins[1],actual_callback=pins[2],
        unchanged_original_library_callback_sha256='8d10bc0e8fa57c272c42a3c97a7e75a58d2efc21e1aafe29e7b1f562bd472905',
        actual_state=str(Path(args.state).absolute()),child_split_policy='FROZEN_CANONICAL_SEED17',
        final_independent_test_eligible_for_train_family=False,
        old_states_untouched=True)
    return profile


def rebind_state(bridge,ctx,args,profile):
    actual=bridge.safe_path(args.state)
    legacy='native_candidate_publication_fixed48_'+profile['release']['sha256'][:12]
    require(ctx.state.name==legacy and actual.parent==ctx.union.parent and
            actual.name=='native_candidate_publication_family_v3_'+profile['release']['sha256'][:12] and
            not actual.is_relative_to(ctx.owner) and actual!=ctx.union,
            'family state must be an isolated sibling of the shared union')
    # The original bootstrap does no state writes. Only its syntactic legacy
    # state-name check uses the compatibility name; all run/lock/cache IO uses
    # this explicit actual path, which is also recorded in profile provenance.
    ctx.state=actual
    ctx.shared_pins.extend(profile['authority_pins'])
    bridge.check_public(ctx)
    return ctx


def execute(args):
    successor=load_pinned('unchanged_successor_v2',args.successor_entry,SUCCESSOR_SHA)
    fixed=successor.load_fixed48(args.fixed48_entry)
    bridge=fixed.load_bridge(args.legacy_bridge)
    # Import the unchanged dependency closure first; the97a callback then shares
    # the same admission/history/atomic modules without changing the old library.
    bridge.library(args.library_dir)
    callback=load_pinned('actual_family_callback_v2',args.family_callback,CALLBACK_SHA)
    rp=fixed.pin(args.release);release=fixed.read(rp)
    require(rp['sha256']!=fixed.OLD_RELEASE_SHA,
            'original3d10 uses isolated backfill_one.py; this is an ordinary48/successor consumer')
    compat=copy.copy(args)
    compat.state=Path(args.state).parent/('native_candidate_publication_fixed48_'+rp['sha256'][:12])
    with family_library(bridge,args,callback):
        checked=None
        if release.get('successor_registration') is not None:
            checked=successor.registered_successor(fixed,bridge,compat,rp,release)
            profile=successor.successor_profile(fixed,rp,release,checked)
        else:
            profile=owner_validated_profile(fixed,bridge,compat,rp,release)
        profile=decorate(fixed,args,profile,callback)
        config=fixed.read(profile['config'])
        require(config['original_manifest']['sha256']==profile['manifest_sha256'] and
                config['max_native_concurrency']==48 and config['resource_budget']['cpu_per_solver']==2,
                'actual owner profile/config/resource identity mismatch')
        with fixed.parameterized_bridge(bridge,compat,profile):
            if checked is not None:
                ctx=successor.successor_bootstrap(fixed,bridge,compat,rp,release,checked,profile)
            else:
                ctx=bridge.bootstrap(compat)
            ctx=rebind_state(bridge,ctx,args,profile)
            return bridge.run(ctx,args.request_id)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('successor-entry','fixed48-entry','legacy-bridge','family-callback','library-dir',
                 'release','contract-inputs','state'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--request-id')
    args=parser.parse_args()
    try:
        receipt,value=execute(args)
        print(json.dumps(dict(receipt=receipt,status=value['status'],formally_added=value['formally_added'])))
        return 0 if value['status']=='CLOSED_TERMINAL_DELTA_PROCESSED' else (75 if value['status']=='RETRYABLE_LEDGER_BUSY' else 2)
    except getattr(sys.modules.get('atomic_primitives'),'BusyStudy',BlockingIOError) as error:
        print(json.dumps(dict(status='RETRYABLE_METADATA_OR_LEDGER_BUSY',error=str(error))));return 75
    except (ValueError,KeyError,TypeError,OSError,RuntimeError) as error:
        print(json.dumps(dict(status='FAMILY_PROFILE_OR_PUBLIC_AUTHORITY_STOP',
            error_type=type(error).__name__,error=str(error))));return 2


if __name__=='__main__':raise SystemExit(main())
