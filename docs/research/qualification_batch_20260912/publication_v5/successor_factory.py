"""One-shot, replay-safe input factory. Never a native scheduler or watcher."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime,timezone

from atomic_primitives import atomic_json, lease

SAMPLER_SHA='bf4a0f0a6bd514622971621684623c1eab703be367c715cb1ddc38ddd1117c27'
EXCLUSIONS_SHA='a18832156ce8307fc56a3ab5d4276bbac93b70723c4bda78f26c78f9c3cdc424'
RECIPE_SHA='4257a113f15112606e290bf039d25bb83d270376b3db81cdf0634239c841c98f'
BASE_SHA={'contract':'09aea00c966b9e6d41d423b60c82e45ef6604533493e95afce071e02ba250447',
 'splits':'92ba524fd139dfb6f9ab93bd745aa26bc7fdcde68687ae132ac1cc0b63064433',
 'current_source_rows':'8a63fe957e6bf16055f0781dc4919dbe20fc4906bc233f8ade8029c389b49e63'}
SOURCE_SHA={'acquisition':'d55eedd5955a6654966fd3dd0ad32074cae4376ef2709ffdbbcfe8edee431f0a',
 'physics':'0e58daf60c20b8b079289efed67e564320c4837d8cc3cc5ad3ed7a5e62fa3e9c',
 'evaluation':'50c36b0ca636fe992b1e7d1eebc76341129ec395c571e1a3c9226685130e456e',
 'frequency_large_eval':'05930823b7ce914f1dc5ec8dd6466fadb785cc4602f5d258db00d44b488f276c',
 'data_split':'55bd9580b61512da8d2e550b556717842b227acaf15319ecd8ec762e78383295',
 'production_geometry_helpers':'b6311d77e65b514d8a186394f6352500f7e717cc02507c0190e401ccd197bf98'}
SOURCE_MODULE={'acquisition':'research/broadband56_nn/eucap15_acquisition.py',
 'physics':'research/broadband56_nn/physics.py','evaluation':'research/broadband56_nn/evaluation.py',
 'frequency_large_eval':'research/broadband56_nn/frequency_large_eval.py',
 'data_split':'research/broadband56_nn/data.py'}
SUPPORT_MODULE={'resource_reader':('research/broadband56_nn/eucap15_prepare_acquisition.py',
 '79d36c84bbd9f24b03b0b6c34a2d06fb348e2649a3ab65471d8c19a1e5aefa24'),
 'canonical_geometry':('rfic_transformer_inverse_design/campaigns/broadband56_balanced200k.py',
 'bd82390aa822a6426ad505e99675694be2fc49e017b25c76b7c93fc78121d424')}
OUTPUT_FILES={'CANDIDATE_INDEX.csv','EXCLUSION_METADATA.json','GEOMETRY_CONTRACT.json',
 'INPUT_PINS.json','PREPARATION_RECEIPT.json','RESOURCE_CHECK.json','SELECTED_CANDIDATES.jsonl'}


def require(ok,message):
    if not ok:raise ValueError(message)


def safe(path):
    p=Path(path)
    require(p.is_absolute() and not any(x.is_symlink() for x in (p,*p.parents)), 'absolute nonsymlink path required')
    return p


def pin(path):
    p=safe(path);b=p.read_bytes()
    return dict(path=str(p),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))


def read_pin(p,mapping=None):
    require(set(p)>={'path','sha256'} and re.fullmatch('[0-9a-f]{64}',p['sha256']), 'invalid SHA pin')
    resolved=safe((mapping or {}).get(p['path'],p['path']));actual=pin(resolved)
    require(actual['sha256']==p['sha256'] and ('bytes' not in p or actual['bytes']==p['bytes']), 'source pin changed: '+p['path'])
    return resolved.read_bytes(),actual


def validate_request(r):
    required={'schema','batch_id','seeds','recipe','inputs','sources','known_pool','prior_batches','research_root'}
    require(set(r) in (required,required|{'path_map'}), 'exact explicit factory request keys required')
    require(r['schema']=='eucap15_successor_factory_request.v1', 'wrong request schema')
    require(isinstance(r['batch_id'],str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{4,120}',r['batch_id']), 'unsafe batch identity')
    seeds=r['seeds'];require(set(seeds)=={'doe','neighbor','split'} and seeds['split']==17,'fixed split17 required')
    require(all(type(seeds[k]) is int and 0<=seeds[k]<2**32 for k in ('doe','neighbor')) and
            seeds['doe']!=seeds['neighbor'], 'explicit distinct uint32 seed pair required')
    require(set(r['inputs'])==set(BASE_SHA) and all(r['inputs'][k]['sha256']==h for k,h in BASE_SHA.items()), 'frozen3801 baseline identity changed')
    require(set(r['sources'])==set(SOURCE_SHA) and all(r['sources'][k]['sha256']==h for k,h in SOURCE_SHA.items()), 'frozen geometry code identity changed')
    require(r['recipe']['sha256']==RECIPE_SHA,'recipe changed')
    require(isinstance(r['prior_batches'],list) and r['prior_batches'],'published prior candidate manifests required')
    safe(r['research_root'])


def prepare_intent(r,request_pin):
    """Read only explicitly named closed metadata; never scan a pool/directory."""
    validate_request(r);mapping=r.get('path_map',{})
    def read(p):return json.loads(read_pin(p,mapping)[0])
    def resolved(p):return read_pin(p,mapping)[1]
    recipe=read(r['recipe']);known=r['known_pool']
    require(set(known)=={'manifest','file'},'known pool needs published manifest/file pins')
    kp=read(known['manifest'])
    require(kp['schema']=='eucap15_production_input_manifest.v1' and
            kp['status']=='PREPARATION_ONLY_NOT_NATIVE_RELEASE' and
            kp['files']['EXCLUSION_METADATA.json']==known['file'],'known pool not closed-manifest-bound')
    known_resolved=resolved(known['file']);prior=[];manifests=[];seen=set();known_candidate_bound=False
    for parent in r['prior_batches']:
        require(set(parent)=={'manifest','candidates'},'prior candidate pair required')
        m=read(parent['manifest']);h=parent['manifest']['sha256']
        require(h not in seen,'prior manifest duplicated');seen.add(h)
        require(m['schema']=='eucap15_production_input_manifest.v1' and
                m['status']=='PREPARATION_ONLY_NOT_NATIVE_RELEASE' and
                m['files']['SELECTED_CANDIDATES.jsonl']==parent['candidates'],'prior candidates not published-manifest-bound')
        parent_intent=read(m['intent'])
        require(m['study_id']==parent_intent['study_id'] and m['study_id']!=r['batch_id'],'batch identity duplicates a parent')
        require(not ({r['seeds'][k] for k in ('doe','neighbor')} &
            {parent_intent['seeds'][k] for k in ('doe','neighbor')}), 'seed duplicates explicit prior batch')
        prior.append(resolved(parent['candidates']));manifests.append(parent['manifest'])
        known_candidate_bound |= h==known['manifest']['sha256']
    require(known_candidate_bound,'include the known-pool owning batch candidates; snapshot excludes its own new proposals')
    sources={k:resolved(v) for k,v in r['sources'].items()}
    # Pin the actual modules imported through PYTHONPATH, not an unrelated good copy.
    for name,relative in SOURCE_MODULE.items():
        actual=pin(safe(r['research_root'])/relative)
        require(actual['sha256']==SOURCE_SHA[name],'imported source changed: '+name)
        sources[name]=actual
    for name,(relative,expected) in SUPPORT_MODULE.items():
        actual=pin(safe(r['research_root'])/relative)
        require(actual['sha256']==expected,'support source changed: '+name)
        sources[name]=actual
    here=Path(__file__).resolve().parent
    sampler=pin(here/'parameterized_sampler.py');helper=pin(here/'successor_exclusions.py')
    require(sampler['sha256']==SAMPLER_SHA and helper['sha256']==EXCLUSIONS_SHA,'factory package source changed')
    sources.update(prepare=sampler,successor_exclusions=helper,factory=pin(__file__))
    intent={k:recipe[k] for k in ('counts','order','neighborhood','physical_qualification_scope','suggested_new_budget','source_policy')}
    intent.update(schema='eucap15_successor_factory_sampler_intent.v1',study_id=r['batch_id'],seeds=r['seeds'],
        inputs={k:resolved(v) for k,v in r['inputs'].items()},sources=sources,known_pool=known_resolved,
        prior_candidate_files=prior,published_parent_manifests=manifests,factory_request_pin=request_pin,
        original_recipe_pin=r['recipe'],source_path_map=mapping,native_release=False)
    require(intent['counts']=={'GEOMETRY_DOE':192,'TRAIN_NEIGHBORHOOD':64},'recipe counts changed')
    budget=intent['suggested_new_budget']
    require((budget['maximum_actual_EMX_starts'],budget['maximum_wall_seconds'],budget['maximum_incremental_bytes'],
             budget['requested_parallel_jobs'],budget['requested_cpu_per_job'])==(256,43200,5368709120,48,2),'budget request changed')
    return intent


def invoke_sampler(intent_pin,out,research_root):
    """A single local Python preparer call, not a simulation or scheduling loop."""
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(safe(research_root)),
             OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2')
    cmd=[sys.executable,'-B',str(Path(__file__).resolve().parent/'parameterized_sampler.py'),
         '--intent',intent_pin['path'],'--intent-sha',intent_pin['sha256'],'--out',str(out)]
    p=subprocess.run(cmd,env=env,text=True,capture_output=True,check=False)
    return dict(command=cmd,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)


def check_bindings(request_pin):
    """Read/hash exact declared inputs only; no registration or new proposals."""
    raw,_=read_pin(request_pin);r=json.loads(raw);validate_request(r);mapping=r.get('path_map',{})
    declared=[r['recipe'],*r['inputs'].values(),*r['sources'].values(),*r['known_pool'].values()]
    declared += [p for pair in r['prior_batches'] for p in pair.values()]
    declared += [dict(path=str(safe(r['research_root'])/rel),sha256=SOURCE_SHA[name])
                 for name,rel in SOURCE_MODULE.items()]
    declared += [dict(path=str(safe(r['research_root'])/rel),sha256=h) for rel,h in SUPPORT_MODULE.values()]
    missing=[];checked=[];seen=set()
    def inspect(p):
        key=(p['path'],p['sha256'])
        if key in seen:return
        seen.add(key)
        try:
            raw,actual=read_pin(p,mapping);checked.append(actual);return raw
        except (OSError,ValueError) as exc:
            missing.append(dict(required=p,resolved_path=mapping.get(p['path'],p['path']),
                error_type=type(exc).__name__,error=str(exc)))
    for p in declared:inspect(p)
    # Parent intent pins are the only additional metadata edges required here.
    for parent in r['prior_batches']:
        try:
            m=json.loads(read_pin(parent['manifest'],mapping)[0]);inspect(m['intent'])
        except (OSError,ValueError,KeyError):pass
    if missing:return dict(status='MISSING_OR_CHANGED_BINDINGS',missing=missing,checked=checked,
        candidate_generation_calls=0,native_dispatches=0,installed=False)
    prepare_intent(r,request_pin)
    return dict(status='INPUT_BINDINGS_READY_NOT_INSTALLED',missing=[],checked=checked,
        candidate_generation_calls=0,native_dispatches=0,installed=False)


def verify_output(batch,registered,expected_manifest=None):
    root=batch/'run_v1';mp=expected_manifest or pin(root/'MANIFEST.json')
    require(Path(mp['path'])==root/'MANIFEST.json','foreign output manifest')
    m=json.loads(read_pin(mp)[0]);require(m['schema']=='eucap15_production_input_manifest.v1' and
        m['status']=='PREPARATION_ONLY_NOT_NATIVE_RELEASE' and m['study_id']==registered['batch_id'] and
        m['intent']==registered['intent'] and set(m['files'])==OUTPUT_FILES,'incomplete/foreign output manifest')
    for name,p in m['files'].items():
        require(Path(p['path'])==root/name,'output pin outside registered batch');read_pin(p)
    receipt=json.loads((root/'PREPARATION_RECEIPT.json').read_bytes())
    require(receipt['status']=='FROZEN_256_PROPOSALS_NOT_NATIVE_RELEASE' and
        receipt['study_id']==registered['batch_id'] and receipt['source_train_count']==3801 and
        receipt['counts']['GEOMETRY_DOE']['proposals']==192 and receipt['counts']['TRAIN_NEIGHBORHOOD']['proposals']==64 and
        receipt['actual_emx_starts']==receipt['model_calls']==receipt['training_updates']==0,'wrong sampler completion')
    return mp


def existing(batch,request_sha):
    registration=batch/'REGISTERED.json'
    require(registration.is_file(),'PARTIAL_REGISTRATION_NO_RESAMPLE')
    registered=json.loads(registration.read_bytes())
    require(registered['batch_id']==batch.name,'registration identity conflicts with directory')
    require(registered['request_sha256']==request_sha,'BATCH_INPUT_CONFLICT')
    read_pin(registered['intent'])
    done=batch/'COMPLETED.json'
    if done.exists():
        completed=json.loads(done.read_bytes())
        require(completed['request_sha256']==request_sha,'completion input conflict')
        mp=verify_output(batch,registered,completed['manifest'])
    else:
        require((batch/'run_v1/MANIFEST.json').is_file(),'PARTIAL_OUTPUT_NO_RESAMPLE')
        mp=verify_output(batch,registered)
        atomic_json(done,dict(schema='eucap15_successor_factory_complete.v1',request_sha256=request_sha,
            manifest=mp,native_dispatches=0,recovered_complete_manifest_without_resampling=True),immutable=True)
    return dict(status='REUSED_EXACT_COMPLETE_MANIFEST',manifest=mp,sampled_now=False,native_dispatches=0)


def generate_once(request_pin,registry_root):
    raw,_=read_pin(request_pin);r=json.loads(raw);validate_request(r)
    root=safe(registry_root);root.mkdir(parents=True,exist_ok=True)
    batch=root/'batches'/r['batch_id'];request_sha=hashlib.sha256(raw).hexdigest()
    with lease(root/'INPUT_FACTORY.lock'):
        if batch.exists():return existing(batch,request_sha)
        # Deterministic seed claim lookups, never a directory/full-history scan.
        for role in ('doe','neighbor'):
            claim=root/'seed_claims'/f"seed_{r['seeds'][role]}.json"
            require(not claim.exists(),'SEED_ALREADY_REGISTERED_NO_RESAMPLE')
        intent=prepare_intent(r,request_pin)
        batch.mkdir(parents=True,exist_ok=False)
        try:
            atomic_json(batch/'INTENT.json',intent,immutable=True)
            ip=pin(batch/'INTENT.json')
            registered=dict(schema='eucap15_successor_factory_registration.v1',batch_id=r['batch_id'],
                request_sha256=request_sha,source_request=request_pin,intent=ip,seeds=r['seeds'],
                budget=intent['suggested_new_budget'],native_queue_registered=False)
            atomic_json(batch/'REGISTERED.json',registered,immutable=True)
            for role in ('doe','neighbor'):
                atomic_json(root/'seed_claims'/f"seed_{r['seeds'][role]}.json",
                    dict(batch_id=r['batch_id'],request_sha256=request_sha,role=role,seed=r['seeds'][role]),immutable=True)
            execution=invoke_sampler(ip,batch/'run_v1',r['research_root'])
            atomic_json(batch/'SAMPLER_EXECUTION.json',execution,immutable=True)
            require(execution['returncode']==0,'SAMPLER_FAILED_NO_RESAMPLE')
            mp=verify_output(batch,registered)
            atomic_json(batch/'COMPLETED.json',dict(schema='eucap15_successor_factory_complete.v1',
                request_sha256=request_sha,manifest=mp,native_dispatches=0),immutable=True)
            return dict(status='NEW_FROZEN_INPUTS_NOT_NATIVE_RELEASE',manifest=mp,sampled_now=True,native_dispatches=0)
        except Exception as e:
            if not (batch/'FAILURE.json').exists():
                atomic_json(batch/'FAILURE.json',dict(status='FAIL_NO_AUTOMATIC_RESAMPLE',error_type=type(e).__name__,error=str(e)),immutable=True)
            raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True);p.add_argument('--request-sha',required=True)
    p.add_argument('--registry-root',type=Path)
    p.add_argument('--check-inputs-only',action='store_true');a=p.parse_args()
    request_pin=dict(path=str(a.request.absolute()),sha256=a.request_sha)
    if a.check_inputs_only:result=check_bindings(request_pin)
    else:
        require(a.registry_root is not None,'persistent --registry-root required for generation')
        result=generate_once(request_pin,a.registry_root)
    print(json.dumps(result));return 0 if not result.get('missing') else 2


if __name__=='__main__':raise SystemExit(main())
