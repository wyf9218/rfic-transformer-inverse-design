"""Synthetic admission/identity checks only: no MARS, simulator or model calls."""
import copy
import fcntl
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_physical_dispatch as dispatch
from research.broadband56_nn import frequency_research_emx as emx


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return emx.pin(path)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # These are deliberately synthetic past/future dates, not a research budget.
    source_root = Path(__file__).resolve().parents[1]
    current = tmp_path / 'new_release'
    names = ['research/__init__.py', 'research/broadband56_nn/__init__.py',
             *['research/broadband56_nn/' + n for n in ('io.py',
               'frequency_physical_dispatch.py', 'frequency_research_emx.py',
               'frequency_research_gds_audit.py', 'frequency_research_calibre.py')]]
    old = tmp_path / 'original_release'
    originals = []
    for name in names:
        target = old / name
        target.parent.mkdir(parents=True, exist_ok=True)
        original_source = source_root / name
        body = (original_source.read_bytes() if original_source.is_file()
                else b'# SYNTHETIC namespace initializer; no scientific code\n')
        new_target = current / name
        new_target.parent.mkdir(parents=True, exist_ok=True)
        new_target.write_bytes(body)
        if Path(name).name in ('frequency_physical_dispatch.py', 'frequency_research_emx.py'):
            body = b'# SYNTHETIC ORIGINAL IDENTITY; NEVER EXECUTED\n' + body
        target.write_bytes(body)
        originals.append(emx.pin(target))
    records = write(tmp_path / 'records.json', {'synthetic': True})
    jobs = [dict(request_id=f'synthetic-{i}', frequency_ghz=5, model_id='synthetic-model',
                 dataset_scope='SYNTHETIC_NOT_RESEARCH', q_proxy=10,
                 candidate_records=records, existing_root=str(tmp_path / 'first15')) for i in range(320)]
    config = dict(schema='frequency_physical_finite_dispatch.v1', code_root=str(old),
                  source_pins=originals, out=str(tmp_path / 'original_out'),
                  repo=str(tmp_path), python=sys.executable,
                  dispatch_deadline_utc='2000-01-01T00:00:00Z',
                  global_lock_path=str(tmp_path / 'original_global.lock'),
                  resource_budget=dict(cpu_per_solver=2, max_global_solvers=4),
                  emx_runtime={'synthetic': True}, configuration=write(tmp_path / 'private.json', {}),
                  max_global_solvers=4, dispatch_manifest=write(tmp_path / 'plan.json', {'jobs': jobs}))
    base = write(tmp_path / 'base.json', config)
    overlay = dict(schema='frequency_physical_operational_budget.v1', base_config=base,
                   original_dispatch_deadline_utc=config['dispatch_deadline_utc'],
                   new_dispatch_deadline_utc='2099-01-01T00:00:00Z',
                   release=dict(code_root=str(current), source_pins=[emx.pin(current / n) for n in names]))
    overlay_path = tmp_path / 'overlay.json'
    write(overlay_path, overlay)
    # Simulate execution from the pinned isolated release, without subprocesses.
    monkeypatch.setattr(dispatch, '__file__', str(current / 'research/broadband56_nn/frequency_physical_dispatch.py'))
    monkeypatch.setattr(emx, '__file__', str(current / 'research/broadband56_nn/frequency_research_emx.py'))
    # Full320 physics transport validation is outside these new unit tests.
    monkeypatch.setattr(dispatch, 'validate', lambda c: ({'jobs': jobs}, {}))
    monkeypatch.setattr(dispatch.subprocess, 'Popen', lambda *a, **k: pytest.fail('native launch forbidden'))
    monkeypatch.setattr(dispatch.subprocess, 'run', lambda *a, **k: pytest.fail('extraction launch forbidden'))
    result = SimpleNamespace(tmp=tmp_path, current=current, old=old, config=config,
                             base=base, overlay=overlay, path=overlay_path, jobs=jobs, held=[])
    yield result
    for lock in result.held:
        lock.close()


