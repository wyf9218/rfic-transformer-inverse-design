"""Read-only, shared single-native-job admission; never launches a simulator."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone


def utc():
    return datetime.now(timezone.utc).isoformat()


def counters():
    mem = {s.split(':')[0]: int(s.split(':')[1].split()[0])*1024
           for s in Path('/proc/meminfo').read_text().splitlines()}
    vm = dict(s.split() for s in Path('/proc/vmstat').read_text().splitlines())
    cpu = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    return dict(memory=mem, vm={k: int(vm[k]) for k in ('pswpin','pswpout','oom_kill')}, cpu=cpu)


def license_query(env_file, variable):
    loader = '/lib64/ld-linux-x86-64.so.2'
    lmutil = '/cae/apps/data/cadence-2023/installs/INNOVUS211/tools/bin/lmutil'
    command = f'. {env_file}; exec {loader} {lmutil} lmstat -a -c "$(printenv {variable})"'
    try:
        p = subprocess.run(['bash','-lc',command], capture_output=True, text=True, timeout=30)
        pattern = r'Users of ([^:]+):\s+\(Total of (\d+) licenses? issued;\s+Total of (\d+) licenses? in use\)'
        counts = {name.strip().lower(): int(total)-int(used)
                  for name,total,used in re.findall(pattern,p.stdout,re.I)}
        return dict(pass_query=p.returncode==0 and 'license server UP' in p.stdout,
                    free=counts, response_sha256=hashlib.sha256(p.stdout.encode()).hexdigest())
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(pass_query=False,free={},error_type=type(error).__name__)


def competing_processes(config):
    """A dead parent or an instant zero-native count is insufficient for handoff."""
    result=[]
    own=os.getpid()
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if p.stat().st_uid != os.getuid() or int(p.name)==own: continue
            args=[v.decode(errors='replace') for v in (p/'cmdline').read_bytes().split(b'\0') if v]
            state=(p/'stat').read_text().rsplit(')',1)[1].split()
            if not args or state[0]=='Z': continue
            joined=' '.join(args)
            native=any(Path(a).name in ('virtuoso','strmout','calibre','emx','emx_cae_singularity') for a in args[:3])
            old_chain=config['original_production_root'] in joined or any(
                Path(a).name in ('run_broadband56_v2_authorized_queue_controller.py',
                                'run_broadband56_v2_rebound_queue_controller.py') for a in args[:3])
            if native or old_chain:
                result.append(dict(pid=int(p.name), start_ticks=state[19], native=native,
                                   old_chain=old_chain, executable=args[0]))
        except (FileNotFoundError, ProcessLookupError): pass
    return result


def evaluate(config, before, after, interval, licenses, disk, load, cpus, peers):
    cpu_shape_valid=len(before['cpu'])==len(after['cpu'])==8 and all(
        type(x) is int and x>=0 for x in before['cpu']+after['cpu'])
    delta=[b-a for a,b in zip(before['cpu'],after['cpu'])] if cpu_shape_valid else None
    cpu_sample_valid=cpu_shape_valid and all(x>=0 for x in delta) and sum(delta)>0
    vm={k:after['vm'][k]-before['vm'][k] for k in after['vm']}
    m=after['memory']
    iowait=100*delta[4]/sum(delta) if cpu_sample_valid else None
    checks=dict(
        cpu=cpus>=4 and load[0]<cpus-2 and load[0]/cpus<=config['normalized_load1_max']
            and load[1]/cpus<=config['normalized_load5_max'],
        memory=m['MemAvailable']/m['MemTotal']>=config['minimum_available_memory_fraction']
            and m['MemAvailable']>=config['resource_budget']['min_memory_available_bytes'],
        swap=not(vm['pswpout']>0 and m['MemAvailable']<before['memory']['MemAvailable']),
        oom=vm['oom_kill']==0, iowait=cpu_sample_valid and iowait<5,
        sample=interval>=60 and min(vm.values())>=0 and cpu_sample_valid,
        storage=disk>=config['resource_budget']['min_disk_free_bytes'],
        licenses=all(licenses.values()), isolation=not peers)
    return dict(status='PASS' if all(checks.values()) else 'WAIT',utc=utc(),checks=checks,
                failed_checks=[k for k,v in checks.items() if not v],logical_cpus=cpus,load=list(load),
                available_memory_bytes=m['MemAvailable'],memory_fraction=m['MemAvailable']/m['MemTotal'],
                free_disk_bytes=disk,iowait_percent=iowait,cpu_deltas=delta,cpu_sample_valid=cpu_sample_valid,
                swap_deltas=vm,interval_seconds=interval,
                competing_processes=peers)


def probe(config):
    first=counters(); start=time.monotonic(); time.sleep(60); last=counters()
    elapsed=time.monotonic()-start
    cad=license_query('/cae/apps/env/cadence-EMX_25.10.000','CDS_LIC_FILE')
    cal=license_query('/cae/apps/env/mentor-CALIBRE_2024.4_39','LM_LICENSE_FILE')
    c,m=cad['free'],cal['free']
    licenses=dict(cadence=cad['pass_query'] and any(v>=1 for k,v in c.items() if k.startswith('virtuoso_layout_suite_')),
                  calibre=cal['pass_query'] and m.get('calibredrc',0)>=1,
                  emx=cad['pass_query'] and min(c.get('emx_modelgen',0),c.get('emx_solver',0))>=1)
    result=evaluate(config,first,last,elapsed,licenses,shutil.disk_usage(config['out']).free,
                    os.getloadavg(),os.cpu_count(),competing_processes(config))
    result['license_evidence']=dict(cadence={k:v for k,v in cad.items() if k!='free'},
                                    calibre={k:v for k,v in cal.items() if k!='free'})
    return result
