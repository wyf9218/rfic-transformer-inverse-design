"""Finite two-worker CPU pool around the existing frequency_study entry.

No daemon, production controller, device-lock deletion, or automatic retry.
The parent global lease is inherited through all training descendants.
"""
from __future__ import annotations

import argparse
import fcntl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from .io import read_json, utc_now
from .study_once import lease, pin, verify_pin, atomic_json, resource_snapshot, BusyStudy

ORDER=[5,10,20,6,7,8,9,11,12,13,14,16,17,18,19,15]


def validate(config):
    if config.get('schema')!='frequency_finite_queue.v1' or config['workers']!=2:
        raise ValueError('exact two-worker finite queue required')
    if [j['frequency_ghz'] for j in config['jobs']]!=ORDER:
        raise ValueError('exact sixteen-frequency priority order required')
    seen=set();data_shas=set()
    for index, job in enumerate(config['jobs']):
        request=read_json(verify_pin(job['request']))
        train=request['train']
        if train['frequency_ghz']!=job['frequency_ghz'] or train['device']!='cpu' or train['threads']!=2:
            raise ValueError('route/CPU budget mismatch')
        if train['label_mode']!='STRICT_LUMPED':raise ValueError('label contract differs')
        resolved_out=str(Path(request['out']).resolve())
        if resolved_out in seen:raise ValueError('duplicate output directory')
        seen.add(resolved_out)
        if not job.get('reuse_existing'):
            if train['deadline_utc']!=config['deadline_utc']:raise ValueError('new-job deadline differs')
            if Path(request['device_lock']).resolve()!=Path(config['slot_locks'][str(index%2)]).resolve():
                raise ValueError('immutable fixed slot lock required')
            if Path(request['device_lock']).resolve()==Path(config['global_device_lock']).resolve():
                raise ValueError('worker may not relock parent global reservation')
            data_shas.add(request['dataset_sha256'])
        for command in job.get('on_ready_commands',[]):
            for value in command['pins']:verify_pin(value)
            if not command['argv'] or command['argv'][0]!=sys.executable:
                raise ValueError('callback must use current independent Python')
    if len(data_shas)!=1:raise ValueError('all new frequencies must share one frozen dataset')
    if len({Path(p).resolve() for p in config['slot_locks'].values()})!=2:
        raise ValueError('distinct worker slot locks required')
    return config


def _completed_pair(job, request):
    from .frequency_study import _verify_pair
    path=Path(request['out'])/'PAIR_RECEIPT.json'
    if not path.exists():return None
    pair=read_json(path)
    _verify_pair(pair,request,job['request'])
    return pin(path)