def runner(setup, overlay=True):
    return dispatch.Dispatcher(setup.base['path'], setup.path if overlay else None)


def use_receipt(setup, value):
    p = value.out / 'operational_budget_uses/USE_synthetic.json'
    value.operational_use = write(p, dict(status='ADMITTED_UNDER_ORIGINAL_QUEUE_AND_GLOBAL_LEASES',
        budget=emx.pin(setup.path), base_config=setup.base,
        original_deadline_utc=setup.overlay['original_dispatch_deadline_utc'],
        new_deadline_utc=setup.overlay['new_dispatch_deadline_utc'], release=setup.overlay['release']))
    lock = (value.out / 'queue.lock').open('w')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    setup.held.append(lock)
    return value.operational_use


def natural_partial(setup):
    return write(Path(setup.config['out']) / 'PARTIAL_synthetic.json',
                 dict(status='PARTIAL_BUDGET_ENDED_NO_CHILD_STOPPED', config=setup.base))


def test_no_overlay_keeps_original_deadline_and_removes_ambient_budget(setup, monkeypatch):
    monkeypatch.setenv(emx.OPERATIONAL_BUDGET_ENV, str(setup.path))
    monkeypatch.setenv(emx.OPERATIONAL_USE_ENV, 'untrusted ambient value')
    value = runner(setup, overlay=False)
    assert value.operational is None
    assert emx.OPERATIONAL_BUDGET_ENV not in value.environment
    assert emx.OPERATIONAL_USE_ENV not in value.environment
    with pytest.raises(dispatch.BudgetEnded):
        value.budget()
    assert emx.pin(setup.base['path']) == setup.base


def test_valid_overlay_keeps_exact_base_config_and_extends_only_admission(setup):
    value = runner(setup)
    value.budget()
    assert value.config == setup.config and value.config_pin == setup.base
    assert value.environment['PYTHONPATH'] == str(setup.old) + os.pathsep + setup.config['repo']
    assert emx.pin(setup.base['path']) == setup.base


@pytest.mark.parametrize('mutation,match', [
    (lambda v: v.update(model_id='changed'), 'Unexpected operational budget fields'),
    (lambda v: v.update(max_global_solvers=48), 'Unexpected operational budget fields'),
    (lambda v: v.update(original_dispatch_deadline_utc='2001-01-01T00:00:00Z'), 'Original deadline differs'),
    (lambda v: v.update(new_dispatch_deadline_utc='2000-01-01T00:00:00Z'), 'strictly later'),
    (lambda v: v.update(new_dispatch_deadline_utc='1999-01-01T00:00:00Z'), 'strictly later'),
    (lambda v: v.update(new_dispatch_deadline_utc='2099-01-01T00:00:00'), 'strictly later'),
    (lambda v: v['release'].update(dataset='changed'), 'Unexpected operational release fields'),
    (lambda v: v['release']['source_pins'].pop(), 'Incomplete or extra'),
])
def test_wrong_deadline_or_extra_scientific_fields_rejected(setup, mutation, match):
    value = copy.deepcopy(setup.overlay)
    mutation(value)
    write(setup.path, value)
    with pytest.raises(emx.ResearchEmxError, match=match):
        runner(setup)


def test_wrong_base_config_rejected_even_if_it_is_validly_pinned(setup):
    wrong = write(setup.tmp / 'different_base.json', setup.config)
    value = dict(setup.overlay, base_config=wrong)
    write(setup.path, value)
    with pytest.raises(emx.ResearchEmxError, match='base config differs'):
        runner(setup)


def test_release_must_be_new_and_executing_source_pinned(setup):
    value = copy.deepcopy(setup.overlay)
    value['release']['code_root'] = str(setup.old)
    write(setup.path, value)
    with pytest.raises(emx.ResearchEmxError, match='separate release'):
        runner(setup)
    write(setup.path, setup.overlay)
    unrelated = setup.tmp / 'unrelated.py'
    unrelated.write_text('# not release code')
    with pytest.raises(emx.ResearchEmxError, match='not release-pinned'):
        emx.operational_budget(setup.path, executing_source=unrelated)


