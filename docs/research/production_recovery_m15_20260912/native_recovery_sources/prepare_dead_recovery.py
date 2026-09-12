"""Prepare a tested same-batch release after observed natural parent failure."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parent/'samebatch_hotpath_v1'))
from stage_recovery import SSH,PYTHON,RN,pin

baseline=Path(sys.argv[1]);prior=json.loads(baseline.read_bytes())
assert prior['owner'] is None and prior['standby'] is None and prior['closed_candidates']==231
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
out=D/('prepare_'+stamp);out.mkdir()
root=RN+'/production256_integrated_20260912T032852585189Z/recoveries/claimwait_'+stamp
files={str(p.relative_to(D)):p for p in (D/'runtime').rglob('*.py')}
files.pop('runtime/continue_batches.py')
files['test_storage_claim_wait.py']=D/'test_storage_claim_wait.py'
transport={n:dict(pin=pin(p),raw=base64.b64encode(p.read_bytes()).decode()) for n,p in files.items()}
source='ROOT='+repr(root)+'\nPARENT='+repr(prior['release'])+'\nFILES='+repr(transport)+'\nEXPECTED_RESULT_PINS='+repr(prior['current_result_pins'])+'\n'+r'''
from contextlib import ExitStack
import base64,hashlib,json,os,subprocess,sys
from pathlib import Path
root=Path(ROOT);assert not root.exists();root.mkdir(mode=0o700)
old_owner=Path(PARENT['path']).parent
for name,item in FILES.items():
 raw=base64.b64decode(item['raw']);assert len(raw)==item['pin']['bytes'] and hashlib.sha256(raw).hexdigest()==item['pin']['sha256']
 target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
 with target.open('xb') as f:f.write(raw)
# Local continuation was revised after a083 deployment; preserve the exact
# deployed parent copy in this same-batch owner package, not that later copy.
with (root/'runtime/continue_batches.py').open('xb') as f:
 f.write((old_owner.parent/'runtime/continue_batches.py').read_bytes())
sys.path.insert(0,str(root/'runtime'))
import controlled_execution as e,controlled_metadata as m,start_slots as s,native_birth
from native_resource_probe import competing_processes
from recovery_inventory import inventory
parent=e.document(PARENT);config=e.document(parent['config'])
assert PARENT['sha256']=='a0834643c375703bbd1dea3c7e778b31a6321b42abba3f33e504bf192ea27a81'
assert Path(sys.executable).resolve()==Path(config['python']).resolve()
changed=[]
for name in (root/'runtime').rglob('*.py'):
 old=Path(config['code_root'])/name.relative_to(root/'runtime')
 if e.pin(name)['sha256']!=e.pin(old)['sha256']:changed.append(str(name.relative_to(root/'runtime')))
assert sorted(changed)==['fixed48_runtime.py','prepare_fixed48.py'],changed
env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',OLD_MANAGER_PATH=str(Path(config['code_root'])/'fixed48_runtime.py'))
test=subprocess.run([config['python'],'-B',str(root/'test_storage_claim_wait.py')],env=env,capture_output=True,text=True,timeout=60)
s.write_once(root/'TEST_RECEIPT.json',dict(utc=e.now(),status='PASS' if test.returncode==0 else 'FAIL',returncode=test.returncode,
 stdout=test.stdout,stderr=test.stderr,source=e.pin(root/'test_storage_claim_wait.py'),old_manager=e.pin(Path(config['code_root'])/'fixed48_runtime.py'),
 new_manager=e.pin(root/'runtime/fixed48_runtime.py'),evidence_class='SYNTHETIC_STORAGE_RACE_REAL_PRODUCER_CONSUMER_NO_NATIVE',native_calls=0))
assert test.returncode==0,test.stderr
start_pin=e.pin(old_owner/'START_RECEIPT.json');start=e.document(start_pin)
assert start['release']==PARENT
def dead():
 try:v=native_birth.proc_info(start['pid'])
 except (FileNotFoundError,ProcessLookupError):v=None
 assert v is None or v['state']=='Z' or v['start_ticks']!=int(start['start_ticks']),'OLD_OWNER_ALIVE'
 assert not competing_processes(config),'SURVIVING_NATIVE_CHAIN'
 return v
assert not (old_owner/'BATCH_RECEIPT.json').exists()
events_pin=e.pin(old_owner/'OWNER_EVENTS.jsonl')
events=[json.loads(line) for line in Path(events_pin['path']).read_text().splitlines()]
assert events[-1]['status']=='FIXED48_OWNER_EXIT_NO_CHILD_SIGNAL' and events[-1]['error']=='MetadataError: PROJECTED_INFLIGHT_STORAGE_UNAVAILABLE'
dead()
sys.path.insert(0,config['repo'])
from research.broadband56_nn.frequency_research_emx import global_lease
with ExitStack() as stack:
 for p in (old_owner/'OWNER.lock',config['production_lock_path'],config['global_lock_path']):stack.enter_context(global_lease(p))
 dead();checkpoint=inventory(PARENT)
 expected={p['path']:p for p in [*parent['concurrency_amendment']['prior_results'],*EXPECTED_RESULT_PINS]}
 assert len(expected)==231 and {p['path']:p for p in checkpoint['results']}==expected
 assert not checkpoint['awaiting_completed_emx_result'] and not checkpoint['unresolved_candidates']
 remaining={rid:v for rid,v in checkpoint['candidate_states'].items() if v['state']!='CLOSED'}
 assert len(remaining)==25
 assert all(all(v.get('state')=='NOT_DISPATCHED' for v in row['stages'].values()) for row in remaining.values()),'UNSTARTED25_SCOPE_MISMATCH'
 hand=root/'handoff';hand.mkdir()
 quiet=dict(utc=e.now(),processes=dict(descendants=[],native=[]),**checkpoint,
  parent_naturally_dead=True,parent_frozen_no_dispatch=False,terminal_event=events[-1],terminal_event_file=events_pin)
 s.write_once(hand/'QUIESCENT_CONTROLLER.json',quiet)
 s.write_once(hand/'RECEIPT.json',dict(status='CONTROLLED_CONCURRENCY_HANDOFF_NO_CHILD_SIGNAL',utc=e.now(),
  pid=start['pid'],start_ticks=int(start['start_ticks']),parent=PARENT,plan_unchanged=True,result_pins_unchanged=True,
  child_signals=0,healthy_solver_kills=0,old_runtime_modified=False,old_budget_reset=False,
  mechanism='NATURALLY_EXITED_PARENT_VERIFIED_DEAD_AND_SHARED_LEASES_ACQUIRED_NO_SIGNAL',quiet=e.pin(hand/'QUIESCENT_CONTROLLER.json')))
 s.write_once(root/'TRANSPORT_INPUTS.json',dict(parent_release=PARENT,handoff=e.pin(hand/'RECEIPT.json'),
  quiescent=e.pin(hand/'QUIESCENT_CONTROLLER.json'),authorizations=parent['concurrency_amendment']['authorizations']))
 from prepare_fixed48 import prepare
 prepare(root)
 from run_fixed48_native import Owner
 new=Owner(root/'owner/RELEASE.json');new.predecessor_closed();new.verify_release()
 assert new.binding['plan']==checkpoint['plan'] and new.config['fixed48_policy']==config['fixed48_policy']
 assert len(new.jobs)==256 and len(new.release['concurrency_amendment']['prior_results'])==231
 commands=[new.cadence_command(new.out/rid) for rid in remaining]
 assert all(c[-2:]==['--port-endpoint-policy','shared_port_edges_20260912_v1'] for c in commands)
 s.write_once(root/'RUNTIME_PREFLIGHT.json',dict(status='PASS',utc=e.now(),release=new.release_pin,original_plan=new.binding['plan'],
  parent=PARENT,closed_preserved=231,unstarted_remaining=25,commands=commands,private_python=str(Path(sys.executable).resolve()),
  tests=e.pin(root/'TEST_RECEIPT.json'),new_supervisors=0,native_calls=0,new_budget=False,scientific_policy_unchanged=True,
  old_result_verification='SHA_BYTES_ONLY_NO_PHYSICAL_QA_OR_FORMAL_PREFIX_READ',old_endpoint_policy='shared_port_edges_20260912_v1'))
print(json.dumps(dict(status='DEAD_PARENT_SAME_BATCH_RECOVERY_PREPARED_NOT_LAUNCHED',root=str(root),release=new.release_pin,
 preflight=e.pin(root/'RUNTIME_PREFLIGHT.json'),tests=e.pin(root/'TEST_RECEIPT.json'),handoff=e.pin(hand/'RECEIPT.json'),
 sources=new.release['sources'],remaining_ids=sorted(remaining))))
'''
(out/'SOURCE.py').write_text(source)
run=subprocess.run([*SSH,shlex.join([PYTHON,'-B','-'])],input=source.encode(),capture_output=True,timeout=180)
(out/'STDOUT.txt').write_bytes(run.stdout);(out/'STDERR.txt').write_bytes(run.stderr)
(out/'RECEIPT.json').write_text(json.dumps(dict(root=root,returncode=run.returncode,stdout=pin(out/'STDOUT.txt'),stderr=pin(out/'STDERR.txt')),indent=2))
print('OUT='+str(out));print(run.stdout.decode());print(run.stderr.decode());run.check_returncode()
