"""MAIN-frame accounting and paired audit integration, without native I/O.

The caller supplies original frozen requests/Q-scan records and *already
chain-verified* publications. This module checks their cross-identities, not the
referenced files, PDK, S4P, solver freshness, or FINAL model selection. A receipt
shaped like a pin is not physical proof. No FINAL experiment is launched here.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re

from .eucap15_final_routing import route_request
from .eucap15_final_audit import STATES, _pin, _vector, _target_errors, summarize_audit
from .eucap15_selected_metrics import SCORE_SPANS, summarize as selected_metrics


NO_SELECTION = 'NO_SELECTION_INCOMPLETE_PROXY_SCAN'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value, name):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None,
             name + ': exact lowercase SHA256 required')
    return value


def record_sha256(record):
    """Canonical JSON binding; distinct from the raw shard-file SHA256."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _publication(source, slot, context):
    needed = {'frame_sha256', 'model_freeze_sha256', 'model_id', 'request_id',
              'candidate_id', 'frozen_record_sha256', 'state', 'actual',
              'evidence_ref', 'touchstone_sha', 'candidate_geometry_identity_sha256',
              'reason_code'}
    _require(isinstance(source, Mapping) and needed <= source.keys(), 'publication fields missing')
    for key in ('frame_sha256', 'model_freeze_sha256', 'model_id'):
        _require(source[key] == context[key], 'publication scope differs: ' + key)
    for key in ('request_id', 'candidate_id'):
        _require(source[key] == slot[key], 'publication identity differs: ' + key)
    _require(source['frozen_record_sha256'] == record_sha256(slot['original_record']),
             'publication does not bind the exact frozen candidate record')
    state = source['state']
    _require(isinstance(state, str) and state in STATES, 'unknown publication state')
    _require((state == 'ANALYTIC_FAIL') == (slot['status'] == 'ANALYTIC_FAIL'),
             'publication changes the original analytic gate')
    evidence = _pin(source['evidence_ref'])
    _require(state in ('PENDING', 'ANALYTIC_FAIL') or evidence is not None,
             'published physical terminal needs a caller-verified evidence reference')
    geometry = source['candidate_geometry_identity_sha256']
    if geometry is not None:
        _sha(geometry, 'candidate_geometry_identity_sha256')
    touchstone = source['touchstone_sha']
    if touchstone is not None:
        _sha(touchstone, 'touchstone_sha')
    reason = source['reason_code']
    _require(reason is None or (isinstance(reason, str) and bool(reason.strip())), 'invalid reason_code')
    actual = source['actual']
    if state == 'STRICT_VALID':
        actual = _vector(actual, 'STRICT_VALID actual')
        _require(geometry is not None and touchstone is not None,
                 'STRICT_VALID needs verified geometry and touchstone identities')
    elif state == 'EMX_INVALID':
        actual = _vector(actual, 'EMX_INVALID actual', nullable=True, nonfinite=True)
        _require(actual is None or touchstone is not None, 'invalid actual needs touchstone identity')
    else:
        _require(actual is None, 'failed/pending state must not carry actual labels')
        if state != 'FEATURE_FAIL':
            _require(touchstone is None, 'pre-EM state cannot carry a touchstone')
    return dict(state=state, actual=actual, evidence_ref=evidence, touchstone_sha=touchstone,
                candidate_geometry_identity_sha256=geometry, reason_code=reason)


