"""Prepare one new256 member from an accepted receipt; never append a ledger."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

import production256_publication as c
from atomic_primitives import atomic_json


def pin(path):
    p=Path(path).absolute();b=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))


def prepare(inputs_pin,path_map,out):
    """The caller supplies an exact INPUTS pin and already-verified local map."""
    inputs=json.loads(c.raw(inputs_pin));out=Path(out).absolute()
    c.require(not out.exists() and not any(p.is_symlink() for p in (out,*out.parents)),'new no-clobber output required')
    out.mkdir(parents=True,exist_ok=False)
    atomic_json(out/'INPUTS.json',inputs,immutable=True)
    reads={}
    def reader(p):
        resolved=dict(p,path=path_map.get(p['path'],p['path']))
        data=c.raw(resolved);reads[(p['path'],p['sha256'])]=dict(original=p,resolved=resolved)
        return data
    receipt=json.loads(reader(inputs['consumer_receipt']));rid=receipt['request_id']
    try:
        evidence=c.build_evidence(inputs,rid,reader=reader)
        atomic_json(out/'EVIDENCE.json',evidence,immutable=True)
        outcome=dict(status='READY_FOR_NATIVE_CURRENT_BYTES_AND_UNION_CHECK',evidence=pin(out/'EVIDENCE.json'),
                     split=evidence['member']['split'],identities=evidence['identities'])
    except (ValueError,KeyError,OSError,TypeError) as error:
        outcome=dict(status='HOLD_NO_COMMIT',error_type=type(error).__name__,error=str(error))
    atomic_json(out/'READ_SOURCE_PINS.json',list(reads.values()),immutable=True)
    value=dict(schema='eucap15_production256_qualification_preparation.v1',utc=datetime.now(timezone.utc).isoformat(),
        request_id=rid,source=pin(__file__),callback=pin(c.__file__),source_inputs=inputs_pin,
        output_inputs=pin(out/'INPUTS.json'),read_source_pins=pin(out/'READ_SOURCE_PINS.json'),
        outcome=outcome,physical_qa_repeated=False,extraction_repeated=False,new_native_starts=0,
        actual_admissions=0,old_broadband_added=0,full_history_certified=False,
        remaining_gate='Unique owner rechecks current source bytes and current mixed union under original WRITE.lock.')
    atomic_json(out/'RECEIPT.json',value,immutable=True)
    return pin(out/'RECEIPT.json')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--inputs-sha256',required=True)
    p.add_argument('--path-map',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();ip=pin(a.inputs);c.require(ip['sha256']==a.inputs_sha256,'INPUTS changed')
    mapping={} if a.path_map is None else json.loads(a.path_map.read_bytes())
    c.require(isinstance(mapping,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in mapping.items()),
              'path map must only map paths; original SHA verification is retained')
    print(json.dumps(prepare(ip,mapping,a.out)))


if __name__=='__main__':main()
