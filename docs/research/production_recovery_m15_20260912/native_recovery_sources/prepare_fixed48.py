"""Consolidated same256 concurrency-only release after a proven handoff."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

import controlled_execution as e
import controlled_metadata as m
import start_slots as s

PARENT_SHA='a0834643c375703bbd1dea3c7e778b31a6321b42abba3f33e504bf192ea27a81'


def prepare(root):
    root=s.path_ok(root)
    inputs=e.document(e.pin(root/'TRANSPORT_INPUTS.json'))
    pp=inputs['parent_release'];m.require(pp['sha256']==PARENT_SHA,'EXACT_ACTIVE_PARENT_REQUIRED')
    parent=e.document(pp);old=e.document(parent['config']);old_out=Path(old['out'])
    quiet=e.document(inputs['quiescent']);handoff=e.document(inputs['handoff'])
    m.require(handoff['status']=='CONTROLLED_CONCURRENCY_HANDOFF_NO_CHILD_SIGNAL','REAL_HANDOFF_REQUIRED')
    for p in [*quiet['results'],*quiet['slots']]:m.read_pin(p)
    plan=quiet['plan'];origin=e.document(plan)['release']
    code=root/'runtime';out=root/'owner';out.mkdir(exist_ok=False);(root/'exports').mkdir(exist_ok=False)
    sources=[e.pin(p) for p in sorted(code.rglob('*.py')) if not p.name.startswith('test_')]
    changes=[]
    for p in sources:
        relative=Path(p['path']).relative_to(code)
        before=Path(old['code_root'])/relative
        changes.append(dict(relative=str(relative),new=p,parent=e.pin(before) if before.exists() else None))
    def relocate(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256','bytes'} and v['path'].startswith(old['code_root']+'/'):
                return e.pin(code/v['path'][len(old['code_root'])+1:])
            return {k:relocate(x) for k,x in v.items()}
        if isinstance(v,list):return [relocate(x) for x in v]
        if isinstance(v,str):
            for old_path,new_path in ((old['code_root'],str(code)),(old['out'],str(out))):
                if v==old_path or v.startswith(old_path+'/'):return new_path+v[len(old_path):]
        return v
    config=relocate(deepcopy(old))
    config['max_native_concurrency']=config['global_simulator_concurrency']=48
    config['resource_budget']['max_global_solvers']=48
    config['fixed48_policy']=dict(requested_emx=48,cpu_per_solver=2,
        normalized_load1_max=1.10,normalized_load5_max=1.10,minimum_available_memory_fraction=.20,
        system_cpu_reserve=4,cpu_reservation=dict(cadence=2,calibre=2,emx=2),
        memory_reservation_bytes={t:8*1024**3 for t in ('cadence','calibre','emx')},
        tool_executor_capacity=dict(cadence=1,calibre=1,emx=48),candidate_pipeline_capacity=64,
        candidate_projected_peak_bytes=64*1024**2,
        reservation_basis='CONSERVATIVE_CONFIGURED_PROJECTION_NOT_ASSERTED_HARD_PEAK_BOUND',
        five_independent_health_checks=5,maximum_snapshot_age_seconds=90)
    m.require(config['fixed48_policy']==old['fixed48_policy'],'SAME48_POLICY_MUST_NOT_CHANGE')
    amendment=dict(schema='eucap15_fixed48_concurrency_amendment.v1',parent_release=pp,
        parent_start=e.pin(old_out/'START_RECEIPT.json'),budget_origin_release=origin,original_plan=plan,
        authorizations=inputs['authorizations'],handoff=inputs['handoff'],quiescent=inputs['quiescent'],
        prior_results=quiet['results'],prior_slots=quiet['slots'],source_replacements=changes,
        recovery_kind='SAME_BATCH_FIXED48_HOTPATH_RECOVERY')
    config['concurrency_amendment']=amendment
    batch=m.load_batch(config['original_manifest'],config['path_map'])
    closed={Path(p['path']).parent.name:p for p in quiet['results']}
    config['candidate_roots']={j['request_id']:str(Path(closed[j['request_id']]['path']).parent) if j['request_id'] in closed
        else str(out/j['request_id']) for j in batch.rows}
    config['adopted_stages']={}
    config['recovery_unresolved']=quiet.get('unresolved_candidates',{})
    for job in batch.rows:
        rid=job['request_id'];dest=out/rid;dest.mkdir(exist_ok=False)
        if rid in closed:continue
        prior=old_out/rid
        shutil.copyfile(prior/'cadence_candidates.csv',dest/'cadence_candidates.csv')
        bindings=relocate(e.document(e.pin(prior/'OWNER_BINDINGS.json')))
        s.write_once(dest/'OWNER_BINDINGS.json',bindings)
        stages={}
        for name,dirname in (('cadence','cadence_only'),('gds_audit','gds_audit'),('calibre','calibre')):
            process=prior/(name+'_PROCESS.json')
            process_pin=e.pin(process) if process.exists() else old.get('adopted_stages',{}).get(rid,{}).get(name)
            if process_pin is None:break
            value=e.document(process_pin)
            if value['returncode']!=0 or value['completion'] is None:break
            m.read_pin(value['completion'])
            # Identical private files, not rerun tools or altered geometry.
            # Hard links keep actual allocated-inode accounting unchanged.
            shutil.copytree(prior/dirname,dest/dirname,copy_function=__import__('os').link)
            stages[name]=process_pin
        if stages:config['adopted_stages'][rid]=stages
    config['source_pins']=[*sources,*[p for p in old['source_pins'] if not p['path'].startswith(old['code_root']+'/')],
        inputs['handoff'],inputs['quiescent'],*inputs['authorizations'],e.pin(root/'TRANSPORT_INPUTS.json')]
    s.write_once(out/'CONFIG.json',config)
    release=dict(schema=parent['schema'],created_utc=e.now(),authority=parent['authority'],config=e.pin(out/'CONFIG.json'),
        sources=sources,parent_release=pp,calibre_wrapper=e.pin(code/'research/broadband56_nn/frequency_research_calibre.py'),
        endpoint_sources=parent['endpoint_sources'],concurrency_amendment=amendment,
        startup_recovery=parent['startup_recovery'],native_execution_started=False)
    s.write_once(out/'RELEASE.json',release)
    e.plan_release(release,config)
    sys.path[:0]=[str(code),config['repo']]
    from research.broadband56_nn.eucap15_controlled_context import build_request
    for job in batch.rows:
        if job['request_id'] in closed:continue
        dest=out/job['request_id'];bindings=e.document(e.pin(dest/'OWNER_BINDINGS.json'))
        s.write_once(dest/'GDS_REQUEST.json',build_request(batch.manifest_pin,job['request_id'],'gds',bindings))
    receipt=dict(status='FIXED48_PACKAGE_PREPARED_NOT_NATIVE_ADMISSION',release=e.pin(out/'RELEASE.json'),
        original_plan=plan,old_closed_results=len(closed),adopted_partial_candidates=len(config['adopted_stages']),
        requested_emx=48,executor_capacity=48,new_native_calls=0)
    s.write_once(out/'PREPARE_RECEIPT.json',receipt);print(json.dumps(receipt))


if __name__=='__main__':prepare(Path(sys.argv[1]))
