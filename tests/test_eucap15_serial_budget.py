"""Synthetic ordinal-proof tests, not native execution or physical results."""
from copy import deepcopy

import pytest

from research.broadband56_nn.eucap15_acquisition_budget import SERIAL_ORDER, matched_prefixes
from tests.test_eucap15_acquisition_budget import ARMS, checkpoint, first, stamp, assert_unknown


def serial_fixture(n=32):
    proposals, ledger = first(n)
    ledger['solver_execution_mode'] = 'SERIAL_SINGLE_OWNER'
    for row in ledger['rows'][:n]:
        ordinal = row['solver_start_order']
        row.update(solver_start_evidence_class=SERIAL_ORDER, solver_started_utc=None,
                   solver_start_lower_bound_utc=stamp(ordinal * 20),
                   closed_utc=stamp(ordinal * 20 + 15),
                   solver_start_order_proof={'path': '/SYNTHETIC/proof.json', 'sha256': 'a' * 64})
    return proposals, ledger


def test_serial_ordinal_without_exact_birth_preserves_cost_and_inputs():
    proposals, ledger = serial_fixture()
    before = deepcopy((proposals, ledger))
    ledger['rows'][0]['state'] = 'SOLVER_FAIL'
    before[1]['rows'][0]['state'] = 'SOLVER_FAIL'
    result = matched_prefixes(proposals, ledger)
    assert checkpoint(result)['equal_budget_publishable']
    assert result['start_evidence_counts'] == {SERIAL_ORDER: 32}
    assert result['exact_solver_start_timestamps_available'] is False
    assert checkpoint(result)['prefixes'][ARMS[0]]['outcome_counts']['SOLVER_FAIL'] == 1
    assert checkpoint(result)['prefixes'][ARMS[0]]['solver_seconds'] == 200
    assert (proposals, ledger) == before
    assert result['coverage_gain'] is None
    assert result['source_validation'] == 'NOT_PERFORMED_BY_PURE_FUNCTION'


@pytest.mark.parametrize('missing', ['mode', 'proof', 'sha', 'lower', 'closure', 'start'])
def test_missing_serial_evidence_is_unknown(missing):
    proposals, ledger = serial_fixture()
    row = ledger['rows'][0]
    if missing == 'mode':
        ledger.pop('solver_execution_mode')
    elif missing == 'proof':
        row.pop('solver_start_order_proof')
    elif missing == 'sha':
        row['solver_start_order_proof']['sha256'] = 'invalid'
    elif missing == 'lower':
        row.pop('solver_start_lower_bound_utc')
    elif missing == 'closure':
        row['closed_utc'] = None
    else:
        row['solver_start_order'] = None
    assert_unknown(matched_prefixes(proposals, ledger))


@pytest.mark.parametrize('problem', ['invented_birth', 'overlap', 'reverse', 'lower_after_close'])
def test_false_serial_chronology_rejected(problem):
    proposals, ledger = serial_fixture()
    if problem == 'invented_birth':
        ledger['rows'][0]['solver_started_utc'] = ledger['rows'][0]['solver_start_lower_bound_utc']
    elif problem == 'overlap':
        ledger['rows'][0]['closed_utc'] = stamp(41)
    elif problem == 'reverse':
        ledger['rows'][0]['solver_start_lower_bound_utc'] = stamp(50)
    else:
        ledger['rows'][0]['solver_start_lower_bound_utc'] = stamp(36)
    with pytest.raises(ValueError):
        matched_prefixes(proposals, ledger)


def test_closed_serial_proof_does_not_prove_pending_start():
    proposals, ledger = serial_fixture()
    ledger['rows'][0].update(state='SOLVER_PENDING', closed_utc=None, solver_seconds=None)
    assert_unknown(matched_prefixes(proposals, ledger))


def test_exact_pending_last_after_serial_prefix_supported():
    proposals, ledger = serial_fixture(33)
    row = ledger['rows'][32]
    row.update(state='SOLVER_PENDING', closed_utc=None, solver_seconds=None,
               solver_start_evidence_class='ACTUAL_SOLVER_START', solver_started_utc=stamp(700))
    assert checkpoint(matched_prefixes(proposals, ledger))['equal_budget_publishable']


def test_exact_pending_before_later_serial_closure_rejected():
    proposals, ledger = serial_fixture()
    ledger['rows'][0].update(state='SOLVER_PENDING', closed_utc=None, solver_seconds=None,
        solver_start_evidence_class='ACTUAL_SOLVER_START', solver_started_utc=stamp(20))
    with pytest.raises(ValueError, match='unclosed prior'):
        matched_prefixes(proposals, ledger)


def test_seven_closed_is_known_not_matched():
    proposals, ledger = serial_fixture(7)
    ledger['rows'][0]['solver_seconds'] = None
    result = matched_prefixes(proposals, ledger)
    assert result['arms'][ARMS[0]]['solver_starts'] == 4
    assert result['arms'][ARMS[1]]['solver_starts'] == 3
    assert checkpoint(result)['status'] == 'PREFIX_NOT_REACHED'
    assert not checkpoint(result)['equal_budget_publishable']
    assert result['arms'][ARMS[0]]['solver_seconds'] is None
