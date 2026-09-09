"""Synthetic terminal-state compatibility tests; no real physical evidence."""
import copy
import hashlib

import pytest

from research.broadband56_nn.eucap15_selected_metrics import STATES, summarize


SPANS = [2.5, 2.5, 20., .8]
TOLERANCES = [.125, .125, 1., .04000000000000001]
NEW_FAILURES = ('GDS_FAIL', 'DRC_FAIL', 'SOLVER_FAIL')


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def row(index, state='STRICT_VALID'):
    valid = state == 'STRICT_VALID'
    return dict(request_id=f'SYNTHETIC-{index:02d}', candidate_id=f'SYNTHETIC-C-{index:02d}',
        candidate_geometry_identity_sha256=digest(f'geometry-{index}'), q_proxy=12,
        target=[1., 1., 12., .5], grid_proxy=[1.01, .99, 12.2, .49], state=state,
        actual=[1.05, 1.1, 12.4, .52] if valid else None,
        strict_joint_hit=True if valid else None,
        touchstone_sha=digest(f'synthetic-touchstone-{index}') if valid else None)


def frame():
    return [row(i) for i in range(58)] + [row(i, 'GDS_FAIL') for i in range(58, 62)] + [
        row(i, 'ANALYTIC_FAIL') for i in range(62, 64)]


def run(rows):
    return summarize(rows, score_spans=SPANS, tolerances=TOLERANCES)


def test_full64_synthetic_accounting_preserves_original_denominator():
    rows = frame()
    original = copy.deepcopy(rows)
    result = run(rows)
    summary = result['summary']
    assert rows == original
    assert summary['N_original_requests'] == summary['N_selected_candidate_rows'] == 64
    assert summary['N_terminal_requests'] == 64
    assert summary['N_pending_requests'] == 0
    assert summary['N_strict_valid'] == summary['N_joint_hit'] == 58
    assert summary['N_gds_fail'] == 4 and summary['N_analytic_fail'] == 2
    assert summary['N_drc_fail'] == summary['N_solver_fail'] == summary['N_emx_invalid'] == 0
    assert summary['completion_status'] == 'COMPLETE_ACCOUNTING'
    assert summary['strict_valid_fraction_original'] == 58/64
    assert summary['completed_joint_hit_fraction_original'] == 58/64
    assert summary['conditional_valid_joint_hit_fraction'] == 1
    assert sum(summary['state_counts'].values()) == 64
    assert set(summary['state_counts']) == set(STATES)
    assert summary['scope'] == 'DESCRIPTIVE_ALREADY_VERIFIED_SELECTED_ROWS_NOT_PHYSICAL_CHAIN_VALIDATION'
    assert summary['q_emx'] is None and summary['complete11_status'] == 'NOT_EVALUATED'
    assert summary['native_attempts'] is summary['independent_native_solves'] is None
    assert summary['cache_hits'] is summary['cache_hit_rate'] is None
    assert summary['ci_status'] == 'NOT_ESTIMATED'


def test_strict_error_and_ecdf_not_conditioned_on_new_failure_type():
    rows = frame()
    with_gds = run(rows)
    for state in ('ANALYTIC_FAIL', 'DRC_FAIL', 'SOLVER_FAIL', 'PENDING'):
        alternate = copy.deepcopy(rows)
        for item in alternate[58:62]: item['state'] = state
        result = run(alternate)
        assert result['metric_rows'] == with_gds['metric_rows']
        assert result['ecdf_rows'] == with_gds['ecdf_rows']
    assert len(with_gds['metric_rows']) == 8
    assert len(with_gds['ecdf_rows']) == 58*4*2
    expected = [abs(a-b) for a, b in zip(rows[0]['actual'], rows[0]['target'])]
    for metric, error, span in zip(with_gds['metric_rows'][:4], expected, SPANS):
        assert metric['N_original'] == 64 and metric['n'] == metric['n_request_groups'] == 58
        assert metric['mae'] == pytest.approx(error)
        assert metric['abs_error_p95'] == pytest.approx(error)
        assert metric['normalized_mae'] == pytest.approx(error/span)
        assert metric['normalization'] == 'Absolute fixed span; dimensionless, not target-relative percentage'
    lp_cdf = [item for item in with_gds['ecdf_rows'] if item['comparison'] == 'emx_minus_target'
              and item['feature'] == 'Lp_nH']
    assert [item['rank'] for item in lp_cdf] == list(range(1, 59))
    assert [item['cdf'] for item in lp_cdf] == [rank/58 for rank in range(1, 59)]
    assert all(item['N_original'] == 64 and item['n'] == 58 for item in lp_cdf)


