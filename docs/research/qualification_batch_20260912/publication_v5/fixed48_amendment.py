"""Explicit concurrency-only rebind; immutable budget origin is not the parent."""
from pathlib import Path

import controlled_metadata as m

CONTINUOUS_AUTH='a1ba80c38f9efbd995648035de288648c91130c1f0e7b20989063cf2e07f5857'
FIXED48_AUTH='3079f3a21033db825e9fef76b26b230c64a830307fb503242dad275fad889584'


def validate(release, config, document, pin, parent_origin):
    a=release['concurrency_amendment']
    m.require(a==config['concurrency_amendment'] and
        a['schema']=='eucap15_fixed48_concurrency_amendment.v1', 'AMENDMENT_BINDING_REQUIRED')
    m.require({x['sha256'] for x in a['authorizations']}=={CONTINUOUS_AUTH,FIXED48_AUTH},
        'EXACT_CONTINUOUS_AND_FIXED48_AUTHORITY_REQUIRED')
    for p in a['authorizations']:document(p)
    parent=document(a['parent_release']);old=document(parent['config'])
    original=document(a['original_plan'])
    m.require(original['release']==a['budget_origin_release']==parent_origin and
        a['original_plan']==pin(Path(old['budget_root'])/'start_ledger/PLAN.json'),
        'IMMUTABLE_BUDGET_ORIGIN_NOT_IMMEDIATE_RUNTIME_PARENT')
    m.require(config['original_manifest']==old['original_manifest'] and
        config['budget_root']==old['budget_root'] and
        Path(config['out']).parent.is_relative_to(Path(old['budget_root'])/'recoveries'),
        'SAME_ORIGINAL_BATCH_AND_BUDGET_REQUIRED')
    handoff=document(a['handoff']);quiet=document(a['quiescent']);start=document(a['parent_start'])
    m.require(start['release']==a['parent_release'] and
        handoff['pid']==start['pid'] and handoff['start_ticks']==int(start['start_ticks']) and
        handoff['status']=='CONTROLLED_CONCURRENCY_HANDOFF_NO_CHILD_SIGNAL' and
        handoff['child_signals']==handoff['healthy_solver_kills']==0 and
        handoff['plan_unchanged'] is True and handoff['result_pins_unchanged'] is True and
        quiet['processes']==dict(descendants=[],native=[]) and quiet['plan']==a['original_plan'],
        'SAFE_IMMEDIATE_PARENT_HANDOFF_REQUIRED')
    m.require(a['prior_results']==quiet['results'] and a['prior_slots']==quiet['slots'],
        'COMPLETE_RECOVERY_CHECKPOINT_REQUIRED')
    for p in [*a['prior_results'],*a['prior_slots']]:m.read_pin(p)
    replacements={p['new']['path']:p['parent'] for p in a['source_replacements'] if p['parent'] is not None}
    for p in a['source_replacements']:
        m.read_pin(p['new'])
        if p['parent'] is not None:m.read_pin(p['parent'])
    def normalize(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256','bytes'} and v['path'] in replacements:
                m.require(v==pin(v['path']),'REPLACEMENT_IDENTITY_DRIFT')
                return replacements[v['path']]
            return {k:normalize(x) for k,x in v.items()}
        if isinstance(v,list):return [normalize(x) for x in v]
        if isinstance(v,str):
            for new,previous in ((config['code_root'],old['code_root']),(config['out'],old['out'])):
                if v==new or v.startswith(new+'/'):return previous+v[len(new):]
        return v
    excluded={'source_pins','concurrency_amendment','fixed48_policy','candidate_roots','adopted_stages'}
    normalized={k:normalize(v) for k,v in config.items() if k not in excluded}
    expected={k:v for k,v in old.items() if k not in excluded}
    for key in ('max_native_concurrency','global_simulator_concurrency'):
        if key in expected:
            m.require(normalized[key]==48 and expected[key]==1,'ONLY_SERIAL_TO_FIXED48_ALLOWED')
            normalized[key]=1
    m.require(normalized['resource_budget']['max_global_solvers']==48 and
        expected['resource_budget']['max_global_solvers']==1,'INNER_FIXED48_BUDGET_REQUIRED')
    normalized['resource_budget']['max_global_solvers']=1
    m.require(normalized==expected,'CONCURRENCY_AMENDMENT_CHANGED_OTHER_CONFIGURATION')
    rows=original['candidates'];closed={Path(p['path']).parent.name:p for p in a['prior_results']}
    m.require(set(config['candidate_roots'])=={r['request_id'] for r in rows.values()},
        'COMPLETE_ORIGINAL256_ROOT_MAP_REQUIRED')
    for rid,root in config['candidate_roots'].items():
        expected_root=Path(closed[rid]['path']).parent if rid in closed else Path(config['out'])/rid
        m.require(Path(root)==expected_root,'RELOCATED_OR_UNKNOWN_CANDIDATE_ROOT')
    m.require(set(config['adopted_stages'])<=set(config['candidate_roots']), 'FOREIGN_ADOPTED_CANDIDATE')
    for rid,stages in config['adopted_stages'].items():
        m.require(rid not in closed, 'COMPLETED_CANDIDATE_MUST_NOT_BE_DISPATCHED')
        for name,p in stages.items():
            m.require(name in ('cadence','gds_audit','calibre') and
                Path(p['path'])==Path(old['out'])/rid/(name+'_PROCESS.json'), 'FOREIGN_ADOPTED_STAGE')
            value=document(p)
            m.require(value['returncode']==0 and value['intent']['release']==a['parent_release'],
                'ONLY_COMPLETED_PARENT_STAGES_MAY_BE_REUSED')
            m.read_pin(value['completion'])
    return parent_origin
