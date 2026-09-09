"""Tiny synthetic frames/publications; no real target/model/EMX access."""
from copy import deepcopy
import pytest

from research.broadband56_nn.eucap15_final_statistics import summarize_frame, record_sha256
from tests.test_eucap15_final_routing import fixture, refresh, MODEL, FIELDS

SPANS = [2.5, 2.5, 20, .8]
TAU = [.125, .125, 1., .04000000000000001]
PIN = dict(path='/SYNTHETIC_NOT_REAL/evidence.json', sha256='a'*64, bytes=1)


def data(count=1, audit=False):
    bundles = []
    for i in range(count):
        request, rows = fixture(audit)
        request.update(request_id=f'SYNTHETIC-{i:06d}', request_order=i)
        for row in rows:
            row.update(request_id=request['request_id'], candidate_id=f"{request['request_id']}-q{row['q_target']:02d}")
        bundles.append(dict(request=request, records=rows))
    context = dict(frame_sha256='1'*64, model_freeze_sha256='2'*64,
                   model_id=MODEL, geometry_fields=FIELDS.copy(), N_original_requests=count)
    return bundles, context


def pub(bundle, context, q=14, state='STRICT_VALID'):
    row = bundle['records'][q-10]
    return dict(frame_sha256=context['frame_sha256'], model_freeze_sha256=context['model_freeze_sha256'],
        model_id=MODEL, request_id=row['request_id'], candidate_id=row['candidate_id'],
        frozen_record_sha256=record_sha256(row), state=state,
        actual=row['target'].copy() if state == 'STRICT_VALID' else None,
        evidence_ref=deepcopy(PIN), touchstone_sha='b'*64 if state == 'STRICT_VALID' else None,
        candidate_geometry_identity_sha256='c'*64, reason_code=None)


def run(bundles, context, publications=()):
    return summarize_frame(bundles, list(publications), context=context, score_spans=SPANS, tolerances=TAU)


def test_original_denominator_failures_missing_selection_and_pending():
    b, c = data(5)
    b[1]['records'][4]['analytic_grid'] = False
    b[2]['records'][2]['grid_proxy'][0] = None
    refresh(b[2]['records'])
    ps = [pub(b[0], c), pub(b[3], c, state='FEATURE_FAIL')]
    result = run(b, c, ps)
    s = result['summary']
    assert (s['N_original_requests'], s['N_main_selected'], s['N_strict_valid'], s['N_pending_requests']) == (5, 4, 1, 1)
    assert s['observed_joint_hit_fraction_original'] == .2
    assert s['completed_joint_hit_fraction_original'] is None
    assert s['conditional_strict_joint_hit_fraction'] == 1
    assert s['state_counts']['ANALYTIC_FAIL'] == s['state_counts']['FEATURE_FAIL'] == s['N_no_selection'] == 1
    assert all(row['N_original'] == 5 and row['n'] == 1 for row in result['metric_rows'])
    assert len(result['ecdf_rows']) == 8 and all(row['N_original'] == 5 for row in result['ecdf_rows'])
    assert result['request_rows'][2]['target'] is None
    assert s['independent_native_solves'] is None and not s['native_dispatch_authorized']


def test_shared_audit_candidate_does_not_inflate_main_or_replace_qproxy():
    b, c = data(audit=True)
    ps = [pub(b[0], c, q) for q in range(10, 21)]
    ps[4]['actual'][0] += .1
    result = run(b, c, ps)
    s, a = result['summary'], result['audit_rows'][0]
    assert s['N_original_requests'] == 1 and s['N_unique_logical_candidates'] == 11
    assert s['N_original_audit_slots'] == 11 and s['N_complete_strict_audits'] == 1
    assert result['request_rows'][0]['q_proxy'] == 14 and a['q_emx'] == 10
    assert a['selection_regret'] > 0
    assert len(result['ecdf_rows']) == 8 and all(row['n'] == 1 for row in result['metric_rows'])