def test_overlay_pin_change_after_initialization_rejected(setup):
    value = runner(setup)
    write(setup.path, dict(setup.overlay, new_dispatch_deadline_utc='2098-01-01T00:00:00Z'))
    with pytest.raises(emx.ResearchEmxError, match='Immutable input mismatch'):
        value.budget()


@pytest.mark.parametrize('overlay', [False, True])
def test_completed_request_exactly_reused_without_native_calls(setup, overlay):
    value = runner(setup, overlay=overlay)
    job = setup.jobs[0]
    p = value.out / 'requests' / job['request_id'] / 'REQUEST_RECEIPT.json'
    expected = dict(frozen_job=job, config=setup.base, artifacts=[])
    before = write(p, expected)
    assert value.execute_request(job) == expected
    assert emx.pin(p) == before


@pytest.mark.parametrize('overlay', [False, True])
def test_complete_queue_receipt_reused_unchanged(setup, overlay):
    value = runner(setup, overlay=overlay)
    expected = dict(status='FINITE_ROUNDROBIN_COMPLETE', config=setup.base, N_requests=320)
    p = value.out / 'TERMINAL.json'
    before = write(p, expected)
    assert value.run() == expected and emx.pin(p) == before
    assert not (value.out / 'operational_budget_uses').exists()


def test_no_natural_partial_and_prior_failure_remain_hard_refusals(setup):
    value = runner(setup)
    with pytest.raises(emx.ResearchEmxError, match='natural budget-exit'):
        value.run()
    failures = list(value.out.glob('FAILURE_*.json'))
    assert len(failures) == 1
    preserved = emx.pin(failures[0])
    natural_partial(setup)
    with pytest.raises(emx.ResearchEmxError, match='Previous failure preserved'):
        value.run()
    assert emx.pin(failures[0]) == preserved


def test_existing_queue_lease_is_not_bypassed(setup):
    value = runner(setup)
    value.out.mkdir()
    with (value.out / 'queue.lock').open('w') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert value.run()['status'] == 'ALREADY_OWNED_NO_DUPLICATE'
    assert not (value.out / 'operational_budget_uses').exists()


def prepare_admission(setup, value):
    natural_partial(setup)
    write(Path(setup.jobs[0]['existing_root']) / 'remaining_q11to18_queue_v1/TERMINAL.json', {})
    value.first15_reuse = lambda job: {'SYNTHETIC': True}
    value.admit = lambda: {'SYNTHETIC': True}


def test_existing_global_lease_is_not_bypassed(setup):
    value = runner(setup)
    prepare_admission(setup, value)
    with open(setup.config['global_lock_path'], 'w') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert value.run()['status'] == 'ALREADY_OWNED_NO_DUPLICATE'
    assert not (value.out / 'operational_budget_uses').exists()


def test_independent_use_receipt_only_after_both_leases(setup):
    value = runner(setup)
    prepare_admission(setup, value)
    old_partial = emx.pin(value.out / 'PARTIAL_synthetic.json')
    def finish_without_native(job):
        raise dispatch.BudgetEnded('SYNTHETIC next admission budget end')
    value.execute_request = finish_without_native
    result = value.run()
    uses = list((value.out / 'operational_budget_uses').glob('USE_*.json'))
    assert len(uses) == 1
    use = emx.read_json(uses[0])
    assert use['status'] == 'ADMITTED_UNDER_ORIGINAL_QUEUE_AND_GLOBAL_LEASES'
    assert use['base_config'] == setup.base and use['budget'] == emx.pin(setup.path)
    assert result['operational_budget_use'] == emx.pin(uses[0])
    assert emx.pin(old_partial['path']) == old_partial
    assert emx.pin(setup.base['path']) == setup.base