def worker(config_path,index,fd_values,resume=False):
    config=validate(read_json(config_path));job=config['jobs'][index]
    if len(fd_values)!=2 or len(set(fd_values))!=2:
        raise ValueError('two inherited reservation descriptors required')
    expected=(Path(config['out'])/'queue.lock',Path(config['global_device_lock']))
    for fd,path in zip(fd_values,expected):
        actual=os.fstat(fd);bound=path.stat()
        if (actual.st_dev,actual.st_ino)!=(bound.st_dev,bound.st_ino):
            raise ValueError('reservation descriptor file identity mismatch')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    request=read_json(job['request']['path'])
    root=Path(config['out'])/'jobs'/f"f{job['frequency_ghz']:02d}"
    root.mkdir(parents=True,exist_ok=True)
    with lease(root/'worker.lock'):
        receipt=root/'JOB_RECEIPT.json'
        if receipt.exists():
            old=read_json(receipt)
            _check_job(old,job)
            verify_pin(old['pair'])
            return old
        from . import frequency_study as study
        original_child,original_finish=study.run_child,study._finish
        def child(argv,cwd,log_path,lock_fds):
            return original_child(argv,cwd,log_path,tuple(set(lock_fds)|set(fd_values)))
        def finish(request,root,lock_fds):
            return original_finish(request,root,tuple(set(lock_fds)|set(fd_values)))
        study.run_child,study._finish=child,finish
        try:
            pair=_completed_pair(job,request)
            if job.get('reuse_existing'):
                if pair is None:raise ValueError('existing frequency must have exact committed pair')
                result={'status':'REUSED_EXISTING_READ_ONLY'}
            elif pair is not None:
                result={'status':'REUSED_COMMITTED_PAIR'}
            else:
                result=study.run(job['request']['path'],resume=resume)
                pair=_completed_pair(job,request)
            if pair is None:
                atomic_json(root/f"OBSERVATION_{utc_now().replace(':','').replace('+','_')}.json",
                    dict(created_utc=utc_now(),result=result,request=job['request']),immutable=True)
                return result
            posttrain=Path(request['out'])/'posttrain/POSTTRAIN_RECEIPT.json'
            if request.get('posttrain') and not posttrain.exists():
                raise ValueError('existing pair lacks completed posttrain; explicit recovery required')
            callbacks=[]
            for i,command in enumerate(job.get('on_ready_commands',[])):
                completed=root/f'CALLBACK_{i:02d}.json'
                if completed.exists():
                    old=read_json(completed)
                    if old['command']!=command or old['returncode']!=0:
                        raise ValueError('prior callback failed/changed; inspect without repeating')
                    verify_pin(old['log'])
                    callbacks.append(pin(completed));continue
                for value in command['pins']:verify_pin(value)
                log=root/f'callback_{i:02d}.log'
                if log.exists():raise ValueError('interrupted callback: explicit recovery required')
                with log.open('xb') as stream:
                    child_result=subprocess.run(command['argv'],cwd=command['cwd'],pass_fds=tuple(fd_values),
                        stdout=stream,stderr=subprocess.STDOUT,check=False)
                atomic_json(completed,dict(created_utc=utc_now(),command=command,returncode=child_result.returncode,log=pin(log)),immutable=True)
                if child_result.returncode:raise ValueError('post-pair callback failed')
                callbacks.append(pin(completed))
            value=dict(schema='frequency_queue_job.v1',status='PAIR_READY',frequency_ghz=job['frequency_ghz'],
                       created_utc=utc_now(),request=job['request'],pair=pair,training_result=result['status'],callbacks=callbacks,
                       posttrain=pin(posttrain) if posttrain.exists() else None)
            atomic_json(receipt,value,immutable=True)
            return value
        finally:
            study.run_child,study._finish=original_child,original_finish


def _check_job(old,job):
    if (old.get('schema')!='frequency_queue_job.v1' or old.get('status')!='PAIR_READY' or
            old.get('request')!=job['request'] or old.get('frequency_ghz')!=job['frequency_ghz']):
        raise ValueError('completed job identity differs')
    verify_pin(old['pair'])
    if old.get('posttrain'):verify_pin(old['posttrain'])
    commands=job.get('on_ready_commands',[])
    if len(old.get('callbacks',[]))!=len(commands):
        raise ValueError('completed callback count differs')
    for value,command in zip(old.get('callbacks',[]),commands):
        callback=read_json(verify_pin(value))
        if callback.get('command')!=command or callback.get('returncode')!=0:
            raise ValueError('completed callback identity/status differs')
        verify_pin(callback['log'])


