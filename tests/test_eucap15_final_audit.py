"""Tiny synthetic audit rows; not a model, target-frame or native evaluation."""
from copy import deepcopy
import json
import math

import pytest

from research.broadband56_nn.eucap15_final_audit import summarize_audit, STATES


RID = 'SYNTHETIC-AUDIT'
SPANS = [2.5, 2.5, 20., .8]
TAU = [.125, .125, 1., .04]


def rows(state='STRICT_VALID', best=12):
    result = []
    for q in range(10, 21):
        target = [1., 1.1, float(q), .4]
        actual = target.copy() if state in ('STRICT_VALID', 'EMX_INVALID') else None
        if actual is not None:
            actual[0] += abs(q-best)*.02
        result.append(dict(candidate_id=f'{RID}-q{q:02d}', q_target=q, target=target,
            grid_proxy=target.copy(), state=state, actual=actual,
            evidence_ref=dict(path=f'/SYNTHETIC/q{q:02d}/FEATURE.json', sha256=f'{q:064x}', bytes=100)
                         if actual is not None else None))
    return result


def call(candidates, q_proxy=14, **kwargs):
    return summarize_audit(RID, q_proxy, candidates, score_spans=kwargs.get('score_spans', SPANS),
                           tolerances=kwargs.get('tolerances', TAU))


def test_complete11_own_q_targets_regret_and_original_inputs():
    candidates = rows()
    before = deepcopy(candidates)
    result = call(candidates)
    assert result['q_emx'] == 12 and result['q_proxy'] == 14
    assert result['N_original_candidates'] == result['N_strict_valid'] == 11
    assert result['selection_regret'] == pytest.approx(.04 / 2.5 / 2)
    assert result['selection_regret'] != 14-12
    assert result['emx_selected_q_emx']['emx_score'] == 0
    assert result['fixed_q15']['strict_errors']['emx_minus_target'][2] == 0
    assert result['fixed_q15']['strict_errors']['target_relative_absolute_percent'][0] == pytest.approx(6.)
    assert result['fixed_q15_minus_preselected_score'] == pytest.approx(.02/2.5/2)
    assert result['source_validation'] == 'NOT_PERFORMED_BY_PURE_FUNCTION'
    assert result['N_request_groups'] == 1 and result['ci_status'] == 'NOT_ESTIMATED'
    assert result['native_attempts'] is None and result['independent_native_solves'] is None
    assert candidates == before
    result['fixed_q15']['target'][0] = 99
    assert candidates[5]['target'][0] == 1.
    assert result['candidate_rows'][5]['target'][0] == 1.


def test_complete_ties_select_smaller_q_not_larger_quality():
    candidates = rows()
    for row in candidates:
        row['actual'] = row['target'].copy()
    result = call(candidates, 20)
    assert result['q_emx'] == 10 and result['q_proxy'] == 20
    assert result['selection_regret'] == 0. and result['selection_agreement'] is False


@pytest.mark.parametrize('failed_state', [s for s in STATES if s != 'STRICT_VALID'])
def test_every_nonvalid_slot_blocks_full_optimum_without_hiding_it(failed_state):
    candidates = rows()
    row = candidates[10]
    row.update(state=failed_state, actual=None, evidence_ref=None)
    result = call(candidates)
    assert result['N_original_candidates'] == len(result['candidate_rows']) == 11
    assert result['state_counts'][failed_state] == 1 and result['N_strict_valid'] == 10
    assert result['q_emx'] is None and result['selection_regret'] is None
    assert result['emx_selected_q_emx']['emx_score'] is None
    assert result['fixed_q15']['emx_score'] is not None
    assert result['preselected_q_proxy']['emx_score'] is not None
    assert result['fixed_q15_minus_preselected_score'] is not None
    assert 'best_available_emx' not in result and 'survivor_best' not in result


def test_selected_failure_not_replaced_by_strict_survivor():
    candidates = rows()
    candidates[4].update(state='GDS_FAIL', actual=None, evidence_ref=None)
    result = call(candidates)
    assert result['q_proxy'] == 14 and result['preselected_q_proxy']['candidate_id'].endswith('-q14')
    assert result['preselected_q_proxy']['emx_score'] is None
    assert result['preselected_q_proxy']['state'] == 'GDS_FAIL'
    assert result['q_emx'] is None and result['selection_regret'] is None


def test_invalid_finite_values_remain_separate_diagnostic_only():
    candidates = rows()
    candidates[4]['state'] = 'EMX_INVALID'
    result = call(candidates)
    chosen = result['preselected_q_proxy']
    assert chosen['actual'] == candidates[4]['actual']
    assert chosen['strict_errors'] is None and chosen['emx_score'] is None
    assert chosen['strict_joint_hit'] is False
    assert chosen['invalid_diagnostic_errors']['normalized_response_score'] is not None
    assert result['N_strict_valid'] == 10 and result['q_emx'] is None


def test_invalid_nonfinite_or_component_null_does_not_become_zero_error():
    candidates = rows()
    candidates[4].update(state='EMX_INVALID', actual=[None, float('nan'), float('inf'), .4])
    result = call(candidates)
    assert result['preselected_q_proxy']['actual'] == [None, None, None, .4]
    assert result['preselected_q_proxy']['invalid_diagnostic_errors'] is None
    json.dumps(result, allow_nan=False)


