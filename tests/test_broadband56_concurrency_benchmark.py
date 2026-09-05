"""Control-only fixtures: these tests do not execute or simulate EMX results."""

import json
import threading
import time

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_concurrency_benchmark import (
    _record, run_trial, screening_summary, write_screen_csv,
)


def setup(tmp_path):
    workload = tmp_path / "FIXTURE_NOT_EMX_WORKLOAD.json"
    jobs = [dict(job_id=f"fixture_{i}") for i in range(8)]
    workload.write_text(json.dumps(dict(jobs=jobs)))
    gate = tmp_path / "FIXTURE_NOT_REAL_RESOURCE.json"
    gate.write_text('{}')
    return dict(jobs=jobs, concurrency=4,
                out_dir=tmp_path / 'trial', workload_identity=_record(workload),
                authority_check=lambda: None,
                admission_gate=lambda _: {'pass': True, 'evidence': _record(gate)},
                execute=lambda job, path: dict(overall_status='PASS', evidence_class='UNIT_TEST_FIXTURE'),
                telemetry=lambda: dict(active_solver_processes=0), sample_interval_seconds=0.01)


def test_bounded_inflight_and_complete_accounting(tmp_path):
    args = setup(tmp_path)
    lock = threading.Lock()
    active = peak = 0

    def fixture(job, path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return dict(overall_status='PASS', evidence_class='UNIT_TEST_FIXTURE')

    args['execute'] = fixture
    result = run_trial(**args)
    assert 1 <= peak <= 4
    assert result['pass_jobs'] == result['completed_jobs'] == 8
    assert result['pending_jobs_after_return'] == result['production_accepted_increment'] == 0
    assert result['requested_solver_concurrency_observed'] is False
    assert result['observed_peak_solver_processes'] == 0
    assert len(list(args['out_dir'].glob('JOB_RESULT_*.json'))) == 8
    assert screening_summary([result], levels=[4])['screening_leader'] is None


def test_initial_wait_runs_nothing(tmp_path):
    args = setup(tmp_path)
    prior = args['admission_gate']
    args['admission_gate'] = lambda n: {**prior(n), 'pass': False}
    args['execute'] = lambda *_: pytest.fail('a WAIT trial must never execute')
    result = run_trial(**args)
    assert result['completed_jobs'] == 0 and result['not_submitted_jobs'] == 8
    assert result['stop_reason'] == 'INITIAL_RESOURCE_WAIT'


def test_gate_change_drains_without_refill(tmp_path):
    args = setup(tmp_path)
    gate = args['admission_gate']
    checks = 0

    def change(n):
        nonlocal checks
        checks += 1
        return {**gate(n), 'pass': checks <= 2}

    def fixture(*_):
        time.sleep(0.05)
        return dict(overall_status='PASS', evidence_class='UNIT_TEST_FIXTURE')

    args.update(admission_gate=change, execute=fixture)
    result = run_trial(**args)
    assert result['completed_jobs'] == 4 and result['not_submitted_jobs'] == 4
    assert result['pending_jobs_after_return'] == 0
    assert result['stop_reason'] == 'RESOURCE_GATE_CHANGED_DRAIN_ONLY'


def test_delegate_failure_is_counted(tmp_path):
    args = setup(tmp_path)

    def fixture(*_):
        raise RuntimeError('fixture failure, not a real EMX failure')

    args['execute'] = fixture
    result = run_trial(**args)
    assert result['fail_jobs'] == 8 and result['pass_jobs'] == 0
    assert result['validated_outputs_per_wall_hour'] == 0


def test_no_clobber_and_no_execution_before_identity_check(tmp_path):
    args = setup(tmp_path)
    args['workload_identity']['sha256'] = '0'*64
    args['execute'] = lambda *_: pytest.fail('bad identity')
    with pytest.raises(ValueError, match='workload'):
        run_trial(**args)
    assert not args['out_dir'].exists()
    args = setup(tmp_path)
    args['out_dir'].mkdir()
    with pytest.raises(FileExistsError):
        run_trial(**args)


def test_authority_failure_stops_before_any_output(tmp_path):
    args = setup(tmp_path)

    def reject():
        raise RuntimeError('not the lease-owning supervisor')

    args['authority_check'] = reject
    with pytest.raises(RuntimeError, match='lease'):
        run_trial(**args)
    assert not args['out_dir'].exists()


def test_substituted_workload_is_rejected(tmp_path):
    args = setup(tmp_path)
    args['jobs'][0]['job_id'] = 'different_job'
    with pytest.raises(ValueError, match='hash-bound workload'):
        run_trial(**args)
    assert not args['out_dir'].exists()


def test_bad_initial_gate_preserves_failure(tmp_path):
    args = setup(tmp_path)
    args['admission_gate'] = lambda _: {'pass': True}
    with pytest.raises(ValueError, match='no evidence'):
        run_trial(**args)
    failure = json.loads((args['out_dir'] / 'PREFLIGHT_FAILURE.json').read_text())
    assert failure['simulator_action_taken'] is False


@pytest.mark.parametrize('change', ['duplicate', 'too_few', 'invalid_limit'])
def test_invalid_workload(tmp_path, change):
    args = setup(tmp_path)
    if change == 'duplicate':
        args['jobs'][1] = args['jobs'][0]
    elif change == 'too_few':
        args['jobs'] = args['jobs'][:1]
    else:
        args['concurrency'] = 3
    with pytest.raises(ValueError):
        run_trial(**args)


def test_incomplete_screen_never_produces_winner(tmp_path):
    result = run_trial(**setup(tmp_path))
    assert screening_summary([result])['screening_leader'] is None
    with pytest.raises(ValueError, match='one screening row'):
        screening_summary([result, result])
    write_screen_csv(tmp_path / 'control_fixture.csv', [result])
    assert 'validated_outputs_per_wall_hour' in (tmp_path / 'control_fixture.csv').read_text()
    assert json.loads((tmp_path / 'trial' / 'TRIAL_RECEIPT.json').read_text())['production_accepted_increment'] == 0