def test_failed_preselection_stays_failed_when_audit_others_pass():
    b, c = data(audit=True)
    b[0]['records'][4]['analytic_grid'] = False
    ps = [pub(b[0], c, q) for q in range(10, 21) if q != 14]
    r = run(b, c, ps)
    assert r['request_rows'][0]['state'] == 'ANALYTIC_FAIL'
    assert r['summary']['N_joint_hit'] == 0 and r['summary']['completed_joint_hit_fraction_original'] == 0
    assert r['audit_rows'][0]['q_emx'] is None
    assert all(row['mae'] is None for row in r['metric_rows'])


def test_resource_pending_retained_not_failure():
    b, c = data()
    p = pub(b[0], c, state='PENDING')
    p.update(reason_code='RESOURCE_WAIT', evidence_ref=None)
    r = run(b, c, [p])
    assert r['summary']['N_pending_requests'] == 1
    assert r['request_rows'][0]['reason_code'] == 'RESOURCE_WAIT'


def test_feature_failure_can_keep_s4p_but_no_label_error():
    b, c = data()
    p = pub(b[0], c, state='FEATURE_FAIL')
    p['touchstone_sha'] = 'b'*64
    r = run(b, c, [p])
    assert r['summary']['completion_status'] == 'COMPLETE_ACCOUNTING'
    assert r['request_rows'][0]['strict_errors'] is None


def test_invalid_actual_is_not_strict_metric():
    b, c = data()
    p = pub(b[0], c, state='EMX_INVALID')
    p.update(actual=[1., None, float('nan'), .5], touchstone_sha='b'*64)
    r = run(b, c, [p])
    assert r['summary']['N_strict_valid'] == 0 and r['ecdf_rows'] == []
    assert r['request_rows'][0]['actual'] == [1., None, None, .5]


@pytest.mark.parametrize('key', ['frame_sha256', 'model_freeze_sha256', 'model_id', 'request_id', 'frozen_record_sha256'])
def test_mixed_or_unbound_publication_rejected(key):
    b, c = data()
    p = pub(b[0], c)
    p[key] = 'WRONG'
    with pytest.raises(ValueError):
        run(b, c, [p])


def test_record_target_mutation_breaks_publication_binding():
    b, c = data()
    p = pub(b[0], c)
    b[0]['records'][4]['unused_frozen_metadata'] = 'changed'
    with pytest.raises(ValueError, match='exact frozen candidate'):
        run(b, c, [p])


def test_duplicate_publication_rejected_even_main_audit_shared():
    b, c = data(audit=True)
    p = pub(b[0], c)
    with pytest.raises(ValueError, match='duplicate publication'):
        run(b, c, [p, deepcopy(p)])


def test_unselected_publication_rejected():
    b, c = data()
    with pytest.raises(ValueError, match='foreign/unselected'):
        run(b, c, [pub(b[0], c, q=15)])


@pytest.mark.parametrize('case', ['partial_frame', 'duplicate_request', 'wrong_order'])
def test_original_frame_denominator_not_reduced(case):
    b, c = data(2)
    if case == 'partial_frame':
        b.pop()
    elif case == 'duplicate_request':
        b[1] = deepcopy(b[0])
    else:
        b.reverse()
    with pytest.raises(ValueError):
        run(b, c)


@pytest.mark.parametrize('change', ['analytic_flip', 'missing_evidence', 'missing_s4p', 'missing_geometry', 'bool_actual'])
def test_invalid_physical_publication_shape_rejected(change):
    b, c = data()
    p = pub(b[0], c)
    if change == 'analytic_flip':
        p.update(state='ANALYTIC_FAIL', actual=None, touchstone_sha=None)
    elif change == 'missing_evidence':
        p['evidence_ref'] = None
    elif change == 'missing_s4p':
        p['touchstone_sha'] = None
    elif change == 'missing_geometry':
        p['candidate_geometry_identity_sha256'] = None
    else:
        p['actual'][0] = True
    with pytest.raises(ValueError):
        run(b, c, [p])


def test_no_input_mutation_and_exact_k_tolerance():
    b, c = data()
    p = pub(b[0], c)
    p['actual'][3] += .04
    before = deepcopy((b, c, p))
    r = run(b, c, [p])
    assert (b, c, p) == before
    assert r['request_rows'][0]['strict_joint_hit'] is True
    assert r['summary']['absolute_tolerances'][3] == .04000000000000001
    r['request_rows'][0]['actual'][0] = 99
    assert (b, c, p) == before
