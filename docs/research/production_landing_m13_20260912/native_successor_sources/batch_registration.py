"""Append-only prepared-input registration; no simulator or process launch."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import controlled_metadata as m
import controlled_execution as e
import native_birth
import start_slots as s
from fixed48_amendment import CONTINUOUS_AUTH, FIXED48_AUTH

FIRST='569e1abda082a070db657d83f90327fe4815bd8a25b290fb6f5cc2230faf9740'
RECIPE='4257a113f15112606e290bf039d25bb83d270376b3db81cdf0634239c841c98f'
FACTORY='1591cfaa229fb79529b4fcbc893f8283d3c937edc5bc8d907d32e0a34ae7155a'
BASE='7f1bdbbefd126468fa00ea9b7ac6fcb27eb7ef120539f570cc8d667b75c910ba'
BOOKKEEPING={'source_pins','startup_recovery','concurrency_amendment','successor_registration',
    'candidate_roots','adopted_stages','original_manifest','path_map','budget_root','out','code_root',
    'intake_duplicate_ids','dedup_receipt','predecessor','selected_pass'}


def registry():return Path(__file__).resolve().parent.parent/'batch_registry'


def alive(start):
    try:p=native_birth.proc_info(start['pid'])
    except (FileNotFoundError,ProcessLookupError):return False
    return p['state']!='Z' and p['start_ticks']==int(start['start_ticks'])


def require_parent_terminal(parent_pin):
    parent=e.document(parent_pin);config=e.document(parent['config']);out=Path(config['out'])
    start_pin=e.pin(out/'START_RECEIPT.json');start=e.document(start_pin)
    m.require(start['release']==parent_pin,'PARENT_START_RELEASE_MISMATCH')
    m.require(not alive(start),'PARENT_STILL_ALIVE_NO_SUCCESSOR')
    terminal_pin=e.pin(out/'BATCH_RECEIPT.json');terminal=e.document(terminal_pin)
    m.require(terminal['status']=='ALL_NEW256_ACCOUNTED_WITHIN_BUDGET_NOT_PRODUCTION_ACCEPTANCE'
        and terminal['release']==parent_pin and terminal['N_original_requests']==256,
        'PARENT_BATCH_NOT_CLOSED')
    batch=m.load_batch(config['original_manifest'],config['path_map'])
    pins=terminal['results'];ids=[]
    m.require(len(pins)==256 and len({p['path'] for p in pins})==256,'PARENT_RESULTS_INCOMPLETE')
    for p,row in zip(pins,batch.rows):
        value=e.document(p);rid=row['request_id'];ids.append(rid)
        root=Path(config.get('candidate_roots',{}).get(rid,str(out/rid)))
        m.require(Path(p['path'])==root/'RESULT.json' and value['request_id']==rid,
            'PARENT_RESULT_ID_OR_ROOT_CHANGED')
    return dict(release=parent_pin,start=start_pin,terminal=terminal_pin,results=pins)


def register(manifest_pin,path_map,authorizations,*,factory_completion=None):
    m.require({p['sha256'] for p in authorizations}=={CONTINUOUS_AUTH,FIXED48_AUTH},'STANDING_AUTHORITY_REQUIRED')
    for p in authorizations:e.document(p)
    raw=m.read_pin(manifest_pin,path_map);manifest=m.parse(raw)
    intent=m.parse(m.read_pin(manifest['intent'],path_map))
    prep=m.parse(m.read_pin(manifest['files']['PREPARATION_RECEIPT.json'],path_map))
    rows=[m.parse(line) for line in m.read_pin(manifest['files']['SELECTED_CANDIDATES.jsonl'],path_map).splitlines()]
    m.require(intent['suggested_new_budget']['requested_parallel_jobs']==48 and
        intent['suggested_new_budget']['requested_cpu_per_job']==2,'FIXED48_TWO_CPU_REQUIRED')
    if manifest_pin['sha256']==FIRST:
        m.require(manifest['intent']['sha256']==RECIPE and factory_completion is None,'FIRST_FROZEN_INPUT_CHANGED')
    else:
        m.require(factory_completion is not None,'FACTORY_COMPLETION_REQUIRED')
        completion=e.document(factory_completion)
        m.require(completion['schema']=='eucap15_successor_factory_complete.v1' and
            completion['manifest']==manifest_pin and completion['native_dispatches']==0,'FACTORY_OUTPUT_NOT_BOUND')
        m.require(intent['original_recipe_pin']['sha256']==RECIPE and
            intent['sources']['factory']['sha256']==FACTORY,'FACTORY_SCIENTIFIC_IDENTITY_CHANGED')
        m.read_pin(intent['sources']['factory'])
        request=intent['factory_request_pin'];data=json.loads(Path(request['path']).read_bytes())
        m.require(hashlib.sha256(Path(request['path']).read_bytes()).hexdigest()==request['sha256']
            and completion['request_sha256']==request['sha256'] and data['batch_id']==intent['study_id'],
            'FACTORY_REQUEST_IDENTITY_CHANGED')
    profile=dict(study=intent['study_id'],intent_schema=intent['schema'],intent_sha256=manifest['intent']['sha256'],
        proposals_sha256=manifest['files']['SELECTED_CANDIDATES.jsonl']['sha256'],
        preparation_sha256=manifest['files']['PREPARATION_RECEIPT.json']['sha256'],seeds=intent['seeds'],
        analytic_counts=[sum(r['analytic_pass'] is True for r in rows if r['arm']==arm) for arm in m.ARMS])
    m.validate_contract(intent,rows,prep,profile)
    for p in manifest['files'].values():m.read_pin(p,path_map)
    value=dict(schema='eucap15_registered_successor_inputs.v1',manifest=manifest_pin,path_map=path_map,
        profile=profile,authorizations=authorizations,factory_completion=factory_completion,
        no_model_or_native_calls=True)
    dest=registry()/(manifest_pin['sha256']+'.json');dest.parent.mkdir(exist_ok=True)
    if dest.exists():m.require(e.document(e.pin(dest))==value,'REGISTERED_INPUT_CONFLICT')
    else:s.write_once(dest,value)
    return e.pin(dest)


def registered_profile(manifest_pin):
    value=e.document(e.pin(registry()/(manifest_pin['sha256']+'.json')))
    m.require(value['schema']=='eucap15_registered_successor_inputs.v1' and value['manifest']==manifest_pin,
        'UNREGISTERED_OR_CHANGED_MANIFEST')
    for p in value['authorizations']:e.document(p)
    m.require({p['sha256'] for p in value['authorizations']}=={CONTINUOUS_AUTH,FIXED48_AUTH},'AUTHORITY_DRIFT')
    return value['profile']


def scientific_config(config):
    replacements={p['path']:str(Path(p['path']).relative_to(config['code_root']))
        for p in config['source_pins'] if p['path'].startswith(config['code_root']+'/')}
    def norm(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256','bytes'} and v['path'] in replacements:
                return dict(runtime_relative=replacements[v['path']])
            return {k:norm(x) for k,x in v.items()}
        if isinstance(v,list):return [norm(x) for x in v]
        if isinstance(v,str) and v.startswith(config['code_root']+'/'):return 'RUNTIME/'+v[len(config['code_root'])+1:]
        return v
    return {k:norm(v) for k,v in config.items() if k not in BOOKKEEPING}


def validate_configuration(release,config,old):
    a=release['successor_registration']
    expected=scientific_config(old)
    if a.get('endpoint_upgrade'):
        from endpoint_upgrade import load, normalize_for_comparison
        upgrade=load(a['endpoint_upgrade'])
        m.require(release['endpoint_sources']==list(upgrade['changes'].values()),'SUCCESSOR_ENDPOINT_RELEASE_PINS_CHANGED')
        actual=scientific_config(normalize_for_comparison(config,a['endpoint_upgrade'],old))
    else:
        actual=scientific_config(config)
    capacity=actual['fixed48_policy']['tool_executor_capacity']
    m.require(capacity==dict(cadence=8,calibre=8,emx=48),'BOUNDED_PREPROCESSING8_EMX48_REQUIRED')
    expected['fixed48_policy']['tool_executor_capacity']=dict(cadence=8,calibre=8,emx=48)
    m.require(actual==expected,'SUCCESSOR_CHANGED_SCIENTIFIC_OR_RESOURCE_CONFIGURATION')


def validate_successor_release(release,config):
    a=release['successor_registration']
    m.require(a==config['successor_registration'] and a['schema']=='eucap15_successor_release_binding.v1',
        'SUCCESSOR_RELEASE_BINDING_CHANGED')
    reg=e.document(a['inputs'])
    m.require(config['original_manifest']==reg['manifest'] and config['path_map']==reg['path_map'],
        'SUCCESSOR_INPUT_OR_TRANSPORT_CHANGED')
    registered_profile(config['original_manifest'])
    base=e.document(a['scientific_base_release']);old=e.document(base['config'])
    m.require(a['scientific_base_release']['sha256']==BASE,'EXACT_SCIENTIFIC_BASE_REQUIRED')
    validate_configuration(release,config,old)
    root=Path(config['budget_root']);chain=Path(config['code_root']).parent
    m.require(root==chain/'batches'/reg['profile']['study'] and Path(config['out'])==root/'owner',
        'SEPARATE_BATCH_BUDGET_REQUIRED')
    parent=e.document(a['parent_release']);pc=e.document(parent['config'])
    m.require(config['original_manifest']!=pc['original_manifest'] and
        not root.is_relative_to(pc['budget_root']) and not Path(pc['budget_root']).is_relative_to(root),
        'OLD_BUDGET_MUST_NOT_BE_RESET_OR_REUSED')
    proof=e.document(a['parent_closure'])
    m.require(proof['release']==a['parent_release'],'WRONG_PARENT_CLOSURE')
    for p in [proof['start'],proof['terminal'],*proof['results']]:m.read_pin(p)
    for p in release['sources']:m.read_pin(p)
    m.require(config['adopted_stages']=={} and config['candidate_roots']=={},'NEW_BATCH_MUST_NOT_REUSE_PHYSICS')
