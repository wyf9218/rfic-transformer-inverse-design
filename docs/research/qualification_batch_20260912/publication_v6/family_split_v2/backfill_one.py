"""One existing TRAIN_NEIGHBORHOOD-002 receipt: prepare or owner-only append.

No receiver replay, native execution, extraction or physics QA. Original HOLD
and frozen proposals remain untouched; outputs always use a fresh directory.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

BRIDGE_SHA='7e3325df307cc8c8ce4ad7c2636ceca27d9a6a16fa96495d0bcf7ae3e2dc26e4'
CALLBACK_SHA='97a2b62cd213d85e09b327af4e0bc21cd160973aa3a54cd91af7684567f03201'
REQUEST_ID='eucap15_production_doe_neighborhood_20260912_v1-TRAIN_NEIGHBORHOOD-002'
CHILD_SHA='39abe71fab401819e2cf790cf3ef704179246d1bdee052062000cbd3fe1b78c1'
PARENT_SHA='adec27ca2c8152875fdb1eca9c5f8388fc02ae777d7367a19cb05a9229a5e7cc'
UNION=Path('/volumes/research-localdata/ywang3652/eucap15_native_owner_20260909T062500Z/qualified15_single_member_v1')


def load_pinned(name,path,sha):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        raise ValueError('source identity changed: '+str(path))
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def execute(args):
    b=load_pinned('unchanged_native_bridge',args.legacy_bridge,BRIDGE_SHA)
    _,old,prepared,_=b.library(args.library_dir)
    c=load_pinned('family_split_callback_v2',Path(__file__).parent/'production256_publication.py',CALLBACK_SHA)
    b.require(c.history is old.history and c.legacy is old.legacy,'foreign qualification dependencies')
    rp=b.pin(args.release);b.require(rp['sha256']==b.RELEASE_SHA,'original3d10 release only')
    release=json.loads(b.read_exact(rp))
    b.require(release['config']['sha256']==b.CONFIG_SHA,'original3d10 config only')
    config=json.loads(b.read_exact(release['config']));mapping=config['path_map']
    b.require(isinstance(mapping,dict) and len(mapping)==9 and
              all(isinstance(k,str) and isinstance(v,str) for k,v in mapping.items()),'original alias map changed')
    b.require(str(UNION)==str(Path(config['budget_root']).parent/'qualified15_single_member_v1'),
              'foreign formal union')
    for dest in mapping.values():
        b.require(b.safe_path(dest).is_relative_to(Path(config['budget_root'])/'inputs'),'foreign original alias destination')
    cp=b.pin(args.contract_inputs);b.require(cp['sha256']==c.CONTRACT_SHA,'current d47 contract changed')
    receiver=b.pin(args.receiver);b.require(receiver['sha256']==args.receiver_sha256,'accepted receiver changed')
    saved=json.loads(b.read_exact(receiver));p=saved['verified_result']['original_proposal']
    b.require(saved['request_id']==REQUEST_ID and p['request_id']==REQUEST_ID and
              p['canonical_geometry_sha256']==CHILD_SHA and p['base_geometry_hash']==PARENT_SHA and
              p['assigned_development_split']=='validation','only original TRAIN002 frozen receipt is in this backfill')
    b.require(saved['execution_release']==rp,'accepted receiver is not bound to original3d10')
    runtime=[b.pin(Path(config['code_root'])/name) for name in c.RUNTIME_PINS]
    b.require({Path(x['path']).name:x['sha256'] for x in runtime}==c.RUNTIME_PINS,'original seven runtime pins changed')
    out=b.safe_path(args.out);b.require(not out.exists(),'new no-clobber output required')
    out.mkdir(parents=True,exist_ok=False)
    inputs=dict(schema=c.INPUT_SCHEMA,consumer_receipt=receiver,current_contract_source=cp,runtime_source_pins=runtime)
    c.atomic_json(out/'INPUTS.json',inputs,immutable=True);ip=b.pin(out/'INPUTS.json')
    # Reuse the existing preparation path; scoped module replacement is recorded
    # explicitly in its callback pin and the outer execution receipt.
    with patch.object(prepared,'c',c):
        prepared_pin=prepared.prepare(ip,mapping,out/'qualification')
    value=json.loads(b.read_exact(prepared_pin));outcome=value['outcome']
    result=dict(status=outcome['status'],added=0)
    if args.publish and outcome['status']=='READY_FOR_NATIVE_CURRENT_BYTES_AND_UNION_CHECK':
        evidence=json.loads(b.read_exact(outcome['evidence']))
        try:
            result=c.publish(UNION,evidence,datetime.now(timezone.utc).isoformat(),
                             reader=lambda pin:b.read_exact(pin,mapping))
        except sys.modules['atomic_primitives'].BusyStudy as error:
            result=dict(status='RETRYABLE_LEDGER_BUSY',added=0,error=str(error))
        except (ValueError,KeyError,TypeError,OSError) as error:
            result=dict(status='SHARED_PUBLICATION_STOP',added=0,error_type=type(error).__name__,error=str(error))
    record=dict(schema='eucap15_production256_family_split_backfill.v2',
        utc=datetime.now(timezone.utc).isoformat(),source=b.pin(__file__),callback=b.pin(c.__file__),
        unchanged_bridge=b.pin(args.legacy_bridge),unchanged_prepare=b.pin(prepared.__file__),
        request_id=REQUEST_ID,original_receiver=receiver,original_release=rp,preparation=prepared_pin,
        requested_publish=bool(args.publish),outcome=result,child_split_preserved='validation',
        final_independent_test_eligible=False,family_warning=p['family_warning'],
        raw_receiver_reexecuted=False,physical_qa_repeated=False,extraction_repeated=False,
        native_starts=0,old_holds_overwritten=False,training_support_changed=False)
    c.atomic_json(out/'RECEIPT.json',record,immutable=True)
    return b.pin(out/'RECEIPT.json'),record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-bridge','library-dir','receiver','release','contract-inputs','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--receiver-sha256',required=True)
    parser.add_argument('--publish',action='store_true',help='Sole native owner only; default prepares without ledger writes')
    args=parser.parse_args()
    try:
        receipt,value=execute(args);print(json.dumps(dict(receipt=receipt,outcome=value['outcome'])))
        status=value['outcome']['status']
        if status=='RETRYABLE_LEDGER_BUSY':return 75
        if status in ('HOLD_NO_COMMIT','SHARED_PUBLICATION_STOP'):return 2
        return 0
    except (ValueError,KeyError,TypeError,OSError,RuntimeError) as error:
        print(json.dumps(dict(status='AUTHORITY_OR_INPUT_STOP',error_type=type(error).__name__,error=str(error))));return 2


if __name__=='__main__':raise SystemExit(main())

