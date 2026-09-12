"""Repair metadata source closure; drain only its exact old metadata worker."""
import base64
from datetime import datetime,timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parent.parent/'samebatch_hotpath_v1'))
from stage_recovery import SSH,PYTHON,RN,pin
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
out=D/('closure_deploy_'+stamp);out.mkdir()
root=RN+'/metadata_endpoint_v2_closure_'+stamp
install=json.loads((D/'INSTALL_RECEIPT.json').read_bytes())
oldroot=str(Path(install['first_process']['path']).parent)
files={n:dict(pin=pin(D/n),data=base64.b64encode((D/n).read_bytes()).decode())
       for n in ('family_entry.py','consume_endpoint_v2.py','test_endpoint_closure.py')}
source='ROOT='+repr(root)+'\nOLDROOT='+repr(oldroot)+'\nINSTALLSHA='+repr(pin(D/'INSTALL_RECEIPT.json')['sha256'])+'\nFILES='+repr(files)+'\n'+r'''
from collections import Counter
from datetime import datetime,timezone
import base64,fcntl,hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
oldroot=Path(OLDROOT);installed=json.loads((oldroot/'INSTALL_RECEIPT.json').read_bytes())
assert hashlib.sha256((oldroot/'INSTALL_RECEIPT.json').read_bytes()).hexdigest()==INSTALLSHA
old=eold=json.loads((oldroot/'DEPLOYMENT.json').read_bytes());rp=old['release'];chain=Path(rp['path']).parents[3]
sys.path.insert(0,str(chain/'runtime'))
import controlled_execution as e,native_birth
root=Path(ROOT);root.mkdir()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.flush();os.fsync(f.fileno())
def copy(src,dst):
 with dst.open('xb') as f:f.write(src.read_bytes());f.flush();os.fsync(f.fileno())
 return e.pin(dst)
def live(i):
 try:p=native_birth.proc_info(i['pid'])
 except (FileNotFoundError,ProcessLookupError):return None
 return p if p['state']!='Z' and p['start_ticks']==i['start_ticks'] else None
release=e.document(rp);cfg=e.document(release['config']);owner=Path(rp['path']).parent
native_start=e.document(e.pin(owner/'START_RECEIPT.json'))
continuation_start=e.document(e.pin(chain/'CONTINUATION_START.json'))['process']
assert live(native_start) and live(continuation_start),'NATIVE_BOUNDARY_CHANGED_RECHECK_REQUIRED'
assert Path(sys.executable).resolve()==Path(cfg['python']).resolve()
pubpath=chain/'PUBLICATION.json';pubpin=e.pin(pubpath);pub=e.document(pubpin)
assert pub['entry']==installed['sources'][-2] or pub['entry'] in installed['sources']
assert pub['entry']['path']==old['command'][2]
archives=dict(old_publication=copy(pubpath,root/'OLD_PUBLICATION.json'),old_deployment=copy(oldroot/'DEPLOYMENT.json',root/'OLD_DEPLOYMENT.json'))
for name,item in FILES.items():
 b=base64.b64decode(item['data']);assert hashlib.sha256(b).hexdigest()==item['pin']['sha256'] and len(b)==item['pin']['bytes']
 with (root/name).open('xb') as f:f.write(b)
test=subprocess.run([cfg['python'],'-B',str(root/'test_endpoint_closure.py')],cwd=root,capture_output=True,text=True,timeout=30)
save(root/'TEST_RECEIPT.json',dict(utc=e.now(),returncode=test.returncode,stdout=test.stdout,stderr=test.stderr,
 evidence_class='SIX_SYNTHETIC_ENDPOINT_CLOSURE_GUARDS_NOT_PHYSICAL_QA',source=e.pin(root/'test_endpoint_closure.py')))
assert test.returncode==0
newentry=e.pin(root/'family_entry.py');receiver=e.pin(root/'consume_endpoint_v2.py');oldentry=pub['entry']
command=list(old['command']);command[2]=newentry['path'];command+=['--endpoint-receiver',receiver['path']]
sources=[newentry if p==oldentry else p for p in old['sources']]+[receiver]
for p in sources:assert e.pin(p['path'])==p
save(root/'DEPLOYMENT.json',dict(command=command,sources=sources,release=rp))
identity=installed['worker'];oldlive=live(identity)
assert oldlive and oldlive['pid'] not in (native_start['pid'],continuation_start['pid'])
assert oldlive['argv'][2]==pub['worker']['path'] and oldlive['argv'][3]==str(oldroot/'DEPLOYMENT.json')
state=Path(old['command'][old['command'].index('--state')+1])
save(root/'DRAIN_INTENT.json',dict(utc=e.now(),metadata_process=oldlive,native_owner=native_start,
 standby=continuation_start,method='Stop metadata polling parent only; let any current metadata child finish; terminate parent; preserve all state',native_process_signals=0))
os.kill(identity['pid'],signal.SIGSTOP)
try:
 deadline=time.monotonic()+370
 while True:
  tids=Path('/proc')/str(identity['pid'])/'task'
  child_ids=set()
  for task in tids.iterdir():
   try:child_ids.update(int(x) for x in (task/'children').read_text().split())
   except FileNotFoundError:pass
  running=[]
  for pid in child_ids:
   try:p=native_birth.proc_info(pid)
   except FileNotFoundError:continue
   if p['state']=='Z':continue
   assert p['argv'][:3]==old['command'][:3],'UNEXPECTED_METADATA_CHILD_DO_NOT_SIGNAL'
   running.append(p)
  if not running:break
  assert time.monotonic()<deadline,'METADATA_CHILD_DRAIN_TIMEOUT'
  time.sleep(1)
 os.kill(identity['pid'],signal.SIGTERM);os.kill(identity['pid'],signal.SIGCONT)
except BaseException:
 if live(identity):os.kill(identity['pid'],signal.SIGCONT)
 raise
for _ in range(50):
 if not live(identity):break
 time.sleep(.1)
assert not live(identity),'OLD_METADATA_PARENT_NOT_DEAD'
save(root/'DRAIN_RECEIPT.json',dict(utc=e.now(),metadata_process=identity,old_metadata_dead=True,
 current_metadata_child_completed_naturally=True,native_owner=live(native_start),standby=live(continuation_start),native_signals=0))
copy(oldroot/'METADATA.log',root/'OLD_METADATA.log')
with (state/'METADATA_WORKER.lock').open('a+') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 attempt=subprocess.run(command,capture_output=True,timeout=360)
 (root/'FIRST_PROCESS_STDOUT.jsonl').write_bytes(attempt.stdout);(root/'FIRST_PROCESS_STDERR.txt').write_bytes(attempt.stderr)
 save(root/'FIRST_PROCESS_RECEIPT.json',dict(utc=e.now(),returncode=attempt.returncode,
  stdout=e.pin(root/'FIRST_PROCESS_STDOUT.jsonl'),stderr=e.pin(root/'FIRST_PROCESS_STDERR.txt'),native_actions=0))
 if attempt.returncode:
  print(json.dumps(dict(root=str(root),status='METADATA_PROCESS_FAILED',stdout=attempt.stdout.decode(),stderr=attempt.stderr.decode())))
  sys.exit(attempt.returncode)
 answer=json.loads(attempt.stdout);actual=e.document(answer['receipt'])
 unexpected=[v for v in actual['outcomes'] if v['status']=='CANDIDATE_EVIDENCE_HOLD_NO_QUALIFICATION']
 save(root/'ACTUAL_PROCESS_CHECK.json',dict(utc=e.now(),receipt=answer['receipt'],formally_added=actual['formally_added'],
  outcome_counts=dict(Counter(v['status'] for v in actual['outcomes'])),holds=unexpected))
 if unexpected:
  print(json.dumps(dict(root=str(root),status='METADATA_EVIDENCE_HOLDS_NOT_RESOLVED',holds=unexpected)))
  sys.exit(2)
assert live(native_start) and live(continuation_start),'NATIVE_BOUNDARY_CHANGED_BEFORE_RELOAD'
assert e.pin(pubpath)==pubpin
pub['entry']=newentry;pub['sources']=[newentry if p==oldentry else p for p in pub['sources']]+[receiver]
pub['extra_arguments']+=['--endpoint-receiver',receiver['path']]
save(root/'NEW_PUBLICATION.json',pub)
tmp=chain/('PUBLICATION_CLOSURE_REPAIR_'+root.name+'.tmp')
with tmp.open('xb') as f:f.write((root/'NEW_PUBLICATION.json').read_bytes());f.flush();os.fsync(f.fileno())
os.replace(tmp,pubpath);fd=os.open(chain,os.O_RDONLY);os.fsync(fd);os.close(fd)
save(root/'RELOAD_RECEIPT.json',dict(utc=e.now(),old_pin=pubpin,archive=archives['old_publication'],new_pin=e.pin(pubpath),
 native_runtime_modified=False,standby_signals=0,native_signals=0))
with (root/'METADATA.log').open('xb') as log:
 worker=subprocess.Popen([cfg['python'],'-B',pub['worker']['path'],str(root/'DEPLOYMENT.json')],
  stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
identity2=native_birth.proc_info(worker.pid)
save(root/'START.json',dict(utc=e.now(),process=identity2,deployment=e.pin(root/'DEPLOYMENT.json')))
time.sleep(1);assert worker.poll() is None
save(root/'INSTALL_RECEIPT.json',dict(utc=e.now(),status='ACTUAL_SOURCE_CLOSURE_PROCESSED_AND_WORKER_RESTORED',
 release=rp,worker=identity2,actual=answer,check=e.pin(root/'ACTUAL_PROCESS_CHECK.json'),source=e.pin(root/'family_entry.py'),
 receiver=receiver,test=e.pin(root/'TEST_RECEIPT.json'),drain=e.pin(root/'DRAIN_RECEIPT.json'),reload=e.pin(root/'RELOAD_RECEIPT.json'),
 native_owner=live(native_start),standby=live(continuation_start),native_signals=0,old_state_preserved=True,
 actual_new_state=str(state)+'_endpointv2closure_v1',sources=sources))
print(json.dumps(dict(root=str(root),install=e.pin(root/'INSTALL_RECEIPT.json'),actual=answer,worker=identity2)))
'''
(out/'SOURCE.py').write_text(source)
r=subprocess.run([*SSH,shlex.join([PYTHON,'-B','-'])],input=source.encode(),capture_output=True,timeout=780)
(out/'STDOUT.json').write_bytes(r.stdout);(out/'STDERR.txt').write_bytes(r.stderr)
print('OUT='+str(out),flush=True);print(r.stdout.decode(),flush=True);print(r.stderr.decode(),flush=True)
r.check_returncode()
