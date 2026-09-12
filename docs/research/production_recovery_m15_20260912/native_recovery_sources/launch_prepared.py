"""Single-use launch through the existing owner and inherited lease interface."""
from datetime import datetime,timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parent/'samebatch_hotpath_v1'))
from stage_recovery import SSH,PYTHON,pin
prepared=Path(sys.argv[1]);meta=json.loads((prepared/'RECEIPT.json').read_bytes());assert meta['returncode']==0
ready=json.loads((prepared/'STDOUT.txt').read_text().splitlines()[-1])
out=D/('launch_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));out.mkdir()
source='READY='+repr(ready)+'\n'+r'''
from contextlib import ExitStack
import json,os,subprocess,sys,time
from pathlib import Path
root=Path(READY['root']);sys.path.insert(0,str(root/'runtime'))
import controlled_execution as e,controlled_metadata as m,start_slots as s,native_birth
assert e.document(READY['preflight'])['status']=='PASS' and e.document(READY['tests'])['status']=='PASS'
release=e.document(READY['release']);config=e.document(release['config']);sys.path.insert(0,config['repo'])
from run_fixed48_native import Owner
new=Owner(READY['release']['path']);new.predecessor_closed();new.verify_release()
assert not (new.out/'START_RECEIPT.json').exists() and not (new.out/'LAUNCH_INTENT.json').exists()
with ExitStack() as stack:
 fds=[stack.enter_context(new.lease(p)) for p in (new.out/'OWNER.lock',config['production_lock_path'],config['global_lock_path'])]
 new.predecessor_closed();new.verify_release()
 command=[config['python'],'-B',str(root/'runtime/run_fixed48_native.py'),'--release',new.release_pin['path']]
 env=new.env.copy();env.update(dict(zip(('EUCAP15_OWNER_FD','EUCAP15_PRODUCTION_FD','EUCAP15_RESEARCH_FD'),map(str,fds))))
 s.write_once(new.out/'LAUNCH_INTENT.json',dict(utc=e.now(),command=command,release=new.release_pin,handoff=READY['handoff']))
 with (new.out/'OWNER.stdout.log').open('xb') as log:
  child=subprocess.Popen(command,cwd=config['repo'],env=env,stdin=subprocess.DEVNULL,stdout=log,
   stderr=subprocess.STDOUT,start_new_session=True,pass_fds=fds)
 live=native_birth.proc_info(child.pid)
 s.write_once(new.out/'START_RECEIPT.json',dict(status='ONE_SAME_BATCH_FIXED48_OWNER_STARTED_NOT_NATIVE_PROOF',utc=e.now(),
  pid=child.pid,start_ticks=live['start_ticks'],uid=live['uid'],command=command,release=new.release_pin,
  parent_release=release['parent_release'],original_plan=new.binding['plan'],old_budget_reset=False,
  requested_emx=48,executor_capacity=48,locks_transferred_without_reacquire_gap=True))
time.sleep(3)
assert child.poll() is None,(new.out/'OWNER.stdout.log').read_text()
receipt=dict(status='SAME_BATCH_RECOVERED_WAITING_FOR_FRESH_ADMISSION',utc=e.now(),process=native_birth.proc_info(child.pid),
 release=new.release_pin,start=e.pin(new.out/'START_RECEIPT.json'),handoff=READY['handoff'],preflight=READY['preflight'],tests=READY['tests'],
 original_plan=new.binding['plan'],closed_preserved=231,unstarted_remaining=25,adopted_candidates=0,
 current_native_jobs=[],native_jobs_note='No pre-existing native jobs; sampler must collect five independent new healthy checks.',
 new_standby_not_yet_started=True,NN_started=False)
s.write_once(root/'DEPLOYMENT_RECEIPT.json',receipt)
print(json.dumps(dict(receipt=e.pin(root/'DEPLOYMENT_RECEIPT.json'),**receipt)))
'''
(out/'SOURCE.py').write_text(source)
run=subprocess.run([*SSH,shlex.join([PYTHON,'-B','-'])],input=source.encode(),capture_output=True,timeout=90)
(out/'STDOUT.txt').write_bytes(run.stdout);(out/'STDERR.txt').write_bytes(run.stderr)
(out/'RECEIPT.json').write_text(json.dumps(dict(root=meta['root'],returncode=run.returncode,stdout=pin(out/'STDOUT.txt'),stderr=pin(out/'STDERR.txt')),indent=2))
print('OUT='+str(out));print(run.stdout.decode());print(run.stderr.decode());run.check_returncode()
