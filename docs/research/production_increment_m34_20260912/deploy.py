"""Stage/test once, then controlled close and continue the existing installer."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

D = Path(__file__).resolve().parent
sys.path.insert(0, str(D.parent))
from stage_remote import SSH, PY, RN, pin

mode = sys.argv[1]
out = D/(mode+'_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
out.mkdir()
root = json.loads(Path(sys.argv[2]).read_bytes())['root'] if mode == 'stage' else None
if mode == 'stage':
    operation = root+'/'+out.name
    payload = {n:dict(pin=pin(D/n), raw=base64.b64encode((D/n).read_bytes()).decode()) for n in
        ('waiter_reservation.py', 'installer_continuation.py', 'test_waiter_reservation.py')}
    source = 'ROOT='+repr(root)+'\nOP='+repr(operation)+'\nPAYLOAD='+repr(payload)+'\n'+r'''
import ast,base64,hashlib,json,subprocess,sys
from pathlib import Path
root=Path(ROOT);op=Path(OP);op.mkdir(mode=0o700)
sys.path.insert(0,str(root/'runtime'))
import controlled_execution as e,controlled_metadata as m,start_slots as s
staged=e.document(e.pin(root/'STAGED.json'))
for p in staged['runtime_sources']:m.read_pin(p)
for name,value in PAYLOAD.items():
 raw=base64.b64decode(value['raw']);assert hashlib.sha256(raw).hexdigest()==value['pin']['sha256'] and len(raw)==value['pin']['bytes']
 ast.parse(raw)
 with (op/name).open('xb') as f:f.write(raw)
command=[sys.executable,'-B',str(op/'test_waiter_reservation.py'),str(root/'runtime')]
result=subprocess.run(command,capture_output=True,text=True,timeout=30)
s.write_once(op/'INTEGRATION_TEST.json',dict(utc=e.now(),status='PASS' if result.returncode==0 else 'FAIL',
 command=command,returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,
 actual_private_python=str(Path(sys.executable).resolve()),unique_integration_tests=2,
 synthetic_parent_and_child_only=True,production_signals=0,native_actions=0,old_test_suites_repeated=0))
assert result.returncode==0,'NEW_HANDOFF_INTEGRATION_FAILED'
sources=[e.pin(op/name) for name in PAYLOAD]
spec=dict(utc=e.now(),status='TESTED_HANDOFF_INSTALLER_CONTINUATION_NOT_DEPLOYED',
 source_changes='Finite installer continuation only; no modification of active or staged scientific runtime',
 old_installer=e.pin(root/'install_at_boundary.py'),previous_start=e.pin(root/'BOUNDARY_INSTALLER_START.json'),
 prior_staged=e.pin(root/'STAGED.json'),sources=sources,integration_test=e.pin(op/'INTEGRATION_TEST.json'),
 runtime_sha256=e.pin(root/'runtime/fixed48_runtime.py')['sha256'],
 continuation_sha256=e.pin(root/'runtime/continue_batches.py')['sha256'],
 authority='STANDING_DELEGATED_RELEASE_AND_SAFE_HANDOFF_AUTHORIZATION',native_signals=0)
s.write_once(op/'SPECIFICATION.json',spec)
print(json.dumps(dict(root=str(root),operation=str(op),specification=e.pin(op/'SPECIFICATION.json'),
 tests=e.pin(op/'INTEGRATION_TEST.json'),test_stdout=result.stdout,test_stderr=result.stderr)))
'''
elif mode == 'launch':
    ready = json.loads(Path(sys.argv[2]).read_bytes())
    source = 'READY='+repr(ready)+'\n'+r'''
import json,os,subprocess,sys,time
from pathlib import Path
root=Path(READY['root']);op=Path(READY['operation']);sys.path.insert(0,str(root/'runtime'))
import controlled_execution as e,controlled_metadata as m,start_slots as s,native_birth
import quiescent_handoff as q,continue_batches as c
spec=e.document(READY['specification'])
assert e.document(READY['tests'])['status']=='PASS'
for p in spec['sources']:m.read_pin(p)
m.read_pin(spec['old_installer'])
staged=e.document(spec['prior_staged']);old=Path(staged['previous_chain'])
for p in staged['runtime_sources']:m.read_pin(p)
previous=e.document(spec['previous_start'])['process']
assert not (op/'PREVIOUS_INSTALLER_TERMINAL.json').exists() and not (op/'LAUNCH.json').exists(),'OPERATION_ALREADY_USED'
def precheck():
 actual=q.state(previous)
 assert actual['argv']==previous['argv'] and actual['state'] not in ('Z','T','t'),'WAITING_INSTALLER_NOT_ALIVE'
 p=Path('/proc')/str(previous['pid'])
 assert (p/'wchan').read_text()=='hrtimer_nanosleep','INSTALLER_NOT_IN_WAIT'
 fds={v.name:os.readlink(v) for v in (p/'fd').iterdir()}
 assert set(fds.values())<={'/dev/null',str(root/'INSTALLER.log'),str(old/'PERMIT_RELEASE_UPGRADE.lock')},'INSTALLER_HOLDS_UNEXPECTED_FD'
 return dict(process=actual,fds=fds)
def children():
 found=[]
 for p in Path('/proc').glob('[0-9]*'):
  try:
   v=native_birth.proc_info(int(p.name))
   if v['ppid']==previous['pid'] and v['state']!='Z':found.append(v)
  except (FileNotFoundError,ProcessLookupError,PermissionError):pass
 return dict(descendants=found,native=[])
def production():
 ev=[json.loads(x) for x in (old/'CONTINUATION_EVENTS.jsonl').read_bytes().splitlines()]
 assert ev[-1]['status']=='WAITING_FOR_CURRENT_BATCH_TERMINAL_AND_OWNER_EXIT'
 rp=ev[-1]['release'];cfg=e.document(e.document(rp)['config'])
 owner=e.document(e.pin(Path(cfg['out'])/'START_RECEIPT.json'))
 standby=e.document(e.pin(old/'CONTINUATION_START.json'))['process']
 assert c.reg.alive(owner),'CURRENT_OWNER_ALREADY_CLOSED_DO_NOT_RACE'
 assert q.state(standby)['state'] not in ('Z','T','t'),'STANDBY_NOT_ACTIVE'
 assert not any((root/n).exists() for n in ('BOUNDARY_PREFLIGHT.json','HANDOFF.json','INSTALL_RECEIPT.json','INSTALL_FAILURE.json'))
 return dict(release=rp,owner=owner,standby=standby)
before=precheck();active=production();fd=q.freeze(previous,children)
assert fd is not None,'INSTALLER_HAS_CHILDREN'
terminated=False
try:
 after=production();assert after['release']==active['release'],'PRODUCTION_BOUNDARY_MOVED'
 q.terminate_frozen(previous,fd);terminated=True
finally:
 if not terminated:q.resume(fd)
assert not c.reg.alive(previous)
s.write_once(op/'PREVIOUS_INSTALLER_TERMINAL.json',dict(status='WAITING_INSTALLER_CONTROLLED_CLOSE_NO_PRODUCTION_SIGNAL',
 utc=e.now(),previous_identity=previous,previous_start=spec['previous_start'],
 reason='Original full-boundary polling loses race to standby; one replacement installer follows same lock',
 observed_before=before,production_before=active,production_after=after,previous_process_dead=True,
 previous_evidence_overwritten=False,native_signals=0,owner_signals=0,standby_signals=0))
command=[sys.executable,'-B',str(op/'installer_continuation.py'),str(root),str(op)]
with (op/'INSTALLER.log').open('xb') as log:
 p=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
identity=native_birth.proc_info(p.pid)
s.write_once(op/'LAUNCH.json',dict(utc=e.now(),process=identity,command=command,
 previous_terminal=e.pin(op/'PREVIOUS_INSTALLER_TERMINAL.json'),specification=READY['specification'],
 independent_session=True,maximum_drain_seconds=3600))
time.sleep(2)
print(json.dumps(dict(root=str(root),operation=str(op),launch=e.pin(op/'LAUNCH.json'),
 process=native_birth.proc_info(p.pid) if p.poll() is None else None,
 reserved=e.pin(op/'BOUNDARY_RESERVED.json') if (op/'BOUNDARY_RESERVED.json').exists() else None,
 terminal=e.document(e.pin(op/'TERMINAL.json')) if (op/'TERMINAL.json').exists() else None)))
assert p.poll() is None or (root/'INSTALL_RECEIPT.json').exists(),'CONTINUATION_FAILED_SEE_NEW_TERMINAL'
'''
else:
    raise ValueError(mode)
(out/'SOURCE.py').write_text(source)
result = subprocess.run([*SSH, shlex.join([PY, '-B', '-'])], input=source.encode(), capture_output=True, timeout=65)
(out/'STDOUT.txt').write_bytes(result.stdout)
(out/'STDERR.txt').write_bytes(result.stderr)
print('OUT='+str(out))
print(result.stdout.decode())
print(result.stderr.decode())
result.check_returncode()
(out/'RECEIPT.json').write_bytes(result.stdout)
print(json.dumps(pin(out/'RECEIPT.json')))
