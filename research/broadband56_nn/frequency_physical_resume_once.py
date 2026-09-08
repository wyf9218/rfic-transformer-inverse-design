"""Wait for one original Linux process identity, then invoke Dispatcher once.

This is not a scheduler or retry loop. The existing Dispatcher retains all
science, PARTIAL/FAILURE, resource and lease gates. The wait deadline limits
admission only: an admitted child is waited for without timeout or signals.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def pin(path):
    path = Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'Regular non-symlink input required')
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    require((before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) ==
            (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns), 'Input changed during read')
    return dict(path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def verify(value):
    require(set(value) == {'path', 'bytes', 'sha256'} and pin(value['path']) == value, 'Exact input pin differs')
    return Path(value['path'])


def write_once(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    return pin(path)


def config_at(path, expected=None):
    identity = pin(path)
    require(expected is None or identity == expected, 'Wait configuration changed')
    c = read(path)
    require(set(c) == {'schema', 'original_process', 'base_config', 'operational_budget', 'release',
                      'python', 'cwd', 'out', 'wait_deadline_utc', 'poll_seconds', 'waiter_source'},
            'Unexpected wait configuration fields')
    require(c['schema'] == 'frequency_physical_resume_once.v1', 'Wrong wait schema')
    original = c['original_process']
    require(set(original) == {'pid', 'uid', 'start_ticks'} and
            all(type(original[k]) is int for k in original) and original['pid'] > 0 and
            original['uid'] >= 0 and original['start_ticks'] > 0, 'Exact original PID/UID/start_ticks required')
    require(type(c['poll_seconds']) in (int, float) and 0 < c['poll_seconds'] <= 60, 'Poll interval must be in (0,60] seconds')
    deadline = datetime.fromisoformat(c['wait_deadline_utc'].replace('Z', '+00:00'))
    require(deadline.tzinfo is not None, 'Zoned finite wait deadline required')
    require(verify(c['waiter_source']) == Path(__file__).absolute(), 'Waiter source identity differs')
    base = read(verify(c['base_config']))
    budget = read(verify(c['operational_budget']))
    require(base['schema'] == 'frequency_physical_finite_dispatch.v1' and
            budget['schema'] == 'frequency_physical_operational_budget.v1', 'Wrong base/budget schema')
    require(budget['base_config'] == c['base_config'] and
            budget['original_dispatch_deadline_utc'] == base['dispatch_deadline_utc'] and
            budget['release'] == c['release'], 'Budget/base/release cross-binding differs')
    old_end = datetime.fromisoformat(budget['original_dispatch_deadline_utc'].replace('Z', '+00:00'))
    new_end = datetime.fromisoformat(budget['new_dispatch_deadline_utc'].replace('Z', '+00:00'))
    require(old_end.tzinfo is not None and new_end.tzinfo is not None and new_end > old_end,
            'Operational budget must explicitly preserve and extend original deadline')
    release = c['release']
    require(set(release) == {'code_root', 'source_pins'}, 'Unexpected release fields')
    names = {'research/__init__.py', 'research/broadband56_nn/__init__.py',
             *['research/broadband56_nn/' + n for n in ('io.py', 'frequency_physical_dispatch.py',
               'frequency_research_emx.py', 'frequency_research_gds_audit.py', 'frequency_research_calibre.py')]}
    require(Path(release['code_root']).is_absolute(), 'Absolute release path required')
    actual = [str(verify(p)) for p in release['source_pins']]
    require(len(actual) == 7 and set(actual) == {str(Path(release['code_root']) / n) for n in names},
            'Exact seven-source release closure required')
    require(c['python'] == base['python'] and c['cwd'] == base['repo'], 'Original Python/cwd differs')
    require(Path(c['python']).is_absolute() and Path(c['python']).is_file() and
            os.access(c['python'], os.X_OK) and Path(c['cwd']).is_dir(), 'Python/cwd not locally accessible')
    out = Path(c['out'])
    require(out.is_absolute() and out != Path(base['out']) and
            not out.is_relative_to(Path(base['out'])), 'Wait output must be separate from original queue')
    require(not any(p.is_symlink() for p in (out, *out.parents)), 'Symlink wait output forbidden')
    return c, identity, deadline


def process_identity(pid, proc_root='/proc'):
    """Read coherent Linux identity; absence is distinct from inaccessible data."""
    require(Path(proc_root).is_dir(), 'Linux proc filesystem is unavailable')
    root = Path(proc_root) / str(pid)
    def stat_fields():
        text = (root / 'stat').read_text()
        require(text.partition(' ')[0] == str(pid) and ')' in text, 'Invalid proc stat PID')
        fields = text[text.rfind(')') + 2:].split()
        return dict(state=fields[0], start_ticks=int(fields[19]))
    try:
        first = stat_fields()
        lines = (root / 'status').read_text().splitlines()
        uid = int(next(line for line in lines if line.startswith('Uid:')).split()[1])
        last = stat_fields()
    except FileNotFoundError:
        return dict(status='ABSENT', pid=pid)
    if first['start_ticks'] != last['start_ticks']:
        return dict(status='RACE_RECHECK', pid=pid)
    return dict(status='PRESENT', pid=pid, uid=uid, **last)


def original_exited(observed, original):
    if observed['status'] == 'ABSENT':
        return True
    if observed['status'] == 'RACE_RECHECK':
        return False
    require(observed['status'] == 'PRESENT', 'Unknown original process observation')
    if observed['start_ticks'] != original['start_ticks']:
        return True  # PID recycled; never signal or wait for the unrelated owner.
    require(observed['uid'] == original['uid'], 'Original PID start_ticks matched but UID differs')
    return observed['state'] in ('Z', 'X')


def command(c):
    return [c['python'], '-B', '-m', 'research.broadband56_nn.frequency_physical_dispatch',
            '--config', c['base_config']['path'], '--operational-budget', c['operational_budget']['path']]


def check(path):
    c, identity, deadline = config_at(path)
    observation = process_identity(c['original_process']['pid'])
    return dict(status='CHECK_PASS_READ_ONLY', config=identity, original=observation,
                original_identity_exited=original_exited(observation, c['original_process']),
                wait_deadline_expired=now() >= deadline, output_exists=Path(c['out']).exists(),
                dispatcher_command=command(c), process_started=False)


def run(path):
    c, identity, deadline = config_at(path)
    out = Path(c['out'])
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return dict(status='ALREADY_CREATED_NO_DUPLICATE', output=str(out), process_started=False)
    child = None
    try:
        write_once(out / 'WAIT_INTENT.json', dict(config=identity, original_process=c['original_process'],
                   created_utc=now().isoformat(), wait_deadline_utc=c['wait_deadline_utc'], no_retries=True))
        while True:
            config_at(path, identity)
            observed = process_identity(c['original_process']['pid'])
            with (out / 'observations.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(utc=now().isoformat(), original=observed)) + '\n')
            if now() >= deadline:
                result = dict(status='WAIT_DEADLINE_PARTIAL', original=observed, process_started=False,
                              config=identity, completed_utc=now().isoformat())
                write_once(out / 'TERMINAL.json', result)
                return result
            if original_exited(observed, c['original_process']):
                # Recheck all pinned inputs and the original identity immediately
                # before one admission. Dispatcher still owns all physical gates.
                config_at(path, identity)
                observed = process_identity(c['original_process']['pid'])
                if original_exited(observed, c['original_process']) and now() < deadline:
                    break
            time.sleep(min(c['poll_seconds'], max(0, (deadline - now()).total_seconds()), 60))
        environment = dict(os.environ, PYTHONOPTIMIZE='0', PYTHONDONTWRITEBYTECODE='1',
                           PYTHONPATH=c['release']['code_root'] + os.pathsep + c['cwd'],
                           OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
        environment.pop('RFIC_RESEARCH_OPERATIONAL_BUDGET', None)
        environment.pop('RFIC_RESEARCH_OPERATIONAL_BUDGET_USE', None)
        argv = command(c)
        intent = write_once(out / 'DISPATCH_INTENT.json', dict(config=identity, argv=argv, cwd=c['cwd'],
            operational_budget=c['operational_budget'], release=c['release'], original_exit=observed,
            pythonpath=environment['PYTHONPATH'], created_utc=now().isoformat(), max_invocations=1))
        with (out / 'dispatcher.stdout').open('xb') as stdout, (out / 'dispatcher.stderr').open('xb') as stderr:
            child = subprocess.Popen(argv, cwd=c['cwd'], env=environment, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr)
            try:
                observation = process_identity(child.pid)
            except Exception as error:
                observation = dict(status='UNAVAILABLE', pid=child.pid,
                                   error=type(error).__name__ + ': ' + str(error))
            child_receipt = write_once(out / 'CHILD_LAUNCH_RECEIPT.json', dict(
                schema='frequency_physical_resume_child_launch.v1',
                status='EXISTING_DISPATCHER_STARTED_NOT_COMPLETION', pid=child.pid,
                uid=observation.get('uid'), start_ticks=observation.get('start_ticks'),
                command=argv, base_config=c['base_config'], operational_budget=c['operational_budget'],
                release=c['release'], waiter_configuration=identity, created_utc=now().isoformat(),
                child_observation=observation, dispatch_intent=intent,
                wait_deadline_is_not_child_timeout=True))
            code = child.wait()  # No timeout, retry, terminate or signal.
        stdout_pin, stderr_pin = pin(out / 'dispatcher.stdout'), pin(out / 'dispatcher.stderr')
        try:
            result = read(out / 'dispatcher.stdout')
            status = result.get('status')
        except (ValueError, AttributeError):
            result, status = None, None
        mapped = {'FINITE_ROUNDROBIN_COMPLETE': 'DISPATCH_COMPLETE',
                  'PARTIAL_BUDGET_ENDED_NO_CHILD_STOPPED': 'DISPATCH_PARTIAL',
                  'ALREADY_OWNED_NO_DUPLICATE': 'DISPATCH_NOT_STARTED_ALREADY_OWNED'}
        terminal = dict(status=('DISPATCH_FAILED_NO_RETRY' if code else
                               mapped.get(status, 'DISPATCH_RESULT_UNRECOGNIZED_NO_RETRY')),
            config=identity, child_receipt=child_receipt, returncode=code, stdout=stdout_pin, stderr=stderr_pin,
            dispatcher_result=result, physical_continuation_claim=(code == 0 and status == 'FINITE_ROUNDROBIN_COMPLETE'),
            completed_utc=now().isoformat(), invocations=1, retries=0)
        write_once(out / 'TERMINAL.json', terminal)
        return terminal
    except Exception as error:
        failure = dict(status='WAIT_OR_DISPATCH_FAILED_NO_RETRY', config=identity,
                       error=type(error).__name__ + ': ' + str(error), child_pid=None if child is None else child.pid,
                       child_may_be_running=child is not None,
                       created_utc=now().isoformat(), no_child_signal_sent=True)
        try:
            write_once(out / 'FAILURE.json', failure)
        finally:
            # Even an evidence-write failure must not abandon an admitted child.
            # Preserve the original failure; never turn its eventual exit into
            # success or use the wait deadline as a timeout for physical work.
            if child is not None:
                code = child.poll()
                if code is None:
                    code = child.wait()
                child_exit = write_once(out / 'CHILD_EXIT_AFTER_FAILURE.json', dict(
                    status='CHILD_EXITED_ORIGINAL_FAILURE_RETAINED', child_pid=child.pid,
                    returncode=code, original_error=failure['error'],
                    physical_continuation_claim=False, no_child_signal_sent=True,
                    completed_utc=now().isoformat()))
                failure.update(child_may_be_running=False, child_returncode=code,
                               child_exit=child_exit, physical_continuation_claim=False)
        return failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--check', action='store_true', help='Read-only; no output directory or process creation')
    args = parser.parse_args()
    result = check(args.config) if args.check else run(args.config)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('CHECK_PASS_READ_ONLY', 'DISPATCH_COMPLETE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
