"""New frozen batch inputs and independent budget, same scientific configuration."""
from copy import deepcopy
import csv
from pathlib import Path
import sys
import controlled_execution as e
import controlled_metadata as m
import start_slots as s
import batch_registration as registration
from live_dedup import Ledger


def prepare(chain, input_registration, parent_pin):
    chain=Path(chain);deployment=e.document(e.pin(chain/'DEPLOYMENT.json'))
    reg=e.document(input_registration);parent=e.document(parent_pin);old=e.document(parent['config'])
    closure=registration.require_parent_terminal(parent_pin)
    root=chain/'batches'/reg['profile']['study']
    m.require(not root.exists(),'EXISTING_SUCCESSOR_NO_BUDGET_RESET')
    root.mkdir(parents=True);out=root/'owner';out.mkdir();(root/'exports').mkdir()
    s.write_once(root/'PARENT_CLOSURE.json',closure)
    code=chain/'runtime';sources=[e.pin(p) for p in sorted(code.rglob('*.py'))]
    oldcode=old['code_root']
    def relocate(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256','bytes'} and v['path'].startswith(oldcode+'/'):
                return e.pin(code/v['path'][len(oldcode)+1:])
            return {k:relocate(x) for k,x in v.items()}
        if isinstance(v,list):return [relocate(x) for x in v]
        if isinstance(v,str) and (v==oldcode or v.startswith(oldcode+'/')):return str(code)+v[len(oldcode):]
        return v
    config=relocate(deepcopy(old))
    config['fixed48_policy']['tool_executor_capacity']=dict(cadence=8,calibre=8,emx=48)
    for k in ('startup_recovery','concurrency_amendment','recovery_unresolved'):config.pop(k,None)
    batch=m.load_batch(reg['manifest'],reg['path_map'])
    dedup=Ledger(deployment['formal_ledger']);checks=[dedup.check(row) for row in batch.rows]
    duplicates=[v['request_id'] for v in checks if v['matches']]
    duplicates += [r['request_id'] for r in batch.rows if r['duplicate_reasons'] and r['request_id'] not in duplicates]
    s.write_once(out/'INTAKE_DEDUP_RECEIPT.json',dict(new_duplicate_ids=duplicates,checks=checks,
        original_duplicates_retained=True,full_history_exhaustive=False))
    binding=dict(schema='eucap15_successor_release_binding.v1',inputs=input_registration,
        parent_release=parent_pin,parent_closure=e.pin(root/'PARENT_CLOSURE.json'),
        scientific_base_release=deployment['initial_release'],formal_ledger=deployment['formal_ledger'])
    config.update(code_root=str(code),out=str(out),budget_root=str(root),original_manifest=batch.manifest_pin,
        path_map=reg['path_map'],successor_registration=binding,candidate_roots={},adopted_stages={},
        selected_pass=sum(r['analytic_pass'] for r in batch.rows),intake_duplicate_ids=duplicates,
        dedup_receipt=e.pin(out/'INTAKE_DEDUP_RECEIPT.json'),
        predecessor=dict(release=parent_pin,terminal=closure['terminal'],start=closure['start']))
    config['source_pins']=[*sources,*[p for p in old['source_pins'] if not p['path'].startswith(oldcode+'/')
        and p not in old.get('concurrency_amendment',{}).get('prior_results',[])],input_registration,
        config['dedup_receipt'],e.pin(root/'PARENT_CLOSURE.json'),e.pin(chain/'DEPLOYMENT.json')]
    if deployment.get('endpoint_upgrade'):
        from endpoint_upgrade import apply
        base=e.document(e.document(deployment['initial_release'])['config'])
        binding['endpoint_upgrade']=deployment['endpoint_upgrade']
        config=apply(config,deployment['endpoint_upgrade'],base)
    s.write_once(out/'CONFIG.json',config)
    release=dict(schema=parent['schema'],created_utc=e.now(),authority=parent['authority'],
        config=e.pin(out/'CONFIG.json'),sources=sources,parent_release=parent_pin,
        calibre_wrapper=e.pin(code/'research/broadband56_nn/frequency_research_calibre.py'),
        endpoint_sources=parent['endpoint_sources'],successor_registration=binding,native_execution_started=False)
    if deployment.get('endpoint_upgrade'):
        upgrade=e.document(deployment['endpoint_upgrade'])
        release['endpoint_sources']=list(upgrade['changes'].values())
    s.write_once(out/'RELEASE.json',release);registration.validate_successor_release(release,config)
    sys.path[:0]=[str(code),config['repo']]
    from research.broadband56_nn.eucap15_controlled_context import build_request
    for row in batch.rows:
        directory=out/row['request_id'];directory.mkdir()
        fields=dict(candidate_id=row['candidate_id'],candidate_id_sha256=batch.candidate(row['request_id'])['candidate_id_sha256'],
            geometry_id='research-'+row['canonical_geometry_sha256'],geometry_sha256=row['canonical_geometry_sha256'],
            candidate_geometry_identity_sha256=row['canonical_geometry_sha256'],acquisition_source=row['source'])
        fields.update({'geom__'+k:v for k,v in zip(row['geometry_fields'],row['geometry'])})
        with (directory/'cadence_candidates.csv').open('x',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(fields));writer.writeheader();writer.writerow(fields)
        b=dict(path_map=reg['path_map'],gds_runtime=config['gds_runtime'],
            cadence=dict(root=str(directory/'cadence_only'),routes={row['candidate_id']:'parallel_shards/shard_000'}))
        s.write_once(directory/'OWNER_BINDINGS.json',b)
        s.write_once(directory/'GDS_REQUEST.json',build_request(batch.manifest_pin,row['request_id'],'gds',b))
    s.write_once(out/'PREPARE_RECEIPT.json',dict(status='SUCCESSOR_PREPARED_NOT_STARTED',release=e.pin(out/'RELEASE.json'),
        manifest=batch.manifest_pin,requests=len(batch.rows),native_calls=0,budget_reset=False,
        maximum_starts=256,maximum_seconds=43200,maximum_bytes=5368709120))
    return e.pin(out/'RELEASE.json')
