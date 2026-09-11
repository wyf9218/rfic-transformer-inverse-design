"""Observe the exact native EMX descendant of one audited wrapper invocation.

No signal is sent to an existing or spawned child. A missing observation stays
UNKNOWN and never releases the already reserved start slot for a retry.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from controlled_metadata import require, read_pin
from start_slots import write_once


def utc():return datetime.now(timezone.utc).isoformat()


def same_process(left,right):
    return all(left[k]==right[k] for k in ('pid','ppid','start_ticks','uid','argv'))


def write_log(path,value):
    with Path(path).open('x',encoding='utf-8') as f:
        f.write(value);f.flush();os.fsync(f.fileno())


def proc_info(pid,proc_root=Path('/proc')):
    p=proc_root/str(pid)
    fields=(p/'stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=int(pid),ppid=int(fields[1]),state=fields[0],start_ticks=int(fields[19]),
        uid=p.stat().st_uid,argv=[v.decode() for v in (p/'cmdline').read_bytes().split(b'\0') if v])


def descendant_chain(pid,ancestor,processes):
    chain=[];seen=set()
    while pid in processes and pid not in seen:
        seen.add(pid);p=processes[pid];chain.append(p)
        if pid==ancestor['pid']:
            return chain if p['start_ticks']==ancestor['start_ticks'] else None
        pid=p['ppid']
    return None


def matches_native(info,ancestor,processes,command,exe_sha256,expected_exe_sha256,uid):
    """Pure identity predicate; a wrapper PID or name match alone is insufficient."""
    if info['pid'] not in processes or not same_process(info,processes[info['pid']]):
        return False
    chain=descendant_chain(info['pid'],ancestor,processes)
    return bool(chain and info['state']!='Z' and info['uid']==uid and
        exe_sha256==expected_exe_sha256 and len(info['argv'])==len(command) and
        info['argv'][1:]==command[1:])


def observe_invocation(command,kwargs,*,expected_executable,slot,output,plan_sha256):
    require(sys.platform.startswith('linux'),'REAL_LINUX_PROC_REQUIRED_FOR_NATIVE_OBSERVER')
    require(slot['status']=='RESERVED_NOT_NATIVE_PROOF' and slot['plan_sha256']==plan_sha256,
            'EXACT_RESERVED_SLOT_REQUIRED')
    require(slot['launch_binding']['command']==command,'SLOT_COMMAND_CHANGED')
    read_pin(expected_executable)
    require(kwargs.get('capture_output') is True and kwargs.get('text') is True and
        kwargs.get('check') is False and set(kwargs)<={'cwd','capture_output','text','check','env','pass_fds'},
        'UNSUPPORTED_NATIVE_INVOCATION_SIGNATURE')
    output=Path(output)
    require(not output.exists(),'NATIVE_OBSERVATION_OUTPUT_EXISTS_NO_REPEAT')
    output.mkdir(exist_ok=False)
    write_once(output/'INVOCATION_INTENT.json',dict(schema='eucap15_controlled64_native_invocation_intent.v1',
        slot=slot,expected_executable=expected_executable,command=command,cwd=kwargs.get('cwd'),created_utc=utc(),
        native_started=None))
    popen_kwargs={k:v for k,v in kwargs.items() if k not in ('capture_output','check')}
    popen_kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        child=subprocess.Popen(command,**popen_kwargs)
    except OSError as error:
        write_once(output/'SPAWN_FAILURE.json',dict(status='WRAPPER_SPAWN_FAILED_NATIVE_NOT_STARTED',
            error=type(error).__name__+': '+str(error),utc=utc(),native_started=False))
        raise
    try:
        ancestor=proc_info(child.pid)
    except (FileNotFoundError,ProcessLookupError):
        ancestor=None
    write_once(output/'WRAPPER_STARTED.json',dict(wrapper_pid=child.pid,wrapper_proc=ancestor,utc=utc(),
        native_started=None,interpretation='Wrapper spawn is not native EMX evidence'))
    done=threading.Event();observed={};errors=[]

    def monitor():
        while not done.is_set():
            try:
                processes={}
                for p in Path('/proc').glob('[0-9]*'):
                    try:
                        if p.stat().st_uid!=os.getuid():continue
                        info=proc_info(int(p.name))
                        processes[info['pid']]=info
                    except (FileNotFoundError,ProcessLookupError,PermissionError,UnicodeError):continue
                if ancestor is not None:
                    for info in processes.values():
                        key=(info['pid'],info['start_ticks'])
                        if key in observed or info['state']=='Z':continue
                        if not descendant_chain(info['pid'],ancestor,processes):continue
                        if len(info['argv'])!=len(command) or info['argv'][1:]!=command[1:]:continue
                        exe=Path('/proc')/str(info['pid'])/'exe'
                        try:
                            raw=exe.read_bytes();sha=hashlib.sha256(raw).hexdigest()
                            again=proc_info(info['pid'])
                            if not same_process(again,info) or again['state']=='Z':continue
                            if len(raw)!=expected_executable['bytes']:continue
                            if not matches_native(info,ancestor,processes,command,sha,expected_executable['sha256'],os.getuid()):continue
                            birth=dict(schema='eucap15_controlled64_native_birth.v1',status='OBSERVED_EXACT_NATIVE_PROCESS',
                                observed_utc=utc(),process=info,ancestor=ancestor,
                                ancestry=descendant_chain(info['pid'],ancestor,processes),
                                executable_sha256=sha,executable_bytes=len(raw),expected_executable=expected_executable,
                                command=command,plan_sha256=plan_sha256,candidate=slot['candidate'],
                                arm=slot['arm'],arm_slot=slot['arm_slot'],global_slot=slot['global_slot'],
                                launch_binding=slot['launch_binding'],native_started=True)
                            write_once(output/f'NATIVE_BIRTH_{info["pid"]}_{info["start_ticks"]}.json',birth)
                            observed[key]=birth
                        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
            except Exception as error:
                errors.append(type(error).__name__+': '+str(error));return
            done.wait(.05)

    thread=threading.Thread(target=monitor,name='bounded-emx-birth-observer',daemon=True)
    thread.start()
    try:
        stdout,stderr=child.communicate()
    finally:
        done.set();thread.join()
    # Retain real tool output even when process-birth evidence is inconclusive.
    write_log(output/'wrapper_stdout.log',stdout)
    write_log(output/'wrapper_stderr.log',stderr)
    native_count=len(observed)
    write_once(output/'OBSERVATION_RECEIPT.json',dict(schema='eucap15_controlled64_native_observation.v1',
        status='ONE_NATIVE_BIRTH_OBSERVED' if native_count==1 and not errors else 'NATIVE_START_OBSERVATION_UNRESOLVED',
        ended_utc=utc(),wrapper_pid=child.pid,wrapper_returncode=child.returncode,
        native_starts_observed=native_count,actual_native_starts=native_count if native_count==1 and not errors else None,
        observations=list(observed.values()),errors=errors,slot=slot,automatic_retry_allowed=False,
        stdout_path=str(output/'wrapper_stdout.log'),stderr_path=str(output/'wrapper_stderr.log'),
        signals_sent=0,unobserved_does_not_mean_zero=True))
    # Fail only after the child has naturally ended; never interrupt a healthy solver.
    require(native_count==1 and not errors,'EXACT_NATIVE_BIRTH_NOT_ESTABLISHED_NO_AUTOMATIC_RETRY')
    return subprocess.CompletedProcess(command,child.returncode,stdout,stderr)
