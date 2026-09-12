"""Versioned metadata-only repair using the existing publication reload entry."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parent.parent/'samebatch_hotpath_v1'))
from stage_recovery import SSH,PYTHON,RN,pin

stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
out=D/('deploy_'+stamp);out.mkdir()
root=RN+'/metadata_endpoint_v2_anchor_'+stamp
files={n:dict(pin=pin(D/n),base64=base64.b64encode((D/n).read_bytes()).decode()) for n in ('family_entry.py','test_anchor.py')}
olddep=json.loads((D/'OLD_DEPLOYMENT.json').read_bytes())
oldpub=pin(D/'OLD_PUBLICATION.json')
source='ROOT='+repr(root)+'\nRN='+repr(RN)+'\nFILES='+repr(files)+'\nOLDDEP='+repr(olddep)+'\nOLDPUBSHA='+repr(oldpub['sha256'])+'\n'+r'''
from contextlib import ExitStack
from datetime import datetime,timezone
import base64,fcntl,hashlib,json,os,subprocess,sys,time
from pathlib import Path
chain=Path(OLDDEP['release']['path']).parents[3]
sys.path.insert(0,str(chain/'runtime'))
import controlled_execution as e,native_birth
root=Path(ROOT);root.mkdir()
def save(path,value):
 with path.open('x') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
def exact_copy(source,dest):
 b=source.read_bytes()
 with dest.open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 return e.pin(dest)
def alive(identity):
 try:p=native_birth.proc_info(identity['pid'])
 except (FileNotFoundError,ProcessLookupError):return False
 return p['state']!='Z' and p['start_ticks']==identity['start_ticks']
for name,item in FILES.items():
 b=base64.b64decode(item['base64']);assert hashlib.sha256(b).hexdigest()==item['pin']['sha256'] and len(b)==item['pin']['bytes']
 with (root/name).open('xb') as f:f.write(b)
rp=OLDDEP['release'];release=e.document(rp);cfg=e.document(release['config']);owner=Path(rp['path']).parent
assert rp['sha256']=='d0670399062e9c10b50d71e532a692c80b75f9fee35f20790d43b7624fc8c6e6'
assert Path(sys.executable).resolve()==Path(cfg['python']).resolve()
oldwork=chain/'publication_workers'/rp['sha256'];oldstart=e.document(e.pin(oldwork/'START.json'))['process']
assert not alive(oldstart),'OLD_METADATA_WORKER_STILL_ALIVE'
oldterminal=e.document(e.pin(oldwork/'worker/TERMINAL.json'));assert oldterminal['status']=='METADATA_SHARED_FAULT'
assert e.document(e.pin(oldwork/'DEPLOYMENT.json'))==OLDDEP
old_log=(oldwork/'METADATA.log').read_bytes();assert b'untrusted successor source: batch_registration.py' in old_log
archive={name:exact_copy(oldwork/name,root/('OLD_'+name)) for name in ('DEPLOYMENT.json','START.json','METADATA.log')}
archive['worker_terminal']=exact_copy(oldwork/'worker/TERMINAL.json',root/'OLD_WORKER_TERMINAL.json')
pubpath=chain/'PUBLICATION.json';pubpin=e.pin(pubpath);assert pubpin['sha256']==OLDPUBSHA
pub=e.document(pubpin);archive['publication']=exact_copy(pubpath,root/'OLD_PUBLICATION.json')
assert all(Path(p['path'])!=pubpath for p in e.document(e.pin(chain/'DEPLOYMENT.json'))['runtime_sources'])
oldentry=pub['entry'];assert oldentry['sha256']=='7614220e5fa6ad4b7f03251037e23607926452ead9bd3ecb0bd7b12a308d0b9d'
assert e.pin(oldentry['path'])==oldentry
archive['family_entry']=exact_copy(Path(oldentry['path']),root/'OLD_family_entry.py')
reg=next(p for p in release['sources'] if p['path']==str(Path(cfg['code_root'])/'batch_registration.py'))
assert reg['sha256']=='27f3f0822db9654f692764efa4616a018db176a5b953a32859644d90ac8faa4a' and e.pin(reg['path'])==reg
assert release['successor_registration']['endpoint_upgrade']['sha256']=='5ab6cf17dca566e4bd0a0bca905b76d09a1d9e2d1fe5d2acebfcab91aecba638'
assert cfg['fixed48_policy']['tool_executor_capacity']==dict(cadence=8,calibre=8,emx=48)
test=subprocess.run([cfg['python'],'-B',str(root/'test_anchor.py')],cwd=root,capture_output=True,text=True,timeout=30)
save(root/'TEST_RECEIPT.json',dict(utc=e.now(),source=e.pin(root/'test_anchor.py'),entry=e.pin(root/'family_entry.py'),
 evidence_class='FIVE_SYNTHETIC_ANCHOR_GUARD_TESTS_NOT_PHYSICAL_QA',returncode=test.returncode,stdout=test.stdout,stderr=test.stderr))
assert test.returncode==0,'ANCHOR_TEST_FAILED'
newentry=e.pin(root/'family_entry.py');sources=[newentry if p==oldentry else p for p in OLDDEP['sources']]
command=list(OLDDEP['command']);assert command[2]==oldentry['path'];command[2]=newentry['path']
state=Path(command[command.index('--state')+1]);state.mkdir(exist_ok=True)
for p in sources:assert e.pin(p['path'])==p
save(root/'DEPLOYMENT.json',dict(release=rp,command=command,sources=sources))
save(root/'REPAIR_SCOPE.json',dict(utc=e.now(),status='METADATA_ONLY_STAGED',
 authorization='Project-owner standing authorization and research-thread delegated metadata recovery 2026-09-12',
 changes='Exact frozen endpoint-v2 registration anchor added; actual registration/QA/dedup validators unchanged',
 release=rp,registry=reg,entry=newentry,old_entry=oldentry,archives=archive,
 old_worker_proven_dead=oldstart,native_actions=0,signals=0,shared_state_unchanged=str(state)))
with (state/'METADATA_WORKER.lock').open('a+') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 attempt=subprocess.run(command,capture_output=True,timeout=360)
 (root/'FIRST_PROCESS_STDOUT.jsonl').write_bytes(attempt.stdout);(root/'FIRST_PROCESS_STDERR.txt').write_bytes(attempt.stderr)
 save(root/'FIRST_PROCESS_RECEIPT.json',dict(utc=e.now(),returncode=attempt.returncode,
  stdout=e.pin(root/'FIRST_PROCESS_STDOUT.jsonl'),stderr=e.pin(root/'FIRST_PROCESS_STDERR.txt'),native_actions=0))
 if attempt.returncode!=0:
  print(json.dumps(dict(root=str(root),status='ACTUAL_METADATA_PROCESS_FAILED_NO_RELOAD',stdout=attempt.stdout.decode(),stderr=attempt.stderr.decode())))
  sys.exit(attempt.returncode)
# This is the existing per-batch reload specification, not an immutable runtime source.
# Its exact prior bytes remain in the archive and no old deployment/receipt is edited.
pub['entry']=newentry;pub['sources']=[newentry if p==oldentry else p for p in pub['sources']]
save(root/'NEW_PUBLICATION.json',pub)
assert e.pin(pubpath)==pubpin,'PUBLICATION_CHANGED_BY_ANOTHER_WRITER'
tmp=chain/('PUBLICATION_METADATA_REPAIR_'+root.name+'.tmp')
with tmp.open('xb') as f:f.write((root/'NEW_PUBLICATION.json').read_bytes());f.flush();os.fsync(f.fileno())
os.replace(tmp,pubpath)
dirfd=os.open(chain,os.O_RDONLY);os.fsync(dirfd);os.close(dirfd)
save(root/'RELOAD_RECEIPT.json',dict(utc=e.now(),old_pin=pubpin,old_bytes_preserved=archive['publication'],
 new_pin=e.pin(pubpath),new_immutable_copy=e.pin(root/'NEW_PUBLICATION.json'),
 supported_entry='continue_batches.attach_publication reads PUBLICATION.json at each natural batch boundary',
 native_runtime_modified=False,native_owner_signals=0,standby_signals=0))
assert not alive(oldstart)
with (root/'METADATA.log').open('xb') as log:
 worker=subprocess.Popen([cfg['python'],'-B',pub['worker']['path'],str(root/'DEPLOYMENT.json')],
  stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
identity=native_birth.proc_info(worker.pid)
save(root/'START.json',dict(utc=e.now(),process=identity,deployment=e.pin(root/'DEPLOYMENT.json'),native_actions=0))
time.sleep(1)
assert worker.poll() is None,'NEW_METADATA_WORKER_EARLY_EXIT'
save(root/'INSTALL_RECEIPT.json',dict(utc=e.now(),status='ACTUAL_METADATA_PROCESS_PASS_AND_WORKER_STARTED',
 release=rp,worker=identity,first_process=e.pin(root/'FIRST_PROCESS_RECEIPT.json'),reload=e.pin(root/'RELOAD_RECEIPT.json'),
 test=e.pin(root/'TEST_RECEIPT.json'),scope=e.pin(root/'REPAIR_SCOPE.json'),sources=sources,
 native_owner_before_after_unmodified=True,process_signals=0,native_launches=0))
print(json.dumps(dict(root=str(root),install=e.pin(root/'INSTALL_RECEIPT.json'),first_process_stdout=attempt.stdout.decode(),worker=identity)))
'''
(out/'SOURCE.py').write_text(source)
r=subprocess.run([*SSH,shlex.join([PYTHON,'-B','-'])],input=source.encode(),capture_output=True,timeout=430)
(out/'STDOUT.json').write_bytes(r.stdout);(out/'STDERR.txt').write_bytes(r.stderr)
print('OUT='+str(out),flush=True);print(r.stdout.decode(),flush=True);print(r.stderr.decode(),flush=True)
r.check_returncode()
