"""Synthetic-only accounting tests; no native evidence or scientific results."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import random

import pytest

from research.broadband56_nn.eucap15_acquisition_budget import (
    ARMS, CHECKPOINTS, IDENTITY, matched_prefixes,
)


def stamp(second):
    return (datetime(2026, 9, 9, tzinfo=timezone.utc) + timedelta(seconds=second)).isoformat()


def fixture():
    proposals = []
    for arm_order in range(1, 129):
        for arm in ARMS:
            proposals.append(dict(candidate_id=f'{arm}-{129-arm_order:03}', arm=arm,
                arm_order=arm_order, global_order=len(proposals) + 1))
    rows = [dict(p, state='NOT_STARTED', solver_start_order=None,
                 solver_started_utc=None, closed_utc=None, solver_seconds=None)
            for p in proposals]
    return proposals, dict(schema='eucap15_acquisition_budget_ledger.v1', complete=True,
                           observed_utc=stamp(10000), rows=rows)


def start_rows(ledger, indices):
    for ordinal, index in enumerate(indices, 1):
        ledger['rows'][index].update(state='STRICT_VALID', solver_start_order=ordinal,
            solver_started_utc=stamp(ordinal), closed_utc=stamp(1000 + ordinal),
            solver_seconds=12.5, solver_start_evidence_class='ACTUAL_SOLVER_START')


def first(n):
    proposals, ledger = fixture()
    start_rows(ledger, range(n))
    return proposals, ledger


def checkpoint(result, m=16):
    return next(row for row in result['checkpoints'] if row['m'] == m)


def assert_unknown(result):
    assert result['status'] == 'UNKNOWN'
    assert result['arms'] is None
    assert result['coverage_gain'] is None
    assert all(row['status'] == 'UNKNOWN' and row['prefixes'] is None
               and not row['equal_budget_publishable'] for row in result['checkpoints'])


def test_closed_16_keeps_failure_and_invalid_cost_denominator():
    proposals, ledger = first(32)
    for row, state in zip(ledger['rows'][:4], ['SOLVER_FAIL', 'FEATURE_FAIL', 'EMX_INVALID', 'EMX_INVALID']):
        row['state'] = state
    result = matched_prefixes(proposals, ledger)
    point = checkpoint(result)
    assert point['status'] == 'MATCHED_CLOSED_SOLVER_START_PREFIX'
    assert point['equal_budget_publishable']
    assert point['prefixes'][ARMS[0]]['outcome_counts']['STRICT_VALID'] == 14
    assert point['prefixes'][ARMS[1]]['outcome_counts']['STRICT_VALID'] == 14
    assert all(p['cost_denominator'] == p['n_started'] == p['n_closed'] == 16
               and p['solver_seconds'] == 200 for p in point['prefixes'].values())
    assert all(checkpoint(result, m)['status'] == 'PREFIX_NOT_REACHED' for m in (32, 64, 128))
    assert result['coverage_gain'] is None
    assert result['source_validation'] == 'NOT_PERFORMED_BY_PURE_FUNCTION'
    assert 'CALLER_MUST_VALIDATE' in result['provenance_scope']
    assert result['superiority_claim'] == 'NOT_ESTABLISHED_SINGLE_PAIR'


def test_all_four_fixed_checkpoints_and_no_input_mutation():
    proposals, ledger = first(256)
    before = deepcopy((proposals, ledger))
    result = matched_prefixes(proposals, ledger)
    assert [row['m'] for row in result['checkpoints']] == list(CHECKPOINTS)
    assert all(row['equal_budget_publishable'] for row in result['checkpoints'])
    assert (proposals, ledger) == before


def test_storage_and_completion_order_do_not_select_prefix():
    proposals, ledger = first(64)
    for row in ledger['rows'][:64]:
        row['closed_utc'] = stamp(2000 - row['solver_start_order'])
    expected = matched_prefixes(proposals, ledger)
    random.Random(42).shuffle(ledger['rows'])
    assert matched_prefixes(proposals, ledger) == expected
    assert checkpoint(expected)['prefixes'][ARMS[0]]['candidate_ids'][0].endswith('-128')


def test_cross_arm_start_interleaving_is_not_a_new_contract_constraint():
    proposals, ledger = fixture()
    start_rows(ledger, list(range(0, 32, 2)) + list(range(1, 32, 2)))
    result = matched_prefixes(proposals, ledger)
    assert checkpoint(result)['equal_budget_publishable']
    assert checkpoint(result)['prefixes'][ARMS[1]]['solver_start_orders'] == list(range(17, 33))


@pytest.mark.parametrize('n,expected', [(0, 'PREFIX_NOT_REACHED'), (31, 'PREFIX_NOT_REACHED'),
                                       (32, 'MATCHED_CLOSED_SOLVER_START_PREFIX')])
def test_complete_ledger_unequal_starts_are_known_but_not_matched(n, expected):
    proposals, ledger = first(n)
    result = matched_prefixes(proposals, ledger)
    assert checkpoint(result)['status'] == expected
    assert result['arms'][ARMS[0]]['solver_starts'] == (n + 1) // 2
    assert result['arms'][ARMS[1]]['solver_starts'] == n // 2
    if n < 32:
        assert all(p['cost_denominator'] is None for p in checkpoint(result)['prefixes'].values())


@pytest.mark.parametrize('pending_index,publishable', [(0, False), (31, False), (32, True)])
def test_pending_only_blocks_prefix_that_contains_it(pending_index, publishable):
    proposals, ledger = first(34)
    ledger['rows'][pending_index].update(state='SOLVER_PENDING', closed_utc=None, solver_seconds=None)
    point = checkpoint(matched_prefixes(proposals, ledger))
    assert point['equal_budget_publishable'] is publishable
    assert point['status'] == ('MATCHED_CLOSED_SOLVER_START_PREFIX' if publishable else 'PREFIX_PENDING')


def test_pre_native_failure_and_held_preserve_original_128_without_solver_cost():
    proposals, ledger = fixture()
    ledger['rows'][0].update(state='GDS_FAIL', closed_utc=stamp(0))
    ledger['rows'][1].update(state='HELD_NO_REPLACEMENT', closed_utc=stamp(0))
    start_rows(ledger, range(2, 34))
    result = matched_prefixes(proposals, ledger)
    assert checkpoint(result)['equal_budget_publishable']
    assert all(row['original_proposals'] == row['pre_native_failure_denominator'] == 128
               and row['solver_starts'] == 16 for row in result['arms'].values())
    assert result['arms'][ARMS[0]]['pre_native_failures'] == 1
    assert result['arms'][ARMS[1]]['held_no_replacement'] == 1
    assert checkpoint(result)['prefixes'][ARMS[0]]['arm_orders'][0] == 2


def test_missing_solver_duration_is_null_not_zero_and_does_not_erase_closure():
    proposals, ledger = first(32)
    ledger['rows'][0]['solver_seconds'] = None
    result = matched_prefixes(proposals, ledger)
    point = checkpoint(result)
    assert point['equal_budget_publishable']
    arm = point['prefixes'][ARMS[0]]
    assert arm['solver_seconds'] is None and arm['solver_seconds_status'] == 'UNKNOWN_MISSING_REPORTED_DURATION'
    assert arm['n_closed'] == arm['cost_denominator'] == 16
    assert arm['end_to_end_seconds'] is None


@pytest.mark.parametrize('kind', ['none', 'incomplete', 'cutoff', 'rows', 'proposal',
                                  'required_field', 'start', 'closure', 'gap',
                                  'start_provenance', 'wrapper_dispatch_only', 'earlier_pending'])
def test_missing_evidence_is_unknown_not_zero(kind):
    proposals, ledger = first(32)
    if kind == 'none':
        ledger = None
    elif kind == 'incomplete':
        ledger['complete'] = False
    elif kind == 'cutoff':
        ledger.pop('observed_utc')
    elif kind == 'rows':
        ledger.pop('rows')
    elif kind == 'proposal':
        ledger['rows'].pop()
    elif kind == 'required_field':
        ledger['rows'][0].pop('solver_seconds')
    elif kind == 'start':
        ledger['rows'][0]['solver_started_utc'] = None
    elif kind == 'closure':
        ledger['rows'][0]['closed_utc'] = None
    elif kind == 'gap':
        ledger['rows'][31]['solver_start_order'] = 33
    elif kind == 'start_provenance':
        ledger['rows'][0].pop('solver_start_evidence_class')
    elif kind == 'wrapper_dispatch_only':
        ledger['rows'][0]['solver_start_evidence_class'] = 'WRAPPER_DISPATCH_ONLY'
    else:
        proposals, ledger = fixture()
        start_rows(ledger, range(2, 34))
    assert_unknown(matched_prefixes(proposals, ledger))


@pytest.mark.parametrize('field,value', [('arm', ARMS[1]), ('arm_order', 2),
                                       ('global_order', 2), ('global_order', True),
                                       ('candidate_id', 'FOREIGN')])
def test_changed_proposal_identity_is_rejected(field, value):
    proposals, ledger = first(32)
    ledger['rows'][0][field] = value
    with pytest.raises(ValueError):
        matched_prefixes(proposals, ledger)


@pytest.mark.parametrize('kind', ['ledger_candidate', 'start', 'frozen_candidate',
                                  'frozen_order', 'frozen_short', 'arm_reversal', 'time_reversal'])
def test_duplicate_or_reversed_order_rejected(kind):
    proposals, ledger = first(32)
    if kind == 'ledger_candidate':
        ledger['rows'].append(deepcopy(ledger['rows'][0]))
    elif kind == 'start':
        ledger['rows'][1]['solver_start_order'] = 1
    elif kind == 'frozen_candidate':
        proposals[1]['candidate_id'] = proposals[0]['candidate_id']
    elif kind == 'frozen_order':
        proposals[0], proposals[1] = proposals[1], proposals[0]
    elif kind == 'frozen_short':
        proposals.pop()
    elif kind == 'arm_reversal':
        for key in ('solver_start_order', 'solver_started_utc'):
            ledger['rows'][0][key], ledger['rows'][2][key] = ledger['rows'][2][key], ledger['rows'][0][key]
    else:
        ledger['rows'][0]['solver_started_utc'] = stamp(9)
    with pytest.raises(ValueError):
        matched_prefixes(proposals, ledger)


@pytest.mark.parametrize('kind', ['pre_native_start', 'pre_native_seconds', 'unstarted_closed',
                                  'pending_closed', 'negative_duration', 'nan_duration',
                                  'bool_duration', 'naive_time', 'closure_before_start',
                                  'future_start', 'unknown_state'])
def test_invalid_null_cost_and_chronology_guards(kind):
    proposals, ledger = first(32)
    row = ledger['rows'][0]
    if kind == 'pre_native_start':
        row['state'] = 'DRC_FAIL'
    elif kind == 'pre_native_seconds':
        ledger['rows'][32].update(state='GDS_FAIL', closed_utc=stamp(10), solver_seconds=0)
    elif kind == 'unstarted_closed':
        ledger['rows'][32]['closed_utc'] = stamp(10)
    elif kind == 'pending_closed':
        row['state'] = 'SOLVER_PENDING'
    elif kind in ('negative_duration', 'nan_duration', 'bool_duration'):
        row['solver_seconds'] = {'negative_duration': -1, 'nan_duration': float('nan'), 'bool_duration': True}[kind]
    elif kind == 'naive_time':
        row['solver_started_utc'] = '2026-09-09T00:00:01'
    elif kind == 'closure_before_start':
        row['closed_utc'] = stamp(0)
    elif kind == 'future_start':
        row['solver_started_utc'] = stamp(20000)
    else:
        row['state'] = 'GUESS_SUCCESS'
    with pytest.raises(ValueError):
        matched_prefixes(proposals, ledger)


def test_pre_native_missing_closure_is_unknown():
    proposals, ledger = fixture()
    ledger['rows'][0]['state'] = 'ANALYTIC_FAIL'
    assert_unknown(matched_prefixes(proposals, ledger))


def test_frame_ignores_extra_scientific_columns_but_does_not_modify_them():
    proposals, ledger = first(32)
    for proposal in proposals:
        proposal.update(actual_response=None, native_status='NOT_RUN', analytic={'pass': True})
    assert checkpoint(matched_prefixes(proposals, ledger))['equal_budget_publishable']
    assert all(set(IDENTITY) < proposal.keys() and proposal['actual_response'] is None for proposal in proposals)