def process_context(setup, value, stage='emx_q10'):
    root = value.out / 'requests' / 'synthetic-0'
    root.mkdir(parents=True, exist_ok=True)
    output = root / stage
    completion = output / 'features/FEATURE_RECEIPT.json'
    command = [sys.executable, '-B', '-m', 'research.broadband56_nn.frequency_research_emx',
               'run', '--request', str(root / 'EMX_Q10_REQUEST.json'), '--output', str(output),
               '--inherited-global-lease-fd', '101']
    semantic = list(command)
    semantic[-1] = '<inherited-global-lease-fd>'
    intent = dict(config=setup.base, command=semantic, output=str(output), completion=str(completion))
    value.fd, value.queue_fd = 101, 102
    value.admit = lambda: {'SYNTHETIC': True}
    return root, output, completion, command, intent


def test_old_completed_stage_receipt_reused_without_changing_intent(setup):
    value = runner(setup)
    root, output, completion, command, intent = process_context(setup, value)
    terminal = write(completion, {'SYNTHETIC': True})
    original = write(root / 'emx_q10_PROCESS.json', dict(intent=intent, returncode=0, completion=terminal))
    assert value.process(root, 'emx_q10', command, output, completion)['intent'] == intent
    assert emx.pin(original['path']) == original


def test_only_new_emx_launch_uses_new_release_and_independent_budget(setup, monkeypatch):
    value = runner(setup)
    use = use_receipt(setup, value)
    root, output, completion, command, intent = process_context(setup, value)
    observed = []
    def fake_launch(argv, **kwargs):
        observed.append(kwargs['env'])
        write(completion, {'SYNTHETIC': True})
        return SimpleNamespace(pid=999999, wait=lambda: 0)
    monkeypatch.setattr(dispatch.subprocess, 'Popen', fake_launch)
    result = value.process(root, 'emx_q10', command, output, completion)
    assert result['intent'] == intent and result['operational_budget_use'] == use
    assert observed[0]['PYTHONPATH'].split(os.pathsep)[0] == str(setup.current)
    assert json.loads(observed[0][emx.OPERATIONAL_USE_ENV]) == use
    assert value.environment['PYTHONPATH'].split(os.pathsep)[0] == str(setup.old)


@pytest.mark.parametrize('old_wrapper', [True, False])
def test_extraction_recovery_uses_exact_proven_wrapper_release(setup, monkeypatch, old_wrapper):
    value = runner(setup)
    root, output, completion, command, intent = process_context(setup, value)
    expected_root = setup.old if old_wrapper else setup.current
    wrapper = emx.pin(expected_root / 'research/broadband56_nn/frequency_research_emx.py')
    write(output / 'PREFLIGHT.json', {'source_pins': [wrapper]})
    write(output / 'SOLVER_RECEIPT.json', {'SYNTHETIC_EXISTING_SOLVER': True})
    write(root / 'emx_q10_INTENT.json', intent)
    (root / 'emx_q10.log').write_text('SYNTHETIC previous child')
    def extraction_only(argv, **kwargs):
        assert 'extract' in argv and 'run' not in argv
        assert kwargs['env']['PYTHONPATH'].split(os.pathsep)[0] == str(expected_root)
        assert emx.OPERATIONAL_BUDGET_ENV not in kwargs['env']
        assert emx.OPERATIONAL_USE_ENV not in kwargs['env']
        write(completion, {'SYNTHETIC': True})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(dispatch.subprocess, 'run', extraction_only)
    result = value.process(root, 'emx_q10', command, output, completion)
    assert result['recovery']['status'] == 'EXISTING_SOLVER_REUSED_EXTRACTION_ONLY'
    assert emx.pin(wrapper['path']) == wrapper


def test_partial_native_output_still_refuses_without_rerun(setup):
    value = runner(setup)
    root, output, completion, command, intent = process_context(setup, value)
    output.mkdir()
    write(root / 'emx_q10_INTENT.json', intent)
    with pytest.raises(emx.ResearchEmxError, match='Partial native output'):
        value.process(root, 'emx_q10', command, output, completion)