def test_mixed_terminal_failures_pending_and_emx_invalid_do_not_fake_completion():
    rows = frame()
    for item, state in zip(rows[58:62], ('GDS_FAIL', 'DRC_FAIL', 'SOLVER_FAIL', 'PENDING')):
        item['state'] = state
    rows[57].update(state='EMX_INVALID', actual=[1., None, 12., .5], strict_joint_hit=False)
    summary = run(rows)['summary']
    assert summary['N_original_requests'] == 64
    assert summary['N_terminal_requests'] == 63 and summary['N_pending_requests'] == 1
    assert summary['N_gds_fail'] == summary['N_drc_fail'] == summary['N_solver_fail'] == 1
    assert summary['N_emx_invalid'] == 1 and summary['N_analytic_fail'] == 2
    assert summary['N_strict_valid'] == summary['N_joint_hit'] == 57
    assert summary['observed_joint_hit_fraction_original'] == 57/64
    assert summary['completed_joint_hit_fraction_original'] is None
    assert summary['completion_status'] == 'PARTIAL_PENDING'


@pytest.mark.parametrize('state', NEW_FAILURES)
@pytest.mark.parametrize('key,value', [
    ('actual', [1., 1., 12., .5]), ('actual', [None]*4),
    ('strict_joint_hit', True), ('strict_joint_hit', False),
    ('touchstone_sha', 'a'*64),
])
def test_new_pre_em_failures_require_actual_touchstone_and_hit_all_null(state, key, value):
    item = row(1, state)
    item[key] = value
    with pytest.raises(ValueError): run([item])


@pytest.mark.parametrize('state', NEW_FAILURES)
def test_each_failure_is_terminal_but_never_an_error_observation(state):
    result = run([row(i, state) for i in range(64)])
    assert result['summary']['N_terminal_requests'] == 64
    assert result['summary']['N_strict_valid'] == 0
    assert result['summary']['completed_joint_hit_fraction_original'] == 0
    assert result['summary']['conditional_valid_joint_hit_fraction'] is None
    assert result['ecdf_rows'] == []
    assert all(metric['N_original'] == 64 and metric['n'] == 0 and metric['mae'] is None
               and metric['abs_error_p95'] is None for metric in result['metric_rows'])


def test_strict_nonhit_uses_same_absolute_tolerance_and_original_denominator():
    rows = frame()
    rows[57]['actual'][0] = 1.2
    rows[57]['strict_joint_hit'] = False
    result = run(rows)
    assert result['summary']['N_joint_hit'] == 57
    assert result['summary']['N_strict_not_hit'] == 1
    assert result['summary']['completed_joint_hit_fraction_original'] == 57/64
    assert result['summary']['conditional_valid_joint_hit_fraction'] == 57/58
    assert result['metric_rows'][0]['mae'] == pytest.approx((57*.05+.2)/58)


def test_unknown_failure_and_duplicate_identity_remain_rejected():
    with pytest.raises(ValueError): run([row(1, 'UNKNOWN_FAIL')])
    with pytest.raises(ValueError): run([row(1, 'GDS_FAIL'), row(1, 'DRC_FAIL')])
    rows = frame()
    rows[0]['actual'] = None
    with pytest.raises(ValueError): run(rows)


def test_empty_frame_keeps_null_metrics_and_explicit_zero_failure_counts():
    result = run([])
    assert result['summary']['completion_status'] == 'EMPTY_FRAME'
    assert result['summary']['N_original_requests'] == 0
    assert result['summary']['N_gds_fail'] == result['summary']['N_drc_fail'] == result['summary']['N_solver_fail'] == 0
    assert result['summary']['completed_joint_hit_fraction_original'] is None
    assert result['ecdf_rows'] == []
