"""Pure logical routing of one frozen FINAL-frame request, never dispatch.

The caller must verify the FINAL model/frame/source pins and original geometry
contract. This helper checks saved Q-scan rows, not models or physical evidence.
MAIN and AUDIT memberships share a request-Q candidate, never a second solve.
No cross-request geometry cache or success replacement is performed.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import math
import struct

import numpy as np

from .frequency_qscan import Q_VALUES, q_targets, score, select_q


_UNRUN = {
    'evidence_source': 'SELF_PROXY', 'parameter_identity_not_actual_gds': True,
    'actual_gds_geometry': None, 'gds_sha256': None, 'cadence_status': 'NOT_RUN',
    'calibre_blocking_count': None, 'emx_status': 'NOT_RUN', 's4p_sha256': None,
    'actual_response': None, 'emx_minus_target': None, 'emx_minus_proxy': None,
    'unique_emx_solve': False,
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _vector(value, size, name, *, nullable=False):
    _require(isinstance(value, list) and len(value) == size, name + ': wrong dimension')
    _require(all(_number(x) or (nullable and x is None) for x in value),
             name + ': finite number or explicit null required')
    return np.asarray([np.nan if x is None else x for x in value], dtype=float)


def _same_float(left, right):
    """Compare exact float64 encodings, never tolerance-correct target values."""
    return _number(left) and struct.pack('>d', float(left)) == struct.pack('>d', float(right))


def route_request(request, records, model_id, geometry_fields):
    """Return main, audit_slots and their unique logical candidate union.

    Inputs are a frozen three-target request and the original ordered eleven
    ``frequency_qscan._batch`` records. Extra fields are retained, not treated as
    authority. ``full11_audit`` is the caller's pre-inference frozen membership.
    ``preselected_emx`` from the legacy producer is deliberately not a route flag.

    Every returned status is preparation-only. Incomplete scores leave MAIN
    unselected while an audited request retains all eleven AUDIT slots; this may
    need eleven extra audit slots, not the planned ten. The helper authorizes no
    native work, even when its per-request logical budget is within ten.
    """
    _require(isinstance(request, Mapping), 'request must be a mapping')
    needed = {'request_id', 'lp_nh', 'ls_nh', 'k_abs', 'full11_audit', 'request_order'}
    _require(needed <= request.keys(), 'frozen request fields missing')
    rid = request['request_id']
    _require(isinstance(rid, str) and rid and rid.strip() == rid, 'exact request_id required')
    _require(type(request['full11_audit']) is bool, 'full11_audit must be bool')
    _require(type(request['request_order']) is int and request['request_order'] >= 0,
             'request_order must be a nonnegative integer')
    _require(isinstance(model_id, str) and model_id and model_id.strip() == model_id,
             'exact frozen model_id required')
    _require(isinstance(geometry_fields, (list, tuple)) and len(geometry_fields) == 10
             and all(isinstance(f, str) and f for f in geometry_fields)
             and len(set(geometry_fields)) == 10, 'exact ten unique geometry fields required')
    fields = list(geometry_fields)
    triple = [request[k] for k in ('lp_nh', 'ls_nh', 'k_abs')]
    _require(all(_number(x) and x > 0 for x in triple), 'positive finite target triple required')
    targets = q_targets(triple)
    _require(isinstance(records, (list, tuple)) and len(records) == 11,
             'exact original eleven records required')
    saved = deepcopy(list(records))
    proxies = []
    context = None
    for q, row, target in zip(Q_VALUES, saved, targets):
        _require(isinstance(row, Mapping), 'candidate row must be a mapping')
        _require(row.get('request_id') == rid and row.get('model_id') == model_id,
                 'candidate request/model identity differs')
        _require(type(row.get('q_target')) is int and row['q_target'] == q
                 and row.get('candidate_id') == f'{rid}-q{q:02d}',
                 'exact ordered candidate-Q identity required')
        _require(row.get('frequency_ghz') == 15 and type(row['frequency_ghz']) in (int, float),
                 'exact 15 GHz candidate required')
        _require(row.get('geometry_fields') == fields, 'geometry field order differs')
        identity = (row.get('target_source'), row.get('dataset_scope'))
        _require(all(isinstance(v, str) and v for v in identity), 'candidate source/scope missing')
        if context is None:
            context = identity
        _require(identity == context, 'mixed source/scope within one request')
        if 'target_source' in request:
            _require(request['target_source'] == identity[0], 'request target_source differs')
        if 'candidate_id_sha256' in row:
            _require(row['candidate_id_sha256'] == hashlib.sha256(row['candidate_id'].encode()).hexdigest(),
                     'candidate ID SHA differs')
        _vector(row.get('target'), 4, 'target')
        _require(all(_same_float(a, b) for a, b in zip(row['target'], target)),
                 'target float64 identity differs; no clipping or tolerance matching')
        for key, expected in _UNRUN.items():
            _require(key in row and row[key] == expected
                     and (not isinstance(expected, bool) or type(row[key]) is bool),
                     'row is not untouched SELF_PROXY: ' + key)
        for key in ('actual', 'q_emx', 'touchstone_sha', 'strict_joint_hit'):
            _require(row.get(key) is None, 'pre-existing physical result: ' + key)
        _require(type(row.get('analytic_grid')) is bool and type(row.get('analytic_raw')) is bool,
                 'original analytic flags must be bool')
        grid = _vector(row.get('grid_geometry'), 10, 'grid_geometry', nullable=True)
        _vector(row.get('continuous_geometry'), 10, 'continuous_geometry', nullable=True)
        _require(not row['analytic_grid'] or bool(np.isfinite(grid).all()),
                 'analytic PASS cannot have nonfinite grid geometry')
        proxies.append(_vector(row.get('grid_proxy'), 4, 'grid_proxy', nullable=True))
    with np.errstate(over='ignore', invalid='ignore'):
        scores = score(np.asarray(proxies), targets)
    selection = select_q(scores)
    q_proxy = selection['q_proxy']
    for q, row, expected in zip(Q_VALUES, saved, scores):
        value = row.get('grid_proxy_score')
        _require(('grid_proxy_score' in row) and
                 (_same_float(value, expected) if np.isfinite(expected) else value is None),
                 'saved grid proxy score differs from original formula')
        _require('q_proxy' in row and row['q_proxy'] == q_proxy
                 and (q_proxy is None or type(row['q_proxy']) is int),
                 'q_proxy differs from complete-scan selection')
        _require(type(row.get('proxy_preselected')) is bool
                 and row['proxy_preselected'] == (q == q_proxy),
                 'preselection differs; no analytic-first reranking')

    common = dict(request_id=rid, request_order=request['request_order'], model_id=model_id,
                  frequency_ghz=15, q_proxy=q_proxy, q_emx=None)
    unique = []
    audit_slots = []
    main = dict(**common, candidate_id=None, q_target=None,
                status='NO_SELECTION_INCOMPLETE_PROXY_SCAN', original_denominator=1)
    for row in saved:
        is_main = row['q_target'] == q_proxy
        members = (['MAIN'] if is_main else []) + (['AUDIT'] if request['full11_audit'] else [])
        if not members:
            continue
        status = 'PENDING' if row['analytic_grid'] else 'ANALYTIC_FAIL'
        slot = dict(**common, candidate_id=row['candidate_id'], q_target=row['q_target'],
                    status=status, analytic_grid=row['analytic_grid'])
        unique.append(dict(**slot, memberships=members, original_record=row,
                           native_dispatch_authorized=False))
        if is_main:
            main = dict(**slot, original_denominator=1)
        if request['full11_audit']:
            audit_slots.append(dict(**slot, original_denominator=11))
    additional = len(audit_slots) - int(request['full11_audit'] and q_proxy is not None)
    extra_budget = additional > 10
    return dict(schema='eucap15_final_request_routing.v1',
        status='LOGICAL_ROUTING_ONLY_NOT_NATIVE_AUTHORIZATION', **common,
        full11_audit=request['full11_audit'], main=main, audit_slots=audit_slots,
        unique_candidates=unique, additional_audit_slots=additional,
        N_original_main_requests=1, N_original_proxy_candidates=11,
        N_original_audit_slots=len(audit_slots), N_unique_logical_candidates=len(unique),
        full11_physical_status='NOT_EVALUATED', independent_solves=0, native_started=False,
        native_dispatch_authorized=False, no_success_replacement=True,
        cross_request_geometry_cache=False, complete_proxy_scan=selection['full_proxy_scan'],
        N_proxy_finite=selection['N_proxy_finite'],
        budget=dict(planned_additional_slots=10 if request['full11_audit'] else 0,
                    additional_audit_slots=additional, requires_extra_audit_budget=extra_budget,
                    status='EXTRA_AUDIT_SLOT_BUDGET_NOT_AUTHORIZED' if extra_budget else
                           'WITHIN_LOGICAL_PER_REQUEST_PLAN_NOT_NATIVE_AUTHORIZATION',
                    campaign_additional_1000_limit_checked=False))
