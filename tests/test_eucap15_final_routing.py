"""Synthetic routing only: no frozen real targets, models or native execution."""
from copy import deepcopy
import math

import numpy as np
import pytest

from research.broadband56_nn.eucap15_final_routing import route_request
from research.broadband56_nn.frequency_qscan import q_targets, score, select_q


FIELDS = [f'synthetic_geometry_{i}' for i in range(10)]
MODEL = 'SYNTHETIC_FINAL_ID_NOT_A_MODEL'


def fixture(audit=False, best=14):
    request = dict(request_id='SYNTHETIC-000000', lp_nh=1.0, ls_nh=1.1,
                   k_abs=.4, full11_audit=audit, request_order=0)
    targets = q_targets([1.0, 1.1, .4])
    rows = []
    for q, target in zip(range(10, 21), targets):
        proxy = target.copy()
        proxy[0] += abs(q-best) / 100
        rows.append(dict(request_id=request['request_id'], model_id=MODEL,
            frequency_ghz=15, target_source='SYNTHETIC_FINAL_TRIPLE', dataset_scope='SYNTHETIC',
            candidate_id=f"{request['request_id']}-q{q:02d}", q_target=q,
            geometry_fields=FIELDS.copy(), continuous_geometry=[1.0]*10, grid_geometry=[1.0]*10,
            target=target.tolist(), grid_proxy=proxy.tolist(), analytic_raw=True, analytic_grid=True,
            evidence_source='SELF_PROXY', parameter_identity_not_actual_gds=True,
            actual_gds_geometry=None, gds_sha256=None, cadence_status='NOT_RUN',
            calibre_blocking_count=None, emx_status='NOT_RUN', s4p_sha256=None,
            actual_response=None, emx_minus_target=None, emx_minus_proxy=None,
            unique_emx_solve=False, preselected_emx=False))
    refresh(rows)
    return request, rows


def refresh(rows):
    proxy = np.asarray([[np.nan if v is None else v for v in r['grid_proxy']] for r in rows])
    scores = score(proxy, np.asarray([r['target'] for r in rows]))
    selected = select_q(scores)['q_proxy']
    for row, value in zip(rows, scores):
        row.update(grid_proxy_score=float(value) if np.isfinite(value) else None,
                   q_proxy=selected, proxy_preselected=row['q_target'] == selected)


def route(request, rows):
    return route_request(request, rows, MODEL, FIELDS)


def test_main_only_one_candidate_and_no_input_mutation():
    request, rows = fixture()
    before = deepcopy((request, rows))
    result = route(request, rows)
    assert result['q_proxy'] == 14 and result['main']['status'] == 'PENDING'
    assert result['audit_slots'] == [] and result['additional_audit_slots'] == 0
    assert result['N_unique_logical_candidates'] == 1
    assert result['unique_candidates'][0]['memberships'] == ['MAIN']
    assert result['q_emx'] is None and result['full11_physical_status'] == 'NOT_EVALUATED'
    assert not result['native_started'] and not result['native_dispatch_authorized']
    assert result['independent_solves'] == 0 and (request, rows) == before
    result['unique_candidates'][0]['original_record']['grid_geometry'][0] = 999
    assert rows[4]['grid_geometry'][0] == 1.0


def test_audit_reuses_main_identity_once_not_twelve():
    request, rows = fixture(True)
    result = route(request, rows)
    assert result['N_unique_logical_candidates'] == len(result['audit_slots']) == 11
    assert result['additional_audit_slots'] == 10
    shared = [r for r in result['unique_candidates'] if r['memberships'] == ['MAIN', 'AUDIT']]
    assert len(shared) == 1 and shared[0]['candidate_id'] == result['main']['candidate_id']
    assert not result['budget']['requires_extra_audit_budget']
    assert not result['budget']['campaign_additional_1000_limit_checked']


def test_analytic_failure_does_not_replace_main_or_block_other_audit_slots():
    request, rows = fixture(True)
    rows[4]['analytic_grid'] = False
    rows[6]['analytic_grid'] = False
    result = route(request, rows)
    assert result['q_proxy'] == 14 and result['main']['status'] == 'ANALYTIC_FAIL'
    assert len(result['unique_candidates']) == 11
    assert [r['q_target'] for r in result['audit_slots'] if r['status'] == 'ANALYTIC_FAIL'] == [14, 16]
    assert sum(r['status'] == 'PENDING' for r in result['audit_slots']) == 9
    assert result['additional_audit_slots'] == 10


def test_exact_score_tie_selects_smallest_q_even_if_analytic_failure():
    request, rows = fixture(True)
    for row in rows:
        row['grid_proxy'] = row['target'].copy()
    rows[0]['analytic_grid'] = False
    refresh(rows)
    result = route(request, rows)
    assert result['q_proxy'] == 10 and result['main']['status'] == 'ANALYTIC_FAIL'