def summarize_frame(bundles, publications, *, context, score_spans, tolerances):
    """Return MAIN statistics plus per-request audit diagnostics.

    ``bundles`` is the complete caller-verified frozen frame, each item containing
    ``request`` and original eleven ``records``. ``context`` has frame_sha256,
    model_freeze_sha256, model_id, geometry_fields and N_original_requests.
    Publications contain only members of the MAIN/AUDIT union, at most one per
    candidate; absent entries remain pending, never become failures or zeroes.
    Resource waits use state PENDING and a reason_code (e.g. RESOURCE_WAIT).

    This API accepts small synthetic frames but never calls them FINAL10000.
    MAIN metrics use all original requests as N, while MAE/CDF are conditional
    STRICT_VALID observations. Audit-only candidates do not inflate MAIN N.
    """
    _require(isinstance(context, Mapping), 'context required')
    for key in ('frame_sha256', 'model_freeze_sha256'):
        _sha(context.get(key), key)
    _require(type(context.get('N_original_requests')) is int and context['N_original_requests'] > 0,
             'positive original frame size required')
    _require(isinstance(bundles, (list, tuple)) and len(bundles) == context['N_original_requests'],
             'must supply the entire original frame, not only published or valid requests')
    spans = _vector(score_spans, 'score_spans')
    _require(tuple(spans) == SCORE_SPANS, 'fixed score spans differ')
    tau = _vector(tolerances, 'tolerances')
    _require(all(x > 0 for x in tau), 'positive exact tolerances required')
    routes, slots, request_ids = [], {}, set()
    for order, bundle in enumerate(bundles):
        _require(isinstance(bundle, Mapping) and {'request', 'records'} <= bundle.keys(), 'bundle fields missing')
        route = route_request(bundle['request'], bundle['records'], context['model_id'], context['geometry_fields'])
        _require(route['request_id'] not in request_ids and route['request_order'] == order,
                 'original frame order or unique request identity differs')
        request_ids.add(route['request_id'])
        routes.append(route)
        for slot in route['unique_candidates']:
            _require(slot['candidate_id'] not in slots, 'duplicate logical candidate')
            slots[slot['candidate_id']] = slot
    _require(isinstance(publications, (list, tuple)), 'publications must be a list')
    found = {}
    for source in publications:
        _require(isinstance(source, Mapping), 'publication must be a mapping')
        cid = source.get('candidate_id')
        _require(isinstance(cid, str) and cid in slots, 'foreign/unselected publication candidate')
        _require(cid not in found, 'duplicate publication; main and audit share one result')
        found[cid] = _publication(source, slots[cid], context)
    candidates = {}
    for cid, slot in slots.items():
        original = slot['original_record']
        result = found.get(cid, dict(state=slot['status'], actual=None, evidence_ref=None,
                                    touchstone_sha=None, candidate_geometry_identity_sha256=None,
                                    reason_code=None))
        candidates[cid] = dict(request_id=slot['request_id'], candidate_id=cid,
            q_target=slot['q_target'], q_proxy=slot['q_proxy'],
            target=deepcopy(original['target']), grid_proxy=deepcopy(original['grid_proxy']),
            memberships=slot['memberships'].copy(), frozen_record_sha256=record_sha256(original), **result)
    main_rows, audits, eligible = [], [], []
    for route in routes:
        cid = route['main']['candidate_id']
        if cid is None:
            row = dict(request_id=route['request_id'], candidate_id=None, q_proxy=None,
                       state=NO_SELECTION, target=None, grid_proxy=None, actual=None,
                       strict_joint_hit=None, strict_errors=None, evidence_ref=None, reason_code=NO_SELECTION)
        else:
            row = deepcopy(candidates[cid])
            errors = (_target_errors(row['actual'], row['target'], spans, tau)
                      if row['state'] == 'STRICT_VALID' else None)
            row.update(strict_errors=errors, strict_joint_hit=all(errors['within_tolerance'])
                       if errors is not None else False if row['state'] == 'EMX_INVALID' else None)
            if row['state'] == 'STRICT_VALID':
                eligible.append(row)
        main_rows.append(row)
        if route['full11_audit']:
            audits.append(summarize_audit(route['request_id'], route['q_proxy'],
                [candidates[x['candidate_id']] for x in route['audit_slots']],
                score_spans=spans, tolerances=tau))
    # Reuse proven arithmetic, not its selected-only denominator/status summary.
    metrics = selected_metrics(eligible, score_spans=spans, tolerances=tau)
    n = len(main_rows)
    for row in metrics['metric_rows'] + metrics['ecdf_rows']:
        row['N_original'] = n
    counts = Counter(row['state'] for row in main_rows)
    valid, pending = counts['STRICT_VALID'], counts['PENDING']
    hits = sum(row['strict_joint_hit'] is True for row in main_rows)
    summary = dict(schema='eucap15_final_main_statistics.v1',
        scope='DESCRIPTIVE_CALLER_VERIFIED_FRAME_NOT_PHYSICAL_CHAIN_OR_FINAL_MODEL_QA',
        context=deepcopy(dict(context)), score_spans=spans, absolute_tolerances=tau,
        N_original_requests=n, N_main_selected=n-counts[NO_SELECTION],
        N_pending_requests=pending, N_terminal_requests=n-pending, N_strict_valid=valid,
        N_joint_hit=hits, N_no_selection=counts[NO_SELECTION],
        state_counts={state: counts[state] for state in (*STATES, NO_SELECTION)},
        observed_joint_hit_fraction_original=hits/n,
        completed_joint_hit_fraction_original=hits/n if pending == 0 else None,
        conditional_strict_joint_hit_fraction=hits/valid if valid else None,
        completion_status='PARTIAL_PENDING' if pending else 'COMPLETE_ACCOUNTING',
        N_audit_requests=len(audits), N_original_audit_slots=11*len(audits),
        N_complete_strict_audits=sum(a['q_emx'] is not None for a in audits),
        N_unique_logical_candidates=len(slots), N_published_candidates=len(found),
        native_attempts=None, independent_native_solves=None, cache_hits=None,
        physical_evidence_validation='CALLER_REQUIRED_NOT_PERFORMED_HERE',
        no_success_replacement=True, ci_status='NOT_ESTIMATED',
        fraction_scope='Observed MAIN hits/original N; pending is not completed success. Audit rows never increase MAIN N.',
        native_dispatch_authorized=False, final10000_results_claimed=False)
    return dict(summary=summary, request_rows=main_rows, candidate_rows=list(candidates.values()),
                metric_rows=metrics['metric_rows'], ecdf_rows=metrics['ecdf_rows'], audit_rows=audits)