def test_failed_stage_receipt_is_preserved_and_never_retried(setup):
    value = runner(setup)
    root, output, completion, command, intent = process_context(setup, value)
    previous = write(root / 'emx_q10_PROCESS.json', dict(intent=intent, returncode=2, completion=None))
    with pytest.raises(emx.ResearchEmxError, match='Previous process failed'):
        value.process(root, 'emx_q10', command, output, completion)
    assert emx.pin(previous['path']) == previous


def wrapper_request(setup):
    job = setup.jobs[0]
    request = {k: job[k] for k in ('request_id', 'frequency_ghz', 'model_id', 'dataset_scope', 'q_proxy')}
    request.update(records=job['candidate_records'], q_requested=10,
                   dispatch_deadline_utc=setup.config['dispatch_deadline_utc'],
                   global_lock_path=setup.config['global_lock_path'], resource_budget=setup.config['resource_budget'],
                   private_config=setup.config['configuration'], runtime=setup.config['emx_runtime'])
    output = Path(setup.config['out']) / 'requests/synthetic-0/emx_q10'
    return request, output


def test_wrapper_uses_same_overlay_without_mutating_request(setup, monkeypatch):
    value = runner(setup)
    use = use_receipt(setup, value)
    monkeypatch.setenv(emx.OPERATIONAL_BUDGET_ENV, str(setup.path))
    monkeypatch.setenv(emx.OPERATIONAL_USE_ENV, json.dumps(use))
    request, output = wrapper_request(setup)
    before = copy.deepcopy(request)
    assert emx.operational_deadline(request, output) == setup.overlay['new_dispatch_deadline_utc']
    assert request == before
    request['model_id'] = 'changed'
    with pytest.raises(emx.ResearchEmxError, match='request identity differs'):
        emx.operational_deadline(request, output)


def test_wrapper_rejects_extra_resource_scope_and_foreign_output(setup, monkeypatch):
    value = runner(setup)
    use = use_receipt(setup, value)
    monkeypatch.setenv(emx.OPERATIONAL_BUDGET_ENV, str(setup.path))
    monkeypatch.setenv(emx.OPERATIONAL_USE_ENV, json.dumps(use))
    request, output = wrapper_request(setup)
    with pytest.raises(emx.ResearchEmxError, match='original request directory'):
        emx.operational_deadline(request, output.parent / 'duplicate')
    request['resource_budget'] = dict(request['resource_budget'], max_global_solvers=48)
    with pytest.raises(emx.ResearchEmxError, match='cannot change scientific'):
        emx.operational_deadline(request, output)


def test_wrapper_without_overlay_retains_old_deadline(setup, monkeypatch):
    monkeypatch.delenv(emx.OPERATIONAL_BUDGET_ENV, raising=False)
    monkeypatch.delenv(emx.OPERATIONAL_USE_ENV, raising=False)
    request, output = wrapper_request(setup)
    assert emx.operational_deadline(request, output) == setup.config['dispatch_deadline_utc']
    monkeypatch.setenv(emx.OPERATIONAL_BUDGET_ENV, str(setup.path))
    with pytest.raises(emx.ResearchEmxError, match='supplied together'):
        emx.operational_deadline(request, output)


def test_saved_use_cannot_bypass_a_free_original_queue_lock(setup, monkeypatch):
    value = runner(setup)
    use = use_receipt(setup, value)
    setup.held[-1].close()
    monkeypatch.setenv(emx.OPERATIONAL_BUDGET_ENV, str(setup.path))
    monkeypatch.setenv(emx.OPERATIONAL_USE_ENV, json.dumps(use))
    request, output = wrapper_request(setup)
    with pytest.raises(emx.ResearchEmxError, match='held original queue lease'):
        emx.operational_deadline(request, output)