def run(config_path,resume=False):
    config=validate(read_json(config_path));root=Path(config['out']);root.mkdir(parents=True,exist_ok=True)
    with lease(root/'queue.lock') as queue_fd,lease(config['global_device_lock']) as global_fd:
        binding=root/'QUEUE_IDENTITY.json'
        if binding.exists():
            if read_json(binding)!=pin(config_path):raise ValueError('queue configuration changed')
        else:atomic_json(binding,pin(config_path),immutable=True)
        resource=resource_snapshot(root,device='cpu',min_available_bytes=6*1024**3,min_disk_bytes=10*1024**3)
        resource.update(cpu_threads_authorized=4,max_concurrent_training=2)
        if resource['status']!='PASS' or resource['cpu_logical']<4:
            result=dict(status='WAITING_RESOURCE',resources=resource,created_utc=utc_now())
            atomic_json(root/'RUN_STATE.json',result);return result
        if datetime.now(timezone.utc)>=datetime.fromisoformat(config['deadline_utc'].replace('Z','+00:00')):
            return {'status':'PARTIAL_BUDGET_EXHAUSTED'}
        atomic_json(root/'RUN_STATE.json',dict(status='RUNNING',pid=os.getpid(),resources=resource,created_utc=utc_now()))
        def lane(slot):
            results=[]
            for index in range(slot,len(config['jobs']),2):
                job=config['jobs'][index];jobroot=root/'jobs'/f"f{job['frequency_ghz']:02d}"
                jobroot.mkdir(parents=True,exist_ok=True)
                if (jobroot/'JOB_RECEIPT.json').exists():
                    old=read_json(jobroot/'JOB_RECEIPT.json');_check_job(old,job)
                    results.append(dict(frequency_ghz=job['frequency_ghz'],status='ALREADY_COMPLETE'));continue
                if datetime.now(timezone.utc)>=datetime.fromisoformat(config['deadline_utc'].replace('Z','+00:00')):
                    results.append(dict(frequency_ghz=job['frequency_ghz'],status='NOT_STARTED_BUDGET_EXHAUSTED'));continue
                logs=sorted(jobroot.glob('worker_*.log'))
                if logs and not resume:
                    results.append(dict(frequency_ghz=job['frequency_ghz'],status='NEEDS_EXPLICIT_RESUME'));continue
                log=jobroot/f'worker_{len(logs)+1:04d}.log'
                argv=[sys.executable,'-B','-m','research.broadband56_nn.frequency_queue','worker',
                      '--config',str(Path(config_path).resolve()),'--index',str(index),
                      '--reservation-fd',str(queue_fd),'--reservation-fd',str(global_fd)]
                if resume:argv.append('--resume')
                env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',VECLIB_MAXIMUM_THREADS='2')
                with log.open('xb') as stream:
                    child=subprocess.Popen(argv,cwd=Path(__file__).resolve().parents[2],stdout=stream,stderr=subprocess.STDOUT,
                                           pass_fds=(queue_fd,global_fd),env=env)
                    atomic_json(jobroot/f'PROCESS_{len(logs)+1:04d}.json',dict(pid=child.pid,created_utc=utc_now(),argv=argv,slot=slot),immutable=True)
                    rc=child.wait()
                value=dict(frequency_ghz=job['frequency_ghz'],returncode=rc,log=pin(log),
                           status='PAIR_READY' if (jobroot/'JOB_RECEIPT.json').exists() else 'INCOMPLETE_OR_FAILED')
                atomic_json(jobroot/f'EXIT_{len(logs)+1:04d}.json',value,immutable=True);results.append(value)
            return results
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(lane,0);b=pool.submit(lane,1);results=a.result()+b.result()
        final=dict(status='FINITE_QUEUE_PASS_ENDED',created_utc=utc_now(),jobs=results,resources=resource,
                   all_pairs_ready=all((root/'jobs'/f"f{j['frequency_ghz']:02d}"/'JOB_RECEIPT.json').exists() for j in config['jobs']))
        atomic_json(root/'RUN_STATE.json',final)
        return final


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('run','worker'))
    p.add_argument('--config',required=True);p.add_argument('--resume',action='store_true')
    p.add_argument('--index',type=int);p.add_argument('--reservation-fd',type=int,action='append',default=[])
    a=p.parse_args()
    result=run(a.config,a.resume) if a.action=='run' else worker(a.config,a.index,a.reservation_fd,a.resume)
    print(json.dumps(result))


if __name__=='__main__':main()
