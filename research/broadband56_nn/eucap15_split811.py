"""15GHz formal 80/10/10 geometry-family mapping above immutable ledgers.

No native calls or historical split edits. Actual fit, tuning, evaluation and
sampling use are distinct from fixed qualification reads. Evidence-backed DOE
families can fill holdouts regardless of age; unknown exposure stays pending.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib, json, math, re
from pathlib import Path
import numpy as np
from .data import SPLIT_NAMES, Y_COLUMNS, PHYSICAL_COLUMNS, _fit_scale, _write_json, sha256
from .eucap15_range_policy import (POLICY as RANGE_POLICY, POLICY_IDS as RANGE_POLICY_IDS,
    make_range_policy, resolve_range_policy, classify_physical15, train_coverage_bounds)

POLICY = 'EUCAP15_FORMAL_GEOMETRY_FAMILY_811_V1'
DEFAULT_FRACTIONS = (.8, .1, .1)
DEFAULT_TARGET_TOTAL = 100000
DEFAULT_SEED = 20260914
EXPOSURE_POLICY = 'ACTUAL_USAGE_AND_FAMILY_EVIDENCE_V2'
LEGACY_EXPOSURE_POLICY = 'LEGACY_SEQUENCE_CUTOFF_V1'
USAGE_FIELDS = ('gradient_training', 'tuning', 'development_evaluation', 'sampling_feedback')

def pin(p):
    p=Path(p).resolve()
    return dict(path=str(p),sha256=sha256(p),bytes=p.stat().st_size)

def make_policy(cutoff, first_doe_batch, *, target_total=DEFAULT_TARGET_TOTAL, seed=DEFAULT_SEED,
                physical_range_policy=RANGE_POLICY, exposure_policy=EXPOSURE_POLICY):
    if type(target_total) is not int or target_total <= 0 or target_total % 10:
        raise ValueError('target_total must be a positive multiple of ten')
    if exposure_policy not in (EXPOSURE_POLICY, LEGACY_EXPOSURE_POLICY):
        raise ValueError('unknown exposure policy')
    result = dict(schema=POLICY, requested_fractions=list(DEFAULT_FRACTIONS), seed=seed,
        target_total=target_total, target_counts=dict(train=target_total*8//10,validation=target_total//10,test=target_total//10),
        exposure_cutoff_sequence=cutoff, first_prospective_doe_batch=first_doe_batch,
        physical_range_policy=resolve_range_policy(physical_range_policy),
        pre_policy_assignment='TRAIN_ONLY_NOT_INDEPENDENT_HOLDOUT',
        independent_doe_rule='future production DOE; no model or parent; preserved fixed DOE recipe; no subsequent holdout labels in sampling',
        final_independent_10000_request_emx='SEPARATE_NOT_PART_OF_MODELING_100K')
    if exposure_policy == EXPOSURE_POLICY:
        result.update(exposure_policy=EXPOSURE_POLICY,
            pre_policy_assignment='ACTUAL_USAGE_EVIDENCE_REQUIRED_AGE_IS_NOT_EXPOSURE',
            unknown_assignment='PENDING_EXPOSURE_NOT_AUTOMATIC_TRAIN',
            independent_doe_rule='existing or future fixed DOE; complete scoped usage evidence; no development, feedback or derived family; family reserved together',
            preprocessing_rule='temporary unused normalizers are recorded and replaced; holdout never fits the new model normalizer')
    return result

def exposure_status(row):
    """Classify evidence, never infer non-use from missing booleans or age.

    Evidence refs bind a separately reviewed usage audit. Screening and an
    unused exploratory normalizer are not model development, but an active
    model's preprocessing fit is. Parent links restrict the entire component.
    """
    e=row.get('exposure', {})
    for key in (*USAGE_FIELDS, 'model_preprocessing_fit'):
        if e.get(key) not in (True, False, None):
            raise ValueError('usage evidence must be true, false or null: '+key)
    if any(e.get(k) is True for k in (*USAGE_FIELDS, 'model_preprocessing_fit')) or any(
            row.get(k) is True for k in ('used_for_training','used_for_tuning','used_for_sampling_feedback')):
        return 'DEVELOPMENT_EXPOSED'
    if (row.get('base_train_id') or row.get('base_geometry_hash') or row.get('base_metadata') or
            row.get('model_used_for_proposal') is True or
            e.get('family_status') == 'DERIVED_OR_NEIGHBORHOOD'):
        return 'DEVELOPMENT_DERIVED_FAMILY'
    if e.get('development_reuse_only') is True:
        return 'DEVELOPMENT_REUSE_ONLY'
    refs=e.get('evidence_refs', [])
    pinned=bool(refs) and all(isinstance(p,dict) and p.get('path') and
        re.fullmatch('[0-9a-f]{64}', p.get('sha256','')) for p in refs)
    recipe=e.get('geometry_recipe_ref', {})
    pinned_recipe=(isinstance(recipe,dict) and recipe.get('path') and
        re.fullmatch('[0-9a-f]{64}',recipe.get('sha256','')))
    fixed_lhs_exploration=(row.get('source')=='EXPLORATION' and
        e.get('proposal_method')=='FIXED_GEOMETRY_LHS' and e.get('no_proxy_selection') is True and
        pinned_recipe)
    no_proposal_model=(row.get('model_used_for_proposal') is False or
        (row.get('model_used_for_proposal') is None and pinned_recipe and
         e.get('proposal_method')=='FIXED_GEOMETRY_LHS' and e.get('no_proxy_selection') is True and
         e.get('proposal_model_used') is False))
    if (pinned and e.get('usage_scope_complete') is True and
            all(e.get(k) is False for k in (*USAGE_FIELDS, 'model_preprocessing_fit')) and
            e.get('family_status')=='INDEPENDENT_DOE_ROOT' and
            (row.get('source')=='GEOMETRY_DOE' or fixed_lhs_exploration) and
            no_proposal_model):
        return 'PROVEN_UNUSED_INDEPENDENT_DOE'
    return 'UNKNOWN_EXPOSURE_OR_FAMILY'

def reserve_doe_families(rows, policy, previous=None):
    """Reserve a pinned, unlabeled DOE queue before qualification outcomes.

    Keep rejected/pending candidates in this reservation; qualify them later
    without rerolling their split or selecting replacement test successes.
    """
    if policy.get('exposure_policy') != EXPOSURE_POLICY:
        raise ValueError('DOE reservation requires actual usage policy')
    if any('physical15' in r or exposure_status(r)!='PROVEN_UNUSED_INDEPENDENT_DOE' for r in rows):
        raise ValueError('reserve only proven independent DOE before reading its response')
    result=assignments(rows,policy,previous)
    result['reservation_scope']='PRE_RESPONSE_QUEUE_KEEP_ALL_FAILURE_AND_PENDING_IDENTITIES'
    return result

def _batch(row):
    m=re.search(r'batch(\d+)-',row.get('request_id',''))
    return int(m.group(1)) if m else -1

def _tokens(row):
    # Namespace aliases group nominally identical geometry conservatively. They
    # are family links, not a claim that differing nominal geometries are equal.
    tokens={row['geometry_sha256']}
    tokens.update(x for x in row.get('identity_namespaces',{}).values() if x)
    tokens.add('coordinates9dp:'+','.join(format(float(x),'.9f') for x in row['geometry']))
    for key in ('base_train_id','base_geometry_hash'):
        if row.get(key):tokens.add(row[key])
    for key in ('geometry_id','geometry_sha256'):
        if row.get('base_metadata',{}):
            if row['base_metadata'].get(key):tokens.add(row['base_metadata'][key])
    return tokens

def assignments(rows, policy, previous=None):
    if policy['schema']!=POLICY or policy['requested_fractions']!=list(DEFAULT_FRACTIONS):
        raise ValueError('formal policy must be 80/10/10')
    rows=sorted(rows,key=lambda r:(r['sequence'],r['geometry_sha256']))
    if len({r['sequence'] for r in rows})!=len(rows):raise ValueError('duplicate ledger sequence')
    parent={}
    def find(x):
        parent.setdefault(x,x)
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:parent[max(a,b)]=min(a,b)
    for r in rows:
        ts=sorted(_tokens(r))
        for t in ts[1:]:union(ts[0],t)
    groups=defaultdict(list)
    for r in rows:groups[find(r['geometry_sha256'])].append(r)
    old={} if previous is None else previous['by_geometry_sha256']
    if previous and previous['policy']!=policy:raise ValueError('resume policy changed')
    if set(old)-{r['geometry_sha256'] for r in rows}:raise ValueError('resume lost prior geometry')
    quota=policy['target_counts'];counts=Counter(old.values());out=[];seen_hash=set();seen_coord=set()
    for family,group in sorted(groups.items(),key=lambda x:(min(r['sequence'] for r in x[1]),x[0])):
        unique=[]
        for r in group:
            coord=tuple(round(float(x),9) for x in r['geometry'])
            if r['geometry_sha256'] in seen_hash or coord in seen_coord:
                out.append(dict(sequence=r['sequence'],geometry_sha256=r['geometry_sha256'],family=family,split='DUPLICATE',reason='DUPLICATE_GEOMETRY_NOT_COUNTED',original_split=r.get('split','UNKNOWN')))
                continue
            seen_hash.add(r['geometry_sha256']);seen_coord.add(coord);unique.append(r)
        if not unique:continue
        # Frozen policies without the v2 evidence field retain exact replay.
        legacy_fresh=all(r['sequence']>policy['exposure_cutoff_sequence'] and
            r.get('source')=='GEOMETRY_DOE' and _batch(r)>=policy['first_prospective_doe_batch'] and
            r.get('model_used_for_proposal') is False and
            not r.get('base_train_id') and not r.get('base_geometry_hash') and
            r.get('origin_class')=='FRESH_EMX_QUALIFICATION' and
            not r.get('used_for_training',False) and not r.get('used_for_tuning',False) and
            not r.get('used_for_sampling_feedback',False) for r in unique)
        statuses={exposure_status(r) for r in group} if policy.get('exposure_policy')==EXPOSURE_POLICY else set()
        fresh=statuses=={'PROVEN_UNUSED_INDEPENDENT_DOE'} if statuses else legacy_fresh
        unknown=bool(statuses) and not fresh and not any(s.startswith('DEVELOPMENT_') for s in statuses)
        prior={old[r['geometry_sha256']] for r in unique if r['geometry_sha256'] in old}
        if len(prior)>1:raise ValueError('prior mapping splits one family across partitions')
        fixed=next(iter(prior)) if prior else None
        if fixed in ('validation','test') and not fresh:
            raise ValueError('HOLDOUT_FAMILY_EXPOSURE_CONFLICT: keep old mapping, quarantine new extension')
        if unknown and fixed is None:
            for r in unique:out.append(dict(sequence=r['sequence'],geometry_sha256=r['geometry_sha256'],family=family,
                split='PENDING_EXPOSURE',reason='USAGE_OR_FAMILY_EVIDENCE_INCOMPLETE',
                exposure_status=exposure_status(r),original_split=r.get('split','UNKNOWN')))
            continue
        new_size=sum(r['geometry_sha256'] not in old for r in unique)
        allowed=[fixed] if fixed else list(SPLIT_NAMES) if fresh else ['train']
        allowed=[s for s in allowed if s in quota and counts[s]+new_size<=quota[s]]
        if not allowed:chosen='PENDING_QUOTA';reason='FAMILY_DOES_NOT_FIT_REMAINING_QUOTA'
        else:
            remaining=[max(1,quota[s]-counts[s]) for s in allowed]
            ticket=int.from_bytes(hashlib.sha256(f'{POLICY}:{policy["seed"]}:{family}'.encode()).digest()[:8],'big')%sum(remaining)
            chosen=allowed[-1]
            for s,n in zip(allowed,remaining):
                if ticket<n:chosen=s;break
                ticket-=n
            reason='PRESERVED_NEW_MAPPING' if fixed else 'EVIDENCE_BACKED_INDEPENDENT_DOE_FAMILY' if fresh and statuses else 'PROSPECTIVE_INDEPENDENT_DOE_FAMILY' if fresh else 'DEVELOPMENT_FAMILY_TRAIN_REUSE' if statuses else 'PRE_POLICY_OR_EXPOSED_FAMILY_TRAIN_ONLY'
            counts[chosen]+=new_size
        for r in unique:out.append(dict(sequence=r['sequence'],geometry_sha256=r['geometry_sha256'],family=family,
            split=old.get(r['geometry_sha256'],chosen),reason='PRESERVED_NEW_MAPPING' if r['geometry_sha256'] in old else reason,
            **({'exposure_status':exposure_status(r)} if statuses else {}),original_split=r.get('split','UNKNOWN')))
    out.sort(key=lambda r:r['sequence'])
    byhash={r['geometry_sha256']:r['split'] for r in out if r['split'] in SPLIT_NAMES}
    if any(byhash.get(h)!=s for h,s in old.items()):raise ValueError('resume changed old new-version assignment')
    counts={s:counts[s] for s in SPLIT_NAMES};deficit={s:quota[s]-counts[s] for s in SPLIT_NAMES}
    return dict(schema='eucap15_split_mapping.v2',policy=policy,counts=counts,deficit=deficit,
        exact_target_complete=all(n==0 for n in deficit.values()),rows=out,by_geometry_sha256=byhash,
        pending=Counter(r['split'] for r in out if r['split'] not in SPLIT_NAMES),
        old_splits_modified=False,old_training_repeated=False)

def build(snapshot_paths, out, *, contract_path, policy, previous_mapping=None, exposure_mapping=None):
    """The formal dataset construction entry. All output is no-clobber."""
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    try:return _build(snapshot_paths,out,contract_path,policy,previous_mapping,exposure_mapping)
    except Exception as exc:
        _write_json(out/'PREPARATION_FAILED.json',dict(status='FAIL_PRESERVED',error=str(exc)));raise

def _build(snapshot_paths,out,contract_path,policy,previous_mapping,exposure_mapping):
    range_policy=resolve_range_policy(policy.get('physical_range_policy'),legacy_if_missing=True)
    snapshots=[json.loads(Path(p).read_text()) for p in snapshot_paths]
    rows=[r for x in snapshots for r in x['members']]
    if exposure_mapping is not None:
        audit=json.loads(Path(exposure_mapping).read_text())
        if audit.get('schema')!='eucap15_usage_evidence_map.v1':raise ValueError('wrong exposure map schema')
        for ref in audit['input_pins']:
            if sha256(Path(ref['path']))!=ref['sha256']:raise ValueError('exposure source changed')
        evidence=audit['by_geometry_sha256']
        rows=[dict(r,exposure=evidence.get(r['geometry_sha256'],{})) for r in rows]
    seq=sorted(r['sequence'] for r in rows)
    if seq!=list(range(1,max(seq)+1)):raise ValueError('consistent full sequence prefix required')
    mapped=assignments(rows,policy,None if previous_mapping is None else json.loads(Path(previous_mapping).read_text()))
    _write_json(out/'SPLIT_POLICY.json',policy);_write_json(out/'SPLIT_MAPPING.json',mapped)
    selected=[r for r in rows if r['geometry_sha256'] in mapped['by_geometry_sha256']]
    # A duplicate alias in rows must not duplicate an array row.
    chosen={};coords=set()
    for r in sorted(selected,key=lambda r:r['sequence']):
        coord=tuple(round(float(x),9) for x in r['geometry'])
        if r['geometry_sha256'] in chosen or coord in coords:continue
        chosen[r['geometry_sha256']]=r;coords.add(coord)
    selected=list(chosen.values());contract=json.loads(Path(contract_path).read_text());fields=contract['field_names']
    for r in selected:
        p=r['physical15']
        if r['frequency_hz']!=15000000000 or r['geometry_fields']!=fields:raise ValueError('geometry/frequency contract mismatch')
        range_result=classify_physical15(p,range_policy)
        if not range_result['range_eligible']:raise ValueError('qualified physical range mismatch: '+','.join(range_result['reasons']))
        if not math.isclose(p['qmin'],min(p['qp'],p['qs']),rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Qmin definition mismatch')
    g=np.array([r['geometry'] for r in selected],dtype=np.float64)
    y=np.array([[r['physical15'][k] for k in Y_COLUMNS] for r in selected],dtype=np.float64)[:,None,:]
    hashes=[r['geometry_sha256'] for r in selected];ids=[r['request_id'] for r in selected]
    split=np.array([SPLIT_NAMES.index(mapped['by_geometry_sha256'][h]) for h in hashes],dtype=np.int8)
    train=split==0
    if train.sum()<2 or not np.isfinite(g).all() or not np.isfinite(y).all():raise ValueError('insufficient/nonfinite train data')
    if (g<np.asarray(contract['lower'])).any() or (g>np.asarray(contract['upper'])).any():raise ValueError('geometry bounds mismatch')
    ym,ys=_fit_scale(y[train]);norm=dict(schema='bb_normalizer.v1',fit_split='train',training_geometries=int(train.sum()),
        g_min=g[train].min(0).tolist(),g_max=g[train].max(0).tolist(),y_mean=ym,y_scale=ys,
        field_names=fields,geometry_fields=fields,y_columns=list(Y_COLUMNS),s_mean=None,s_scale=None,
        s_columns=[],s_status='NOT_INCLUDED',contract_bounds_um=dict(lower=contract['lower'],upper=contract['upper']),split_policy=POLICY,
        physical_range_policy=range_policy)
    arrays=dict(geometry=g,y=y,geometry_ids=np.array(ids),geometry_sha256=np.array(hashes),split=split,
        frequency_hz=np.array([15000000000],dtype=np.int64),y_valid=np.ones_like(y,dtype=bool),
        strict_lumped_valid=np.ones((len(g),1),dtype=bool),broadband_descriptor_valid=np.ones((len(g),1),dtype=bool))
    with (out/'dataset.npz').open('xb') as f:np.savez_compressed(f,**arrays)
    sources=dict(schema='eucap15_formal811_sources.v1',snapshots=[pin(p) for p in snapshot_paths],contract=pin(contract_path),previous_mapping=pin(previous_mapping) if previous_mapping else None,
        exposure_mapping=pin(exposure_mapping) if exposure_mapping else None)
    _write_json(out/'SOURCE_MANIFEST.json',sources);_write_json(out/'normalizer.json',norm)
    _write_json(out/'PHYSICAL_RANGE_POLICY.json',range_policy)
    _write_json(out/'TRAIN_COVERAGE_BOUNDS.json',train_coverage_bounds(
        [r['physical15'] for r,s in zip(selected,split) if s==0]))
    splits=dict(schema='bb_splits.v1',seed=policy['seed'],method=POLICY,requested_fractions=list(DEFAULT_FRACTIONS),counts=mapped['counts'],
        by_geometry_sha256=mapped['by_geometry_sha256'],geometry_id_to_sha256=dict(zip(ids,hashes)),
        ids={s:[i for i,h in zip(ids,hashes) if mapped['by_geometry_sha256'][h]==s] for s in SPLIT_NAMES},
        mapping=pin(out/'SPLIT_MAPPING.json'),policy=policy,partial_quotas=not mapped['exact_target_complete'])
    _write_json(out/'splits.json',splits)
    _write_json(out/'geometry_provenance.json',dict(rows=[dict(sequence=r['sequence'],geometry_sha256=r['geometry_sha256'],source=r['source'],original_split=r['split'],record=r['record']) for r in selected]))
    (out/'contract.json').write_bytes(Path(contract_path).read_bytes())
    artifacts={n:dict(path=n,sha256=sha256(out/n),size_bytes=(out/n).stat().st_size) for n in ('dataset.npz','splits.json','SPLIT_MAPPING.json','SPLIT_POLICY.json','normalizer.json','geometry_provenance.json','contract.json','PHYSICAL_RANGE_POLICY.json','TRAIN_COVERAGE_BOUNDS.json')}
    counts=mapped['counts'];balanced=counts['validation']==counts['test'] and counts['train']==8*counts['test'] and counts['test']>0
    manifest=dict(schema='bb_data_manifest.v1',status='PASS',split_policy=POLICY,physical_range_policy=range_policy,source_manifest=pin(out/'SOURCE_MANIFEST.json'),
        contract_fingerprint_sha256=selected[0]['scientific_contract_fingerprint'],unique_geometries=len(g),frequency_rows=len(g),
        geometry_dim=len(fields),geometry_fields=fields,geometry_field_order=fields,geometry_units='um',frequency_hz=[15000000000],
        target_columns=list(Y_COLUMNS),split_counts=counts,normalizer_fit_split='train',artifacts=artifacts,
        scope='FORMAL_811_INCOMPLETE_POOL_NOT_A_TRAINED_MODEL',training_ready=balanced,
        readiness_reason='READY_EXACT_811' if balanced else 'WAITING_FOR_INDEPENDENT_HOLDOUT_QUOTAS',
        broadband='NOT_SUPPORTED_15GHZ_ONLY',deficit=mapped['deficit'],final_independent_10k_requests='SEPARATE_NOT_RUN')
    _write_json(out/'data_manifest.json',manifest)
    receipt=dict(status='MAPPING_AND_DATA_BUILT_TRAINING_NOT_STARTED',counts=counts,deficit=mapped['deficit'],training_ready=balanced,
        geometry_unique=len(g),family_count=len({r['family'] for r in mapped['rows']}),native_actions=0,training_calls=0,old_files_changed=False,
        normalizer_fit='ONLY_NEW_MAPPING_TRAIN',manifest=pin(out/'data_manifest.json'),mapping=pin(out/'SPLIT_MAPPING.json'))
    _write_json(out/'DATA_RECEIPT.json',receipt)
    with (out/'SHA256SUMS').open('x') as f:
        for p in sorted(out.iterdir()):
            if p.name!='SHA256SUMS':f.write(sha256(p)+'  '+p.name+'\n')
    return receipt

def validate_training_split(data_root, *, legacy_resume=False, expected_mapping_sha=None,
                            expected_range_policy=None):
    """Called by the actual F/I training entry before any model update."""
    root=Path(data_root);manifest=json.loads((root/'data_manifest.json').read_text())
    if manifest.get('split_policy')!=POLICY:
        if legacy_resume:return dict(status='LEGACY_EXACT_RESUME_NOT_NEW_811')
        raise ValueError('NEW_TRAINING_REQUIRES_FORMAL_811_MAPPING; historical replay/resume remains separate')
    splits=json.loads((root/'splits.json').read_text());mapping=json.loads((root/'SPLIT_MAPPING.json').read_text())
    actual=sha256(root/'SPLIT_MAPPING.json')
    if actual!=splits['mapping']['sha256'] or (expected_mapping_sha and actual!=expected_mapping_sha):raise ValueError('split mapping identity differs')
    if mapping['policy']['requested_fractions']!=list(DEFAULT_FRACTIONS):raise ValueError('80/10/10 required')
    range_policy=resolve_range_policy(mapping['policy'].get('physical_range_policy'),legacy_if_missing=True)
    if manifest.get('physical_range_policy',range_policy)!=range_policy:
        raise ValueError('manifest response range policy differs from split mapping')
    if expected_range_policy is not None and resolve_range_policy(expected_range_policy)!=range_policy:
        raise ValueError('training response range policy differs from dataset')
    families=defaultdict(set)
    for r in mapping['rows']:
        if r['split'] in SPLIT_NAMES:families[r['family']].add(r['split'])
    if any(len(x)>1 for x in families.values()):raise ValueError('family split leakage')
    with np.load(root/'dataset.npz',allow_pickle=False) as a:
        expected=np.array([SPLIT_NAMES.index(mapping['by_geometry_sha256'][str(h)]) for h in a['geometry_sha256']])
        if not np.array_equal(a['split'],expected):raise ValueError('arrays do not use the new mapping')
        for values in a['y'][:,0,:]:
            if not classify_physical15(dict(zip(Y_COLUMNS,values)),range_policy)['range_eligible']:
                raise ValueError('training data response range mismatch')
        counts={s:int((expected==i).sum()) for i,s in enumerate(SPLIT_NAMES)}
    if counts!=mapping['counts'] or counts!=splits['counts']:raise ValueError('split count mismatch')
    if not counts['test'] or counts['train']!=8*counts['test'] or counts['validation']!=counts['test']:
        raise ValueError('WAITING_FOR_INDEPENDENT_HOLDOUT_QUOTAS: incomplete pool is not an 80/10/10 training set')
    return dict(status='PASS',mapping_sha256=actual,counts=counts,physical_range_policy=range_policy)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',action='append',required=True);p.add_argument('--out',required=True)
    p.add_argument('--contract',required=True);p.add_argument('--exposure-cutoff',type=int,required=True);p.add_argument('--first-doe-batch',type=int,required=True)
    p.add_argument('--target-total',type=int,default=DEFAULT_TARGET_TOTAL);p.add_argument('--seed',type=int,default=DEFAULT_SEED);p.add_argument('--previous-mapping')
    p.add_argument('--physical-range-policy',choices=RANGE_POLICY_IDS,default=RANGE_POLICY)
    p.add_argument('--exposure-policy',choices=(EXPOSURE_POLICY,LEGACY_EXPOSURE_POLICY),default=EXPOSURE_POLICY)
    p.add_argument('--exposure-map')
    a=p.parse_args();print(json.dumps(build(a.snapshot,a.out,contract_path=a.contract,policy=make_policy(a.exposure_cutoff,a.first_doe_batch,target_total=a.target_total,seed=a.seed,physical_range_policy=a.physical_range_policy,exposure_policy=a.exposure_policy),previous_mapping=a.previous_mapping,exposure_mapping=a.exposure_map)))

if __name__=='__main__':main()
