"""Synthetic proc and subprocess fixtures; no server, simulator or model calls."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from research.broadband56_nn import frequency_physical_resume_once as once


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return once.pin(path)


@pytest.fixture
def config(tmp_path, monkeypatch):
    release = tmp_path / 'release'
    names = ['research/__init__.py', 'research/broadband56_nn/__init__.py',
             *['research/broadband56_nn/' + n for n in ('io.py', 'frequency_physical_dispatch.py',
               'frequency_research_emx.py', 'frequency_research_gds_audit.py', 'frequency_research_calibre.py')]]
    pins = []
    for name in names:
        p = release / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('# SYNTHETIC RELEASE; NEVER EXECUTED\n')
        pins.append(once.pin(p))
    base = write(tmp_path / 'base.json', dict(schema='frequency_physical_finite_dispatch.v1',
        dispatch_deadline_utc='2000-01-01T00:00:00Z', python=sys.executable,
        repo=str(tmp_path), out=str(tmp_path / 'original_physics')))
    release = dict(code_root=str(release), source_pins=pins)
    budget = write(tmp_path / 'budget.json', dict(schema='frequency_physical_operational_budget.v1',
        base_config=base, release=release, original_dispatch_deadline_utc='2000-01-01T00:00:00Z',
        new_dispatch_deadline_utc='2099-01-01T00:00:00Z'))
    value = dict(schema='frequency_physical_resume_once.v1', original_process=dict(pid=123, uid=456, start_ticks=789),
        base_config=base, operational_budget=budget, release=release, python=sys.executable, cwd=str(tmp_path),
        out=str(tmp_path / 'new_wait'), wait_deadline_utc='2030-01-01T01:00:00Z', poll_seconds=60,
        waiter_source=once.pin(once.__file__))
    p = tmp_path / 'wait.json'
    write(p, value)
    monkeypatch.setattr(once, 'now', lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(once.subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected process creation'))
    monkeypatch.setattr(once.time, 'sleep', lambda n: pytest.fail('unexpected wait'))
    return p, value


def present(start=789, uid=456, state='S', pid=123):
    return dict(status='PRESENT', pid=pid, uid=uid, start_ticks=start, state=state)


def exited(pid):
    return dict(status='ABSENT', pid=pid)


def child_stub(config, monkeypatch, result=None, code=0, raw=None, child_identity=None):
    p, c = config
    out = Path(c['out'])
    calls = []
    result = result or {'status': 'FINITE_ROUNDROBIN_COMPLETE', 'N_requests': 320}
    def observe(pid):
        return (child_identity or present(start=4321, uid=456, pid=987)) if pid == 987 else exited(pid)
    monkeypatch.setattr(once, 'process_identity', observe)
    class Child:
        pid = 987
        done = False
        def wait(self):
            launch = once.read(out / 'CHILD_LAUNCH_RECEIPT.json')
            assert launch['pid'] == 987
            assert launch['status'] == 'EXISTING_DISPATCHER_STARTED_NOT_COMPLETION'
            self.done = True
            return code
        def poll(self):
            return code if self.done else None
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        kwargs['stdout'].write(raw if raw is not None else json.dumps(result).encode())
        kwargs['stderr'].write(b'SYNTHETIC stderr bytes\n')
        return Child()
    monkeypatch.setattr(once.subprocess, 'Popen', popen)
    return calls


def test_check_is_readonly_and_does_not_start_or_wait(config, monkeypatch):
    p, c = config
    monkeypatch.setattr(once, 'process_identity', lambda pid: present())
    before = set(p.parent.rglob('*'))
    result = once.check(p)
    assert result['status'] == 'CHECK_PASS_READ_ONLY' and not result['original_identity_exited']
    assert not result['process_started'] and not Path(c['out']).exists()
    assert set(p.parent.rglob('*')) == before


@pytest.mark.parametrize('mutation', [
    lambda c: c.update(model_id='NOT_ALLOWED'),
    lambda c: c.update(poll_seconds=61),
    lambda c: c.update(wait_deadline_utc='2030-01-01T01:00:00'),
    lambda c: c['original_process'].update(uid='456'),
    lambda c: c['release']['source_pins'].pop(),
])
def test_bad_configuration_rejected_without_output(config, mutation):
    p, c = config
    mutation(c)
    write(p, c)
    with pytest.raises(ValueError):
        once.config_at(p)
    assert not Path(c['out']).exists()


def test_proc_identity_parses_spaces_and_parentheses_in_comm(tmp_path):
    root = tmp_path / '123'
    root.mkdir()
    fields = ['S'] + ['0'] * 18 + ['789'] + ['0'] * 3
    (root / 'stat').write_text('123 (worker name (nested)) ' + ' '.join(fields))
    (root / 'status').write_text('Name:\tworker\nUid:\t456\t456\t456\t456\n')
    assert once.process_identity(123, tmp_path) == present()
    assert once.process_identity(124, tmp_path)['status'] == 'ABSENT'


def test_pid_reuse_is_exited_but_same_birth_wrong_uid_is_refused():
    original = dict(pid=123, uid=456, start_ticks=789)
    assert once.original_exited(present(start=999, uid=777), original)
    assert once.original_exited(present(state='Z'), original)
    assert not once.original_exited(dict(status='RACE_RECHECK', pid=123), original)
    with pytest.raises(ValueError, match='UID differs'):
        once.original_exited(present(uid=999), original)


def test_missing_proc_filesystem_is_not_treated_as_original_exit(tmp_path):
    with pytest.raises(ValueError, match='proc filesystem is unavailable'):
        once.process_identity(123, tmp_path / 'not_a_proc_mount')


def test_wait_is_bounded_then_one_dispatch_and_child_receipt_precedes_wait(config, monkeypatch):
    p, c = config
    calls = child_stub(config, monkeypatch)
    original_checks = [present(), present(), exited(123), exited(123)]
    sleeps = []
    def observe(pid):
        return present(pid=987, start=4321) if pid == 987 else original_checks.pop(0)
    monkeypatch.setattr(once, 'process_identity', observe)
    monkeypatch.setattr(once.time, 'sleep', lambda seconds: sleeps.append(seconds))
    result = once.run(p)
    assert len(calls) == 1 and sleeps == [60, 60]
    assert result['status'] == 'DISPATCH_COMPLETE'
    launch = once.read(Path(c['out']) / 'CHILD_LAUNCH_RECEIPT.json')
    assert launch['schema'] == 'frequency_physical_resume_child_launch.v1'
    assert (launch['pid'], launch['uid'], launch['start_ticks']) == (987, 456, 4321)
    assert launch['base_config'] == c['base_config'] and launch['operational_budget'] == c['operational_budget']
    assert calls[0][0] == once.command(c)
    assert calls[0][1]['env']['PYTHONPATH'] == c['release']['code_root'] + ':' + c['cwd']
    assert result['stdout'] == once.pin(Path(c['out']) / 'dispatcher.stdout')
    assert result['returncode'] == 0 and result['invocations'] == 1 and result['retries'] == 0


def test_atomic_existing_output_blocks_second_invocation(config, monkeypatch):
    p, c = config
    calls = child_stub(config, monkeypatch)
    once.run(p)
    original = once.pin(Path(c['out']) / 'TERMINAL.json')
    result = once.run(p)
    assert result['status'] == 'ALREADY_CREATED_NO_DUPLICATE' and len(calls) == 1
    assert once.pin(original['path']) == original


def test_wait_deadline_partial_never_launches_or_signals(config, monkeypatch):
    p, c = config
    c['wait_deadline_utc'] = '2030-01-01T00:00:00Z'
    write(p, c)
    monkeypatch.setattr(once, 'process_identity', lambda pid: present())
    result = once.run(p)
    assert result['status'] == 'WAIT_DEADLINE_PARTIAL' and not result['process_started']
    assert not (Path(c['out']) / 'DISPATCH_INTENT.json').exists()


@pytest.mark.parametrize('status,code,expected', [
    ('ALREADY_OWNED_NO_DUPLICATE', 0, 'DISPATCH_NOT_STARTED_ALREADY_OWNED'),
    ('PARTIAL_BUDGET_ENDED_NO_CHILD_STOPPED', 0, 'DISPATCH_PARTIAL'),
    ('FAIL_NO_RETRY', 1, 'DISPATCH_FAILED_NO_RETRY'),
    ('UNKNOWN_STATUS', 0, 'DISPATCH_RESULT_UNRECOGNIZED_NO_RETRY'),
])
def test_partial_failure_and_owned_results_never_claim_success_or_retry(config, monkeypatch, status, code, expected):
    p, c = config
    calls = child_stub(config, monkeypatch, result={'status': status}, code=code)
    result = once.run(p)
    assert result['status'] == expected and not result['physical_continuation_claim']
    assert len(calls) == 1 and result['returncode'] == code
    assert once.run(p)['status'] == 'ALREADY_CREATED_NO_DUPLICATE'
    assert len(calls) == 1


def test_child_birth_not_invented_if_it_exited_before_proc_observation(config, monkeypatch):
    p, c = config
    child_stub(config, monkeypatch, child_identity=exited(987))
    once.run(p)
    launch = once.read(Path(c['out']) / 'CHILD_LAUNCH_RECEIPT.json')
    assert launch['pid'] == 987 and launch['uid'] is None and launch['start_ticks'] is None
    assert launch['child_observation']['status'] == 'ABSENT'


def test_exact_non_json_stdout_retained_without_success_claim(config, monkeypatch):
    p, c = config
    raw = b'not JSON\n\x00SYNTHETIC\n'
    child_stub(config, monkeypatch, raw=raw)
    result = once.run(p)
    assert result['status'] == 'DISPATCH_RESULT_UNRECOGNIZED_NO_RETRY'
    assert (Path(c['out']) / 'dispatcher.stdout').read_bytes() == raw


def test_changed_pin_during_wait_fails_before_dispatch(config, monkeypatch):
    p, c = config
    monkeypatch.setattr(once, 'process_identity', lambda pid: present())
    def change_source(seconds):
        target = Path(c['release']['source_pins'][0]['path'])
        target.write_text('# changed SYNTHETIC source\n')
    monkeypatch.setattr(once.time, 'sleep', change_source)
    result = once.run(p)
    assert result['status'] == 'WAIT_OR_DISPATCH_FAILED_NO_RETRY'
    assert 'pin differs' in result['error'] and result['child_pid'] is None
    assert not (Path(c['out']) / 'DISPATCH_INTENT.json').exists()


def test_same_birth_wrong_uid_fails_closed_without_launch(config, monkeypatch):
    p, c = config
    monkeypatch.setattr(once, 'process_identity', lambda pid: present(uid=999))
    result = once.run(p)
    assert result['status'] == 'WAIT_OR_DISPATCH_FAILED_NO_RETRY'
    assert 'UID differs' in result['error'] and result['child_pid'] is None


def test_admitted_child_has_no_wait_timeout_or_kill(config, monkeypatch):
    p, c = config
    calls = child_stub(config, monkeypatch)
    original = once.now
    def popen(argv, **kwargs):
        # Admission already occurred; advancing beyond wait deadline must not
        # stop, time out or retry the existing child.
        monkeypatch.setattr(once, 'now', lambda: original() + timedelta(days=1))
        return captured(argv, **kwargs)
    captured = once.subprocess.Popen
    monkeypatch.setattr(once.subprocess, 'Popen', popen)
    result = once.run(p)
    assert result['status'] == 'DISPATCH_COMPLETE' and len(calls) == 1


def test_spawn_failure_retains_intent_and_refuses_second_attempt(config, monkeypatch):
    p, c = config
    monkeypatch.setattr(once, 'process_identity', exited)
    def fail(*args, **kwargs):
        raise OSError('SYNTHETIC exec failure')
    monkeypatch.setattr(once.subprocess, 'Popen', fail)
    result = once.run(p)
    assert result['status'] == 'WAIT_OR_DISPATCH_FAILED_NO_RETRY'
    assert (Path(c['out']) / 'DISPATCH_INTENT.json').is_file()
    assert (Path(c['out']) / 'FAILURE.json').is_file()
    assert once.run(p)['status'] == 'ALREADY_CREATED_NO_DUPLICATE'


def test_launch_receipt_write_failure_retains_failure_and_waits_admitted_child(config, monkeypatch):
    p, c = config
    out = Path(c['out'])
    monkeypatch.setattr(once, 'process_identity', lambda pid: exited(pid) if pid == 123 else present(pid=987))
    original_write = once.write_once
    def fail_launch_receipt(path, value):
        if path.name == 'CHILD_LAUNCH_RECEIPT.json':
            raise OSError('SYNTHETIC launch receipt write failure')
        return original_write(path, value)
    monkeypatch.setattr(once, 'write_once', fail_launch_receipt)
    events = []
    class Child:
        pid = 987
        def poll(self):
            return None
        def wait(self):
            assert once.read(out / 'FAILURE.json')['error'] == 'OSError: SYNTHETIC launch receipt write failure'
            events.append('unbounded_wait')
            return 7
    def popen(*args, **kwargs):
        events.append('spawn')
        kwargs['stdout'].write(b'SYNTHETIC preserved stdout\n')
        return Child()
    monkeypatch.setattr(once.subprocess, 'Popen', popen)
    result = once.run(p)
    assert events == ['spawn', 'unbounded_wait']
    assert result['status'] == 'WAIT_OR_DISPATCH_FAILED_NO_RETRY'
    assert result['child_returncode'] == 7 and not result['child_may_be_running']
    assert not result['physical_continuation_claim']
    assert not (out / 'TERMINAL.json').exists()
    assert once.read(out / 'CHILD_EXIT_AFTER_FAILURE.json')['returncode'] == 7
    assert (out / 'dispatcher.stdout').read_bytes() == b'SYNTHETIC preserved stdout\n'
    assert once.run(p)['status'] == 'ALREADY_CREATED_NO_DUPLICATE'
    assert events == ['spawn', 'unbounded_wait']
