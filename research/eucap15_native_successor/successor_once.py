"""One fixed256-to-fixed128 handoff, scheduled by atd, never a simulator queue."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid


class Blocked(RuntimeError):
    pass


def check(value, message):
    if not value:
        raise Blocked(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def pin(path):
    p = Path(path)
    raw = p.read_bytes()
    return dict(path=str(p), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def verify(value):
    observed = pin(value['path'])
    check(all(observed[k] == value[k] for k in ('sha256', 'bytes')), 'PIN_MISMATCH:' + value['path'])
    return observed


def read(path):
    return json.loads(Path(path).read_text())


def sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, value):
    path = Path(path)
    with path.open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    sync_dir(path.parent)


def state_write(root, value):
    path = root / ('state-' + uuid.uuid4().hex + '.tmp')
    write_new(path, value)
    os.replace(path, root / 'STATE.json')
    sync_dir(root)


@contextmanager
def adapter_lock(path):
    with Path(path).open('a') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def decision(snapshot, invoked=False):
    if snapshot.get('identity_error'):
        return 'BLOCKED_IDENTITY'
    if snapshot.get('successor_complete'):
        return 'DONE_SUCCESSOR_ALREADY_COMPLETE'
    if snapshot.get('new_owner_count', 0) > 1:
        return 'BLOCKED_DUPLICATE_SUCCESSOR'
    if snapshot.get('new_owner_count') == 1:
        return 'DONE_EXISTING_SUCCESSOR_NO_DUPLICATE'
    if invoked:
        return 'BLOCKED_PREEXEC_INTENT_WITHOUT_LIVE_OWNER_REVIEW_REQUIRED'
    if snapshot.get('successor_inspection_count', 0):
        return 'WAIT_SUCCESSOR_PREFLIGHT_NOT_OWNER'
    if snapshot.get('old_pid_reused'):
        return 'BLOCKED_PREDECESSOR_PID_REUSED'
    if snapshot.get('deadline_expired'):
        return 'BLOCKED_IMMUTABLE_DEADLINE_EXPIRED'
    if snapshot.get('old_owner_alive') or snapshot.get('old_related_count', 0):
        return 'WAIT_PREDECESSOR_ALIVE'
    if not snapshot.get('predecessor_complete'):
        return 'WAIT_PREDECESSOR_FULL_ACCOUNTING'
    if snapshot.get('native_count', 0):
        return 'WAIT_SURVIVING_NATIVE'
    return 'CHECK_FRESH_RESOURCE_AND_ORIGINAL_GATES'


def matching_jobs(jobs, token):
    ids = [job_id for job_id, body in jobs.items() if token in body]
    check(len(ids) <= 1, 'DUPLICATE_SCHEDULER_TICKET')
    return ids


def current_identity():
    fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=os.getpid(), start_ticks=fields[19])


def unconsumed_ticket(root, ticket):
    seen = set()
    while (root/'tickets'/ticket/'CONSUMED.json').exists():
        check(ticket not in seen, 'TICKET_RECOVERY_CYCLE')
        seen.add(ticket)
        ticket = read(root/'tickets'/ticket/'CONSUMED.json')['next_ticket']
        check(Path(ticket).name == ticket, 'UNSAFE_RECOVERY_TICKET')
    return ticket


def terminal_valid(path, release, count, frozen_ids=None):
    if not Path(path).exists():
        return False
    terminal = read(path)
    expected = 'ALL_FROZEN_256_ACCOUNTED' if count == 256 else 'ALL_ORIGINAL_128_ACCOUNTED'
    check(terminal.get('status') == expected, 'NONCOMPLETION_TERMINAL_NOT_A_HANDOFF')
    check(terminal.get('release') == release, 'TERMINAL_RELEASE_MISMATCH')
    rows = terminal.get('results', [])
    check(terminal.get('N_original_requests') == count, 'TERMINAL_DENOMINATOR_MISMATCH')
    check(len(rows) == count and len({r['request_id'] for r in rows}) == count, 'INCOMPLETE_TERMINAL_ACCOUNTING')
    if frozen_ids is not None:
        check({r['request_id'] for r in rows} == set(frozen_ids), 'FROZEN_ID_SET_MISMATCH')
    for row in rows:
        check(Path(row['request_id']).name == row['request_id'], 'UNSAFE_TERMINAL_ID')
        check(read(Path(path).parent / row['request_id'] / 'RESULT.json') == row, 'TERMINAL_RESULT_MISMATCH')
    return True


def successor_mode(args, config):
    script = config['successor_script']['path']
    if script not in args:
        return 'UNRELATED'
    expected = ['-B', script, '--release', config['successor_release']['path']]
    interpreters = (config['python']['path'], config['native_python_path'])
    if args[0] in interpreters and args[1:] == expected:
        return 'OWNER'
    if args[0] in interpreters and args.count('--preflight-only') == 1:
        stripped = [a for a in args[1:] if a != '--preflight-only']
        if stripped == expected:
            return 'PREFLIGHT'
    raise Blocked('UNRECOGNIZED_SUCCESSOR_PROCESS_ARGV')


def process_snapshot(config):
    result = []
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if p.stat().st_uid != os.getuid() or int(p.name) == os.getpid():
                continue
            args = [a.decode(errors='replace') for a in (p / 'cmdline').read_bytes().split(b'\0') if a]
            fields = (p / 'stat').read_text().rsplit(')', 1)[1].split()
            if not args or fields[0] == 'Z':
                continue
            native = any(Path(a).name in ('emx', 'emx_cae_singularity', 'calibre', 'virtuoso', 'strmout') for a in args[:3])
            related = any(str(config['predecessor_root']) in a for a in args)
            mode = successor_mode(args, config)
            new = mode == 'OWNER'
            old = config['predecessor_script']['path'] in args
            if native or related or mode != 'UNRELATED' or old or int(p.name) == config['predecessor_pid']:
                result.append(dict(pid=int(p.name), ppid=int(fields[1]), start_ticks=fields[19],
                                   argv=args, native=native, old_related=related or old, new_owner=new,
                                   successor_inspection=mode == 'PREFLIGHT'))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


class Adapter:
    def __init__(self, manifest_path, manifest_sha):
        self.manifest_path = Path(manifest_path)
        check(pin(self.manifest_path)['sha256'] == manifest_sha, 'MANIFEST_SHA_MISMATCH')
        self.manifest_sha = manifest_sha
        self.c = read(self.manifest_path)
        self.root = Path(self.c['state_root'])
        check(self.root.is_dir(), 'STATE_ROOT_MISSING')
        binding = read(self.root/'BINDING.json')
        check(binding['manifest_sha256'] == manifest_sha and binding['adapter_id'] == self.c['adapter_id'],
              'DURABLE_STATE_ROOT_BINDING_MISMATCH')
        check(os.getuid() == self.c['uid'], 'WRONG_ACCOUNT')
        for identity in self.c['pins']:
            verify(identity)
        check(pin(__file__) in self.c['pins'], 'ADAPTER_BYTES_NOT_BOUND')
        check(str(Path(self.c['python']['path']).resolve()) == self.c['python']['realpath'], 'PRIVATE_PYTHON_REALPATH_MISMATCH')
        self.env = dict(PATH='/usr/bin:/bin', HOME=self.c['home'], USER=self.c['username'],
                        LOGNAME=self.c['username'], SHELL='/bin/sh', LANG='C', LC_ALL='C')

    def event(self, status, **data):
        value = dict(utc=utc(), adapter_id=self.c['adapter_id'], pid=os.getpid(), status=status, **data)
        write_new(self.root / 'events' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex + '.json'), value)
        state_write(self.root, value)
        return value

    def at_jobs(self):
        q = subprocess.run(['/usr/bin/atq'], env=self.env, capture_output=True, text=True, timeout=10)
        check(q.returncode == 0, 'ATQ_UNAVAILABLE:' + q.stderr.strip())
        jobs = {}
        for line in q.stdout.splitlines():
            job_id = line.split()[0]
            check(job_id.isdigit(), 'UNRECOGNIZED_AT_QUEUE')
            body = subprocess.run(['/usr/bin/at', '-c', job_id], env=self.env, capture_output=True, text=True, timeout=10)
            check(body.returncode == 0, 'AT_JOB_IDENTITY_UNREADABLE:' + job_id)
            jobs[job_id] = body.stdout
        return jobs

    def schedule(self, ticket, delay_minutes=5):
        tdir = self.root / 'tickets' / ticket
        if not tdir.exists():
            tdir.mkdir(mode=0o700)
            write_new(tdir / 'TICKET.json', dict(ticket=ticket, manifest_sha256=self.manifest_sha, created_utc=utc()))
        else:
            check(read(tdir / 'TICKET.json')['manifest_sha256'] == self.manifest_sha, 'TICKET_MANIFEST_MISMATCH')
        token = 'eucap15-once-ticket:' + ticket
        jobs = matching_jobs(self.at_jobs(), token)
        receipt = tdir / 'SCHEDULE_RECEIPT.json'
        if jobs:
            value = dict(job_id=jobs[0], ticket=ticket, token=token, reconciled=True, utc=utc())
            if not receipt.exists():
                write_new(receipt, value)
            return value
        if (tdir / 'CONSUMED.json').exists():
            return dict(ticket=ticket, consumed=True)
        check(not receipt.exists() and not (tdir / 'SUBMIT_INTENT.json').exists(), 'AT_SUBMISSION_UNCERTAIN_NO_BLIND_RESUBMIT:' + ticket)
        command = [self.c['python']['path'], '-B', self.c['adapter_script'], 'tick', '--manifest',
                   str(self.manifest_path), '--manifest-sha', self.manifest_sha, '--ticket', ticket]
        payload = '# ' + token + '\nexec ' + shlex.join(command) + ' >>' + shlex.quote(str(self.root/'ADAPTER_STDOUT.log')) + ' 2>&1\n'
        with (tdir / 'job.sh').open('x') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        write_new(tdir / 'SUBMIT_INTENT.json', dict(utc=utc(), token=token, payload=pin(tdir / 'job.sh')))
        result = subprocess.run(['/usr/bin/at', '-M', '-f', str(tdir / 'job.sh'), 'now', '+', str(delay_minutes), 'minutes'],
                                env=self.env, capture_output=True, text=True, timeout=20)
        found = re.search(r'job (\d+) at (.+)', result.stderr)
        check(result.returncode == 0 and found, 'AT_SUBMISSION_FAILED:' + result.stderr.strip())
        value = dict(utc=utc(), job_id=found.group(1), scheduled_at_text=found.group(2), ticket=ticket,
                     token=token, payload=pin(tdir / 'job.sh'), stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest())
        write_new(receipt, value)
        return value

    def snapshot(self):
        c = self.c
        start = read(c['predecessor_start']['path'])
        check(start['pid'] == c['predecessor_pid'] and str(start['start_ticks']) == c['predecessor_start_ticks'], 'PREDECESSOR_START_IDENTITY')
        procs = process_snapshot(c)
        old_pid = [p for p in procs if p['pid'] == c['predecessor_pid']]
        new = [p for p in procs if p['new_owner']]
        for p in new:
            check('--release' in p['argv'] and p['argv'][p['argv'].index('--release')+1] == c['successor_release']['path'], 'UNKNOWN_SUCCESSOR_RELEASE')
        oldalive = any(p['start_ticks'] == c['predecessor_start_ticks'] for p in old_pid)
        oldreused = any(p['start_ticks'] != c['predecessor_start_ticks'] for p in old_pid)
        terminal = False
        if Path(c['predecessor_terminal']).exists():
            verify(c['frozen_proposals'])
            proposals = [json.loads(line) for line in Path(c['frozen_proposals']['path']).read_text().splitlines()]
            check(len(proposals) == 256, 'ORIGINAL_FROZEN_COUNT')
            terminal = terminal_valid(c['predecessor_terminal'], c['predecessor_release'], 256,
                                      [j['request_id'] for j in proposals])
            by_id = {j['request_id']: j for j in proposals}
            for row in read(c['predecessor_terminal'])['results']:
                original = by_id[row['request_id']]
                check(row['candidate_id'] == original['candidate_id'] and row['q_proxy'] == original['q_proxy'],
                      'PREDECESSOR_FROZEN_SELECTION_CHANGED')
        new_complete = terminal_valid(c['successor_terminal'], c['successor_release'], 128, c['successor_request_ids'])
        return dict(utc=utc(), old_owner_alive=oldalive, old_pid_reused=oldreused,
                    old_related_count=sum(p['old_related'] for p in procs), new_owner_count=len(new),
                    successor_inspection_count=sum(p['successor_inspection'] for p in procs),
                    native_count=sum(p['native'] for p in procs), processes=procs,
                    predecessor_complete=terminal, successor_complete=new_complete,
                    deadline_expired=datetime.now(timezone.utc) >= datetime.fromisoformat(c['deadline_utc']))

    def original_admission(self):
        code = Path(self.c['successor_script']['path'])
        sys.path.insert(0, str(code.parent))
        spec = importlib.util.spec_from_file_location('frozen_development_successor', code)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        owner = module.Owner(self.c['successor_release']['path'])
        owner.predecessor_closed()
        snapshot = module.probe(owner.config)
        evidence = self.event('ORIGINAL_FRESH_RESOURCE_CHECK', resource=snapshot)
        return owner, snapshot, evidence

    def run_ticket(self, ticket):
        tdir = self.root / 'tickets' / ticket
        check(tdir.is_dir() and Path(ticket).name == ticket, 'UNKNOWN_TICKET')
        check(read(tdir / 'TICKET.json')['manifest_sha256'] == self.manifest_sha, 'TICKET_IDENTITY')
        state = read(self.root/'STATE.json') if (self.root/'STATE.json').exists() else {}
        if state.get('status', '').startswith(('DONE_', 'BLOCKED_')):
            return state
        if (tdir / 'CONSUMED.json').exists():
            return dict(status='DUPLICATE_TICKET_IGNORED', ticket=ticket)
        # Enqueue the next bounded check before doing any expensive admission work.
        next_file = tdir / 'NEXT_TICKET.json'
        if not next_file.exists():
            write_new(next_file, dict(ticket=uuid.uuid4().hex))
        next_ticket = read(next_file)['ticket']
        queued = self.schedule(next_ticket, self.c['interval_minutes'])
        write_new(tdir / 'CONSUMED.json', dict(utc=utc(), next_ticket=next_ticket))
        snap = self.snapshot()
        intent = self.root / 'NATIVE_EXEC_INTENT.json'
        action = decision(snap, intent.exists())
        if action != 'CHECK_FRESH_RESOURCE_AND_ORIGINAL_GATES':
            launched_here = False
            if intent.exists() and action == 'DONE_EXISTING_SUCCESSOR_NO_DUPLICATE':
                invocation = read(intent)
                launched_here = any(p['new_owner'] and p['pid'] == invocation['pid'] and
                                    p['start_ticks'] == invocation['start_ticks'] for p in snap['processes'])
                if launched_here:
                    action = 'DONE_SUCCESSOR_LAUNCH_OBSERVED'
            return self.event(action, snapshot=snap, next_check=queued,
                              successor_started_by_adapter=launched_here, invocation_intent_present=intent.exists())
        owner, resource, _ = self.original_admission()
        if resource['status'] != 'PASS':
            return self.event('WAIT_FRESH_RESOURCE', resource=resource, next_check=queued)
        final = self.snapshot()
        check(decision(final, intent.exists()) == 'CHECK_FRESH_RESOURCE_AND_ORIGINAL_GATES', 'FINAL_HANDOFF_CHANGED')
        owner.verify_release()
        owner.predecessor_closed()
        command = [self.c['python']['path'], '-B', self.c['successor_script']['path'],
                   '--release', self.c['successor_release']['path']]
        identity = current_identity()
        write_new(intent, dict(status='DURABLE_PRE_EXEC_INTENT_NOT_NATIVE_SUCCESS', utc=utc(), **identity,
                  argv=command, manifest_sha256=self.manifest_sha,
                  release=self.c['successor_release'], source=self.c['successor_script'], resource=resource))
        self.event('NATIVE_EXEC_INTENT_COMMITTED', invocation=pin(intent), next_check=queued)
        env = dict(owner.env)
        env.update(HOME=self.c['home'], USER=self.c['username'], LOGNAME=self.c['username'],
                   PATH='/usr/bin:/bin', PYTHONUNBUFFERED='1')
        with (self.root/'NATIVE_STDOUT.log').open('xb') as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            os.execve(command[0], command, env)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['install', 'tick', 'stop-scheduling'])
    p.add_argument('--manifest', required=True)
    p.add_argument('--manifest-sha', required=True)
    p.add_argument('--ticket')
    args = p.parse_args()
    adapter = Adapter(args.manifest, args.manifest_sha)
    try:
        with adapter_lock(adapter.root/'DISPATCH.lock'):
            if args.action == 'install':
                prior = read(adapter.root/'STATE.json') if (adapter.root/'STATE.json').exists() else {}
                if prior.get('status', '').startswith(('DONE_', 'BLOCKED_')):
                    print(json.dumps(prior))
                    return
                initial = adapter.root/'INITIAL_TICKET.json'
                if not initial.exists():
                    write_new(initial, dict(ticket=uuid.uuid4().hex))
                ticket = unconsumed_ticket(adapter.root, read(initial)['ticket'])
                queued = adapter.schedule(ticket, 0)
                value = adapter.event('INSTALLED_ATD_INITIAL_CHECK_QUEUED', next_check=queued,
                                      release=adapter.c['successor_release'])
            elif args.action == 'stop-scheduling':
                value = adapter.event('DONE_SCHEDULING_STOPPED_NO_CHILD_SIGNAL')
            else:
                check(args.action == 'tick', 'UNSUPPORTED_ACTION')
                value = adapter.run_ticket(args.ticket)
            print(json.dumps(value))
    except BlockingIOError:
        print(json.dumps(dict(status='ADAPTER_MUTEX_BUSY_NO_NATIVE_ACTION')))
    except Exception as error:
        adapter.event('BLOCKED_EXCEPTION_NO_NATIVE_RETRY', error=type(error).__name__+': '+str(error))
        raise


if __name__ == '__main__':
    main()
