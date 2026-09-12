"""Restore the existing endpoint-v2 chain after same-batch dead-parent recovery."""
import base64
from datetime import datetime,timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parent/'samebatch_hotpath_v1'))
from stage_recovery import SSH,PYTHON,RN,pin
launched=Path(sys.argv[1]);ready=json.loads((launched/'STDOUT.txt').read_bytes())
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=D/('continuation_'+stamp);out.mkdir()
old=RN+'/continuous_endpoint_v2_20260912T090023519751Z'
chain=RN+'/continuous_endpoint_v2_claimwait_'+stamp
sourcefile=D/'continuation_runtime/continue_batches.py'
source='READY='+repr(ready)+'\nOLD='+repr(old)+'\nCHAIN='+repr(chain)+'\nNEW_CONTINUE='+repr(dict(pin=pin(sourcefile),raw=base64.b64encode(sourcefile.read_bytes()).decode()))+'\n'+r'''
import base64,copy,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
old=Path(OLD);chain=Path(CHAIN);root=Path(READY['release']['path']).parent.parent
assert not chain.exists();chain.mkdir(mode=0o700)
prior=json.loads((old/'DEPLOYMENT.json').read_bytes())
for p in prior['runtime_sources']:
 raw=Path(p['path']).read_bytes();assert len(raw)==p['bytes'] and hashlib.sha256(raw).hexdigest()==p['sha256']
 target=chain/'runtime'/Path(p['path']).relative_to(old/'runtime');target.parent.mkdir(parents=True,exist_ok=True)
 if target.name=='continue_batches.py':
  raw=base64.b64decode(NEW_CONTINUE['raw']);assert hashlib.sha256(raw).hexdigest()==NEW_CONTINUE['pin']['sha256']
 elif target.name=='fixed48_runtime.py':raw=(root/'runtime/fixed48_runtime.py').read_bytes()
 with target.open('xb') as f:f.write(raw)
shutil.copytree(old/'factory',chain/'factory');shutil.copytree(old/'batch_registry',chain/'batch_registry')
shutil.copyfile(old/'PUBLICATION.json',chain/'PUBLICATION.json')
sys.path.insert(0,str(chain/'runtime'))
import controlled_execution as e,controlled_metadata as m,start_slots as s,native_birth
import continue_batches as cont
from batch_registration import alive
assert e.document(READY['receipt'])['release']==READY['release']
current=e.document(READY['release']);cfg=e.document(current['config'])
start=e.document(READY['start']);assert alive(start),'RECOVERY_OWNER_NOT_ALIVE'
install=e.document(e.pin(old/'INSTALL_RECEIPT.json'))
assert not alive(install['standby']) and not alive(install['metadata']),'PREVIOUS_METADATA_OR_STANDBY_STILL_LIVE'
failure=e.pin(old/'CONTINUATION_FAILURE.json');assert e.document(failure)['status']=='CONTINUATION_STOPPED_PRESERVE_NATIVE_CHILDREN'
resume=dict(schema='eucap15_samebatch_continuation_resume.v1',utc=e.now(),previous_release=current['parent_release'],
 resumed_release=READY['release'],original_plan=READY['original_plan'],handoff=READY['handoff'],
 previous_resume=prior['same_batch_resume'],old_continuation_install=e.pin(old/'INSTALL_RECEIPT.json'),
 old_continuation_stop=failure,same_original_requests=True,new_budget=False)
s.write_once(chain/'SAME_BATCH_RESUME.json',resume)
d=copy.deepcopy(prior);d.update(utc=e.now(),same_batch_resume=e.pin(chain/'SAME_BATCH_RESUME.json'),
 runtime_sources=[e.pin(p) for p in sorted((chain/'runtime').rglob('*.py'))],
 factory_sources=[e.pin(p) for p in sorted((chain/'factory').glob('*.py'))],
 first_registration=e.pin(chain/'batch_registry'/Path(prior['first_registration']['path']).name),
 replaces_continuation=dict(deployment=e.pin(old/'DEPLOYMENT.json'),install=e.pin(old/'INSTALL_RECEIPT.json'),failure=failure))
assert d['first_registration']['sha256']==prior['first_registration']['sha256']
assert d['endpoint_upgrade']==prior['endpoint_upgrade']
s.write_once(chain/'DEPLOYMENT.json',d)
assert cont.resumed_parent(cont.verify_deployment(chain))==READY['release']
assert cont.resumed_parent(prior)==current['parent_release']
checks=['ACTUAL_TWO_HOP_SAME_BATCH_CHAIN_PASS','EXISTING_ONE_HOP_CHAIN_PASS']
original_document=e.document
for label,mutate in [('MISSING_PARENT_REF_REJECTED',lambda v:v.pop('previous_resume')),
 ('CYCLE_REJECTED',lambda v:v.update(previous_resume=d['same_batch_resume'])),
 ('WRONG_IMMEDIATE_PARENT_REJECTED',lambda v:v.update(previous_release=d['initial_release']))]:
 value=copy.deepcopy(resume);mutate(value)
 def test_document(p):return value if p==d['same_batch_resume'] else original_document(p)
 try:
  with patch.object(e,'document',side_effect=test_document):cont.resumed_parent(d)
 except (ValueError,KeyError):checks.append(label)
 else:raise AssertionError('NEGATIVE_CHAIN_NOT_REJECTED:'+label)
assert e.pin(chain/'runtime/fixed48_runtime.py')['sha256']==e.pin(root/'runtime/fixed48_runtime.py')['sha256']
s.write_once(chain/'PREFLIGHT.json',dict(status='PASS',utc=e.now(),checks=checks,source=e.pin(chain/'runtime/continue_batches.py'),
 deployment=e.pin(chain/'DEPLOYMENT.json'),parent_failure=failure,actual_private_python=str(Path(sys.executable).resolve()),
 synthetic_negative_mutations=True,native_calls=0,old_receipts_modified=False,old_budget_reset=False))
pub=e.document(e.pin(chain/'PUBLICATION.json'))
for p in pub['sources']:m.read_pin(p)
state=Path(d['formal_ledger']).parent.parent/(pub['state_prefix']+READY['release']['sha256'][:12])
command=[cfg['python'],'-B',pub['entry']['path'],'--fixed48-entry',pub['fixed48_entry']['path'],
 '--legacy-bridge',pub['legacy_bridge']['path'],'--library-dir',pub['library_dir'],'--release',READY['release']['path'],
 '--contract-inputs',pub['contract_inputs']['path'],'--state',str(state),*pub.get('extra_arguments',[])]
first=subprocess.run(command,capture_output=True,text=True,timeout=90)
s.write_once(chain/'FIRST_METADATA_CALL.json',dict(utc=e.now(),command=command,returncode=first.returncode,
 stdout=first.stdout,stderr=first.stderr,native_calls=0))
metadata=None
if first.returncode in (0,75):
 worker=chain/'current_publication';worker.mkdir()
 s.write_once(worker/'DEPLOYMENT.json',dict(release=READY['release'],command=command,sources=pub['sources']))
 with (worker/'METADATA.log').open('xb') as log:
  md=subprocess.Popen([cfg['python'],'-B',pub['worker']['path'],str(worker/'DEPLOYMENT.json')],
   stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 metadata=native_birth.proc_info(md.pid)
 s.write_once(worker/'START.json',dict(utc=e.now(),process=metadata,release=READY['release'],command=command))
with (chain/'CONTINUATION.log').open('xb') as log:
 child=subprocess.Popen([d['python'],'-B',str(chain/'runtime/continue_batches.py'),str(chain)],
  stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
standby=native_birth.proc_info(child.pid)
s.write_once(chain/'CONTINUATION_LAUNCH.json',dict(utc=e.now(),process=standby,deployment=e.pin(chain/'DEPLOYMENT.json')))
time.sleep(3);assert child.poll() is None,(chain/'CONTINUATION.log').read_text()
event=[json.loads(l) for l in (chain/'CONTINUATION_EVENTS.jsonl').read_text().splitlines()][-1]
assert event['status']=='WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT' and event['release']==READY['release']
assert alive(start)
receipt=dict(status='RECOVERED_SAME_BATCH_AND_ENDPOINT_V2_STANDBY_INSTALLED',utc=e.now(),
 current_owner=native_birth.proc_info(start['pid']),current_release=READY['release'],standby=native_birth.proc_info(child.pid),
 metadata=metadata,first_metadata=e.pin(chain/'FIRST_METADATA_CALL.json'),metadata_returncode=first.returncode,
 resume=e.pin(chain/'SAME_BATCH_RESUME.json'),deployment=e.pin(chain/'DEPLOYMENT.json'),preflight=e.pin(chain/'PREFLIGHT.json'),
 publication=e.pin(chain/'PUBLICATION.json'),first_successor_manifest=e.document(d['first_registration'])['manifest'],
 endpoint_upgrade=d['endpoint_upgrade'],old_batch_budget_reset=False,old_endpoint_policy_unchanged=True,
 new_native_processes_started_by_this_install=0)
s.write_once(chain/'INSTALL_RECEIPT.json',receipt)
print(json.dumps(dict(receipt=e.pin(chain/'INSTALL_RECEIPT.json'),**receipt)))
'''
(out/'SOURCE.py').write_text(source)
run=subprocess.run([*SSH,shlex.join([PYTHON,'-B','-'])],input=source.encode(),capture_output=True,timeout=180)
(out/'STDOUT.txt').write_bytes(run.stdout);(out/'STDERR.txt').write_bytes(run.stderr)
(out/'RECEIPT.json').write_text(json.dumps(dict(root=str(Path(ready['release']['path']).parent.parent),chain=chain,returncode=run.returncode,
 stdout=pin(out/'STDOUT.txt'),stderr=pin(out/'STDERR.txt')),indent=2))
print('OUT='+str(out));print(run.stdout.decode());print(run.stderr.decode());run.check_returncode()
