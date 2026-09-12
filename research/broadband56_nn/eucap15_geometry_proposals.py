"""Configured offline DOE/train-neighborhood preparation, not a native owner.

Public v2 path interface refactored from a private one-shot preparer. It is not
byte-identical to the source that froze the existing September12 batch. Reuse
that frozen manifest; this CLI refuses existing outputs and cannot resume native
work. Source data / private helper paths must be supplied as explicit SHA pins.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from research.broadband56_nn.eucap15_acquisition import geometry_lhs
from research.broadband56_nn.eucap15_prepare_acquisition import memory_headroom
from research.broadband56_nn.evaluation import _grid_geometry, _grid_from_contract
from research.broadband56_nn.frequency_large_eval import _geometry_hash, _geometry_status
from research.broadband56_nn.data import _split_for_hash
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import canonical_geometry_sha256

O = None
PINS = {}
OUTPUT_CREATED = False


def pin(p):
    p = Path(p)
    if not p.is_absolute() or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('Exact nonsymlink path required')
    b = p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))


def read(p, sha, text=False):
    actual = pin(p)
    if actual['sha256'] != sha:
        raise ValueError('SHA changed: ' + str(p))
    PINS[str(p)] = actual
    return Path(p).read_text() if text else json.loads(Path(p).read_text())


def save(name, value):
    with (O / name).open('x') as f:
        json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False)
        f.write('\n')


def jsonl(name, rows):
    with (O / name).open('x') as f:
        for row in rows:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n')


def neighbors(bases, lower, upper, count, seed, fraction):
    """New bounded local uniform kernel, not the old global-LHS exploration."""
    lower,upper=np.asarray(lower,float),np.asarray(upper,float)
    if not 0 < fraction <= .01 or count > len(bases):
        raise ValueError('Declared bounded neighborhood required')
    rng=np.random.Generator(np.random.PCG64(seed))
    picks=rng.choice(len(bases),count,replace=False)
    radius=(upper-lower)*fraction
    result=[]
    for index in picks:
        base=bases[int(index)]
        geometry=np.asarray(base['geometry'],float)
        lo=np.maximum(lower,geometry-radius)
        hi=np.minimum(upper,geometry+radius)
        unit=rng.random(len(lower))
        raw=lo+(hi-lo)*unit
        result.append(dict(base=base,raw=raw.tolist(),unit=unit.tolist(),
            lower=lo.tolist(),upper=hi.tolist(),jitter=(raw-geometry).tolist()))
    return result


def configured_inputs(intent):
    if intent.get('schema') != 'eucap15_geometry_proposal_public_intent.v2':
        raise ValueError('Explicit public-v2 intent required; do not reuse old batch intent')
    required={'contract','splits','current_source_rows','exclusion_metadata','prior_members31','prior_proposals64'}
    if set(intent.get('inputs',{})) != required:
        raise ValueError('Exact six source pins required; private data are not in the repository')
    for item in [*intent['inputs'].values(),*intent.get('sources',{}).values()]:
        if not isinstance(item,dict) or set(item) != {'path','sha256'}:
            raise ValueError('Each source must be explicit path/sha256')
        p=Path(item['path']);h=item['sha256']
        if not p.is_absolute() or not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h):
            raise ValueError('Absolute source path and lowercase SHA256 required')
    if 'production_geometry_helpers' not in intent.get('sources',{}):
        raise ValueError('Pinned production geometry helper required')
    return intent['inputs']


def main(argv=None):
    global OUTPUT_CREATED, O
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent',required=True,type=Path)
    parser.add_argument('--intent-sha',required=True)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args(argv)
    started=time.monotonic()
    intent=read(args.intent,args.intent_sha)
    old=configured_inputs(intent)
    O=args.out
    if not O.is_absolute() or O.exists() or any(p.is_symlink() for p in (O,*O.parents)):
        raise ValueError('NO_CLOBBER: new absolute nonsymlink output required')
    intent_pin=pin(args.intent)
    assert intent['counts']=={'GEOMETRY_DOE':192,'TRAIN_NEIGHBORHOOD':64}
    assert intent['seeds']=={'doe':2026091201,'neighbor':2026091202,'split':17}
    for p in intent['sources'].values():
        read(p['path'],p['sha256'],text=True)
    O.mkdir()
    OUTPUT_CREATED = True
    memory=memory_headroom();disk=shutil.disk_usage(O)
    save('RESOURCE_CHECK.json',dict(memory=memory,free_bytes=disk.free,cpu_threads=2,native_admission='OWNER_REQUIRED'))
    if not memory['passed'] or disk.free < 2*1024**3:
        raise ValueError('Local tiny preparation headroom not met')
    torch.set_num_threads(2)
    contract=read(old['contract']['path'],old['contract']['sha256'])
    splits=read(old['splits']['path'],old['splits']['sha256'])
    assert splits['counts']==dict(train=3801,validation=1269,test=1259) and splits['seed']==17
    fields=contract['field_names'];grid_um=_grid_from_contract(contract)
    read(old['current_source_rows']['path'],old['current_source_rows']['sha256'],text=True)
    # CSV strings are mechanically parsed, but no val/test numeric labels used.
    with Path(old['current_source_rows']['path']).open(newline='') as f:
        metadata=list(csv.DictReader(f))
    assert len(metadata)==6329
    bases=[]
    for row in metadata:
        h=row['geometry_sha256']
        assert row['assigned_development_split']==splits['by_geometry_sha256'][h]
        if row['assigned_development_split']!='train':
            continue
        assert row['strict_lumped_valid']=='true' and row['core_eligible'].lower()=='true'
        g=[float(row['geom__'+f]) for f in fields]
        assert canonical_geometry_sha256(dict(zip(fields,g)),fields=fields)==h
        bases.append(dict(geometry_id=row['geometry_id'],geometry_sha256=h,geometry=g,
            assigned_development_split='train',source_candidate_id=row['candidate_id'],
            source_row_1based=int(row['view_row'])+2))
    assert len(bases)==3801
    bases.sort(key=lambda r:r['geometry_sha256'])
    _,flags=_geometry_status(np.asarray([r['geometry'] for r in bases]),contract)
    feasible_bases=[r for r,passed in zip(bases,flags) if passed]
    assert len(feasible_bases)>=64
    excluded=read(old['exclusion_metadata']['path'],old['exclusion_metadata']['sha256'])
    canonical_sets={k:set(v) for k,v in excluded['canonical_hashes'].items()}
    grid_sets={k:set(v) for k,v in excluded['nominal_grid_hashes'].items()}
    members31=read(old['prior_members31']['path'],old['prior_members31']['sha256'])
    assert len(members31)==31
    new64text=read(old['prior_proposals64']['path'],old['prior_proposals64']['sha256'],text=True)
    new64=[json.loads(line) for line in new64text.splitlines()]
    assert len(new64)==64
    helper=Path(intent['sources']['production_geometry_helpers']['path'])
    spec=importlib.util.spec_from_file_location('frozen_geometry_helpers',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    fingerprint=module._production_geometry_fingerprint
    production_known=set()
    for name,items in [('PREVIOUS31_RESEARCH_MEMBERS',[(r['geometry_sha256'],r['geometry']) for r in members31]),
                        ('CLOSED_CONTROLLED64_ALL_PROPOSALS',[(r['canonical_geometry_sha256'],r['geometry']) for r in new64 if r['geometry'] is not None])]:
        canonical_sets[name]=set();grid_sets[name]=set()
        for h,g in items:
            assert canonical_geometry_sha256(dict(zip(fields,g)),fields=fields)==h
            canonical_sets[name].add(h)
            gg=_grid_geometry(np.asarray(g),grid_um).tolist()
            grid_sets[name].add(canonical_geometry_sha256(dict(zip(fields,gg)),fields=fields))
            production_known.add(fingerprint(g));production_known.add(fingerprint(gg))
    for row in metadata:
        g=[float(row['geom__'+f]) for f in fields]
        production_known.add(fingerprint(g));production_known.add(fingerprint(_grid_geometry(np.asarray(g),grid_um)))
    save('EXCLUSION_METADATA.json',dict(canonical_hashes={k:sorted(v) for k,v in canonical_sets.items()},
        nominal_grid_hashes={k:sorted(v) for k,v in grid_sets.items()},
        production_fingerprint_known=sorted(production_known),
        production_fingerprint_scope='CURRENT6329_PLUS_PREVIOUS31_PLUS_CLOSED64_RAW_AND_GRID_ONLY',
        old_exclusions_reused_without_P215_scan=True,all_history_complete=False,
        current_native_owner_ledger_recheck='REQUIRED',validation_test_numeric_labels_used=0))
    study=intent['study_id'];lo,hi=contract['lower'],contract['upper']
    rawdoe=geometry_lhs(lo,hi,192,intent['seeds']['doe'])
    nbs=neighbors(feasible_bases,lo,hi,64,intent['seeds']['neighbor'],.01)
    proposals=[]
    def row(source,pos,raw,base=None):
        grid=_grid_geometry(np.asarray(raw),grid_um)
        _,analytic=_geometry_status(grid[None,:],contract)
        rid=f'{study}-{source}-{pos:03d}'
        return dict(request_id=rid,candidate_id=rid,source=source,
            arm=('GEOMETRY_DOE_PRODUCTION' if source=='GEOMETRY_DOE' else 'TRAIN_NEIGHBORHOOD_PRODUCTION'),
            arm_order=pos+1,target=None,requested_triple=None,target_cell=None,sparse_cell=None,
            q_proxy=None,q_emx=None,proxy=None,score=None,predicted_cell=None,model_id=None,qscan_source_candidate=None,
            support='GEOMETRY_PROPOSAL_PHYSICAL_SUPPORT_UNKNOWN',
            decoded_continuous_geometry=None,raw_lhs_geometry=list(raw) if source=='GEOMETRY_DOE' else None,
            raw_proposed_geometry=list(raw),geometry=grid.tolist(),analytic_pass=bool(analytic[0]),
            base_train_id=None if base is None else base['base']['geometry_id'],
            base_geometry_hash=None if base is None else base['base']['geometry_sha256'],
            base_train_geometry=None if base is None else base['base']['geometry'],
            base_metadata=None if base is None else base['base'],
            neighborhood_lower=None if base is None else base['lower'],
            neighborhood_upper=None if base is None else base['upper'],
            neighborhood_unit=None if base is None else base['unit'],jitter=None if base is None else base['jitter'],
            seed=intent['seeds']['doe' if source=='GEOMETRY_DOE' else 'neighbor'])
    for i,base in enumerate(nbs):
        for j in range(3): proposals.append(row('GEOMETRY_DOE',i*3+j,rawdoe[i*3+j]))
        proposals.append(row('TRAIN_NEIGHBORHOOD',i,base['raw'],base))
    hashes=[canonical_geometry_sha256(dict(zip(fields,r['geometry'])),fields=fields) for r in proposals]
    phashes=[fingerprint(r['geometry']) for r in proposals]
    mult=Counter(hashes);pmult=Counter(phashes)
    for order,(r,h,ph) in enumerate(zip(proposals,hashes,phashes),1):
        reasons=[]
        for name,hs in canonical_sets.items():
            if h in hs: reasons.append('EXISTING_CANONICAL_'+name)
        for name,hs in grid_sets.items():
            if h in hs: reasons.append('NOMINAL_GRID_EQUIVALENT_'+name)
        if ph in production_known: reasons.append('EXISTING_PRODUCTION_FINGERPRINT_KNOWN_SUBSET')
        if mult[h]>1 or pmult[ph]>1: reasons.append('WITHIN_BATCH_DUPLICATE_ALL_SYMMETRIC_HOLD')
        r.update(schema='eucap15_acquisition_candidate.v1',protocol_schema=intent['schema'],
            recipe_sha256=intent_pin['sha256'],global_order=order,
            frequency_hz=15000000000,geometry_fields=fields,geometry_units='um',
            canonical_geometry_sha256=h,parameter_geometry_hash=_geometry_hash(r['geometry'],fields),
            production_geometry_fingerprint=ph,raw_production_geometry_fingerprint=fingerprint(r['raw_proposed_geometry']),
            identity_not_actual_gds=True,duplicate_reasons=reasons,
            local_dispatch_eligible=r['analytic_pass'] and not reasons,
            assigned_development_split=('train','validation','test')[_split_for_hash(h,17)],
            split_used_for_proposal_selection=False,no_replacement=True,no_q_fallback=True,
            native_status='NOT_SUBMITTED',reservation_status='NOT_RESERVED',gds_status='NOT_RUN',drc_status='NOT_RUN',
            emx_status='NOT_RUN',actual_response=None,actual_landing=None,coverage_gain=None,
            solver_start_order=None,solver_seconds=None,total_stage_cost_seconds=None,storage_bytes=None,
            owner_current_ledger_check='REQUIRED',qualification_union_status='PENDING_KNOWN_POOL_AND_FULL_HISTORY_MERGE',
            final_independent_test_eligible=False,
            family_warning='NEIGHBOR_CHILD_OF_TRAIN_NOT_INDEPENDENT_FINAL_TEST' if r['source']=='TRAIN_NEIGHBORHOOD' else 'NO_FAMILY_INDEPENDENCE_CLAIM')
    jsonl('SELECTED_CANDIDATES.jsonl',proposals)
    columns=['global_order','arm','arm_order','request_id','candidate_id','source','seed','analytic_pass',
             'local_dispatch_eligible','duplicate_reasons','canonical_geometry_sha256','production_geometry_fingerprint',
             'assigned_development_split','base_train_id','base_geometry_hash','q_proxy','target','native_status']
    with (O/'CANDIDATE_INDEX.csv').open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns,lineterminator='\n');w.writeheader()
        for r in proposals:w.writerow({k:json.dumps(r[k]) if isinstance(r[k],(list,dict)) else r[k] for k in columns})
    save('GEOMETRY_CONTRACT.json',contract)
    counts={source:dict(proposals=sum(r['source']==source for r in proposals),
        analytic_pass=sum(r['source']==source and r['analytic_pass'] for r in proposals),
        duplicate_held=sum(r['source']==source and bool(r['duplicate_reasons']) for r in proposals),
        local_dispatch_eligible=sum(r['source']==source and r['local_dispatch_eligible'] for r in proposals)) for source in intent['counts']}
    assert len(proposals)==256 and all(r['q_proxy'] is None and r['target'] is None for r in proposals)
    for p in PINS.values():assert pin(p['path'])==p
    save('INPUT_PINS.json',list(PINS.values()))
    save('PREPARATION_RECEIPT.json',dict(status='FROZEN_256_PROPOSALS_NOT_NATIVE_RELEASE',public_preparer_schema='PUBLIC_REFACTOR_V2_NOT_ORIGINAL_BATCH_SOURCE',
        completed_utc=datetime.now(timezone.utc).isoformat(),study_id=study,counts=counts,
        source_train_count=3801,analytically_feasible_train_base_count=len(feasible_bases),
        selected_unique_base_count=64,previous20_train_used_for_exclusions_only=True,
        numeric_validation_test_labels_used=0,CSV_label_strings_mechanically_parsed=True,
        old64_budget_unchanged=True,new_budget_owner_registration_required=True,
        actual_emx_starts=0,completed_emx=0,new_real_qualified=0,model_calls=0,training_updates=0,
        source_bytes_unchanged=True,native_status='NOT_SUBMITTED',
        new_neighborhood_method='NEW_SMALL_RADIUS_NEIGHBORHOOD_NOT_EXISTING_MATURE_FUNCTION',
        generation_elapsed_seconds=time.monotonic()-started,python=sys.executable,
        numpy=np.__version__,torch=torch.__version__,platform=platform.platform(),
        bounded_metadata_exclusions_not_full_history_qualification=True,
        complete_physical_scope=intent['physical_qualification_scope'],
        proposed_native_budget=intent['suggested_new_budget'],native_admission='OWNER_ONLY_REQUIRED'))
    files={p.name:pin(p) for p in sorted(O.iterdir()) if p.is_file()}
    save('MANIFEST.json',dict(schema='eucap15_production_input_manifest.v1',
        status='PREPARATION_ONLY_NOT_NATIVE_RELEASE',study_id=study,intent=intent_pin,files=files))
    with (O/'SHA256SUMS').open('x') as f:
        for p in sorted(O.iterdir()):
            if p.is_file() and p.name!='SHA256SUMS':f.write(pin(p)['sha256']+'  '+p.name+'\n')
    print(json.dumps(dict(manifest=pin(O/'MANIFEST.json'),receipt=pin(O/'PREPARATION_RECEIPT.json'),counts=counts)))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        if OUTPUT_CREATED and not (O/'FAILURE.json').exists():save('FAILURE.json',dict(status='FAIL_NO_AUTOMATIC_RETRY',error=repr(exc)))
        raise