def test_no_strict_valid_null_errors_and_pending_denominator():
    result = call(rows('PENDING'))
    assert result['N_strict_valid'] == 0 and result['N_pending'] == 11
    assert result['N_terminal'] == 0 and result['conditional_strict_hit_fraction'] is None
    assert result['completed_hit_fraction_original'] is None
    assert result['observed_hit_fraction_original'] == 0
    assert all(row['strict_errors'] is None and row['emx_score'] is None for row in result['candidate_rows'])


def test_missing_preselection_is_not_inferred_even_for_full_strict_audit():
    result = call(rows(), None)
    assert result['q_proxy'] is None and result['q_emx'] == 12
    assert result['preselected_q_proxy']['candidate_id'] is None
    assert result['selection_regret'] is None and result['selection_agreement'] is None


def test_nullable_proxies_do_not_erase_valid_physics_or_change_preselection():
    candidates = rows()
    candidates[4]['grid_proxy'] = [None, 1.0, None, .4]
    candidates[5]['grid_proxy'] = None
    result = call(candidates)
    assert result['q_proxy'] == 14 and result['q_emx'] == 12
    assert result['preselected_q_proxy']['emx_minus_grid_proxy'] == [None, pytest.approx(.1), None, 0.]
    assert result['fixed_q15']['emx_minus_grid_proxy'] is None


def test_input_order_does_not_change_q_order_or_result():
    candidates = rows()
    assert call(list(reversed(candidates))) == call(candidates)


def test_exact_caller_tolerance_float_used_and_q_overshoot_is_error():
    candidates = rows()
    for row in candidates:
        row['target'][3] = .21
        row['actual'] = row['target'].copy()
        row['actual'][3] = .25
    strict = call(candidates)
    legacy = call(candidates, tolerances=[.125, .125, 1., .05*.8])
    assert strict['N_strict_joint_hit'] == 0 and legacy['N_strict_joint_hit'] == 11
    candidates[4]['actual'][2] += 1.1
    value = call(candidates)['preselected_q_proxy']
    assert value['strict_errors']['emx_minus_target'][2] == pytest.approx(1.1)
    assert value['strict_errors']['within_tolerance'][2] is False


def test_near_zero_relative_percent_suppressed_not_hit_gate():
    candidates = rows()
    for row in candidates:
        row['target'][3] = 1e-8
        row['actual'][3] = 1.01e-8
    value = call(candidates)['fixed_q15']['strict_errors']
    assert value['target_relative_absolute_percent'][3] is None
    assert value['target_relative_percent_status'][3] == 'NEAR_ZERO_REFERENCE_NOT_REPORTED'
    assert value['within_tolerance'][3] is True


@pytest.mark.parametrize('field,value', [
    ('candidate_id', 'foreign-q10'), ('q_target', 11), ('q_target', True),
    ('request_id', 'foreign'), ('q_proxy', 13), ('state', 'UNKNOWN'),
    ('target', [1., 1.1, 11., .4]), ('target', [True, 1.1, 10., .4]),
    ('actual', None), ('actual', [None, 1., 10., .4]),
    ('actual', [1., float('inf'), 10., .4]), ('actual', [True, 1., 10., .4]),
    ('grid_proxy', [1., 'bad', 10., .4]), ('grid_proxy', [1., float('nan'), 10., .4]),
    ('evidence_ref', None), ('evidence_ref', {'path':'/SYNTHETIC/x','sha256':'a'*64,'bytes':True}),
    ('evidence_ref', {'path':'relative','sha256':'a'*64,'bytes':10}),
    ('evidence_ref', {'path':'/SYNTHETIC/x','sha256':'not-a-sha','bytes':10}),
])
def test_bad_candidate_rejected(field, value):
    candidates = rows()
    candidates[0][field] = value
    with pytest.raises(ValueError):
        call(candidates)


def test_one_ulp_triple_change_rejected():
    candidates = rows()
    candidates[10]['target'][0] = math.nextafter(1., math.inf)
    with pytest.raises(ValueError, match='exact same'):
        call(candidates)


@pytest.mark.parametrize('q_proxy', [True, 9, 21, 14.0, '14'])
def test_bad_frozen_q_proxy_rejected(q_proxy):
    with pytest.raises(ValueError):
        call(rows(), q_proxy)


def test_missing_or_duplicate_rows_rejected():
    candidates = rows()
    with pytest.raises(ValueError):
        call(candidates[:-1])
    candidates[-1] = deepcopy(candidates[0])
    with pytest.raises(ValueError):
        call(candidates)


def test_failed_actual_and_invalid_bool_rejected():
    candidates = rows()
    candidates[0]['state'] = 'FEATURE_FAIL'
    with pytest.raises(ValueError, match='cannot carry'):
        call(candidates)
    candidates[0].update(state='EMX_INVALID', actual=[True, None, None, .4])
    with pytest.raises(ValueError):
        call(candidates)


@pytest.mark.parametrize('kwargs', [{'score_spans':[2.5,2.5,20.,1.]}, {'tolerances':[.125,.125,1.,0.]}])
def test_wrong_spans_or_tolerances_rejected(kwargs):
    with pytest.raises(ValueError):
        call(rows(), **kwargs)


def test_finite_actual_with_overflowing_score_fails_closed():
    candidates = rows()
    candidates[0]['actual'][0] = 1e308
    with pytest.raises(ValueError, match='score exceeds'):
        call(candidates)