@pytest.mark.parametrize('audit', [False, True])
def test_incomplete_scan_main_unselected_audit_keeps_eleven_and_budget(audit):
    request, rows = fixture(audit)
    rows[3]['grid_proxy'][0] = None
    refresh(rows)
    result = route(request, rows)
    assert result['q_proxy'] is None and result['N_proxy_finite'] == 10
    assert result['main']['status'] == 'NO_SELECTION_INCOMPLETE_PROXY_SCAN'
    assert result['main']['candidate_id'] is None
    assert result['N_unique_logical_candidates'] == (11 if audit else 0)
    assert result['additional_audit_slots'] == (11 if audit else 0)
    assert result['budget']['requires_extra_audit_budget'] is audit
    assert all(r['memberships'] == ['AUDIT'] for r in result['unique_candidates'])
    assert not result['native_dispatch_authorized']


def test_all_nonfinite_proxy_and_geometry_failure_retains_audit_slots():
    request, rows = fixture(True)
    for row in rows:
        row.update(grid_proxy=[None]*4, grid_geometry=[None]*10,
                   continuous_geometry=[None]*10, analytic_grid=False, analytic_raw=False)
    refresh(rows)
    result = route(request, rows)
    assert result['N_proxy_finite'] == 0 and len(result['audit_slots']) == 11
    assert all(row['status'] == 'ANALYTIC_FAIL' for row in result['audit_slots'])
    assert result['budget']['requires_extra_audit_budget']


def test_no_cross_request_geometry_cache_and_legacy_flag_does_not_route_audit():
    request, rows = fixture()
    for row in rows:
        row['preselected_emx'] = True
    a = route(request, rows)
    request['request_id'] = 'SYNTHETIC-000001'
    request['request_order'] = 1
    for row in rows:
        row.update(request_id=request['request_id'], candidate_id=f"{request['request_id']}-q{row['q_target']:02d}")
    b = route(request, rows)
    assert a['audit_slots'] == b['audit_slots'] == []
    assert a['main']['candidate_id'] != b['main']['candidate_id']
    assert not a['cross_request_geometry_cache'] and not b['cross_request_geometry_cache']


@pytest.mark.parametrize('field,value', [
    ('model_id', 'WRONG'), ('request_id', 'WRONG'), ('candidate_id', 'WRONG-q10'),
    ('q_target', 11), ('frequency_ghz', 16), ('target_source', 'WRONG'),
    ('dataset_scope', 'WRONG'), ('geometry_fields', list(reversed(FIELDS))),
    ('evidence_source', 'FRESH_EMX'), ('actual_response', [1., 1., 10., .4]),
    ('gds_sha256', 'a'*64), ('s4p_sha256', 'b'*64), ('cadence_status', 'PASS'),
    ('emx_status', 'PASS'), ('unique_emx_solve', True), ('actual', [1., 1., 10., .4]),
    ('strict_joint_hit', False), ('q_emx', 10), ('candidate_id_sha256', '0'*64),
    ('analytic_grid', 1), ('grid_proxy_score', .999), ('q_proxy', 10),
    ('proxy_preselected', True), ('grid_proxy', [True, 1., 10., .4]),
])
def test_malformed_or_already_physical_candidate_rejected(field, value):
    request, rows = fixture()
    rows[0][field] = value
    with pytest.raises(ValueError):
        route(request, rows)


def test_one_ulp_target_change_is_rejected_not_tolerance_repaired():
    request, rows = fixture()
    rows[0]['target'][3] = math.nextafter(rows[0]['target'][3], math.inf)
    with pytest.raises(ValueError, match='target float64 identity'):
        route(request, rows)


def test_missing_unrun_field_rejected():
    request, rows = fixture()
    rows[0].pop('s4p_sha256')
    with pytest.raises(ValueError, match='SELF_PROXY'):
        route(request, rows)


@pytest.mark.parametrize('field,value', [('full11_audit', 1), ('request_order', True),
    ('request_order', -1), ('lp_nh', 0.), ('k_abs', float('nan'))])
def test_invalid_request_rejected(field, value):
    request, rows = fixture()
    request[field] = value
    with pytest.raises(ValueError):
        route(request, rows)


def test_missing_or_duplicate_q_slot_rejected():
    request, rows = fixture()
    with pytest.raises(ValueError):
        route(request, rows[:-1])
    rows[-1] = deepcopy(rows[0])
    with pytest.raises(ValueError):
        route(request, rows)


def test_attempted_partial_scan_fallback_selection_rejected():
    request, rows = fixture(True)
    rows[1]['grid_proxy'][0] = None
    refresh(rows)
    for row in rows:
        row['q_proxy'] = 14
        row['proxy_preselected'] = row['q_target'] == 14
    with pytest.raises(ValueError, match='q_proxy differs'):
        route(request, rows)


def test_analytic_pass_requires_finite_geometry():
    request, rows = fixture(True)
    rows[0]['grid_geometry'][0] = None
    with pytest.raises(ValueError, match='analytic PASS'):
        route(request, rows)
