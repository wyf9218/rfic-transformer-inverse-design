"""Descriptive full-eleven-Q audit for caller-verified candidate evidence.

Pure arithmetic only: no file reads, model, native execution, source validation,
confidence interval or cross-request pooling. Q is a symmetric response target,
not a lower bound or an objective to maximize. Each Q has its own target vector.
The frozen preselection is an input, never replaced by a surviving candidate.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import math
from pathlib import PurePosixPath
import re
import struct

import numpy as np

from .frequency_qscan import Q_VALUES, SCALE, score, select_q
from .frequency_physical_statistics import percentage
from .eucap15_selected_metrics import FEATURES, UNITS, SCORE_SPANS


STATES = ('PENDING', 'ANALYTIC_FAIL', 'GDS_FAIL', 'DRC_FAIL', 'SOLVER_FAIL',
          'FEATURE_FAIL', 'EMX_INVALID', 'STRICT_VALID')
_REQUIRED = {'candidate_id', 'q_target', 'target', 'grid_proxy', 'state', 'actual', 'evidence_ref'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _vector(value, name, *, nullable=False, nonfinite=False):
    if nullable and value is None:
        return None
    _require(isinstance(value, (list, tuple)) and len(value) == 4,
             name + ': exact four-vector required')
    result = []
    for v in value:
        _require((nullable and v is None) or type(v) in (int, float),
                 name + ': numbers or allowed nulls, never bool/string')
        if v is None:
            result.append(None)
            continue
        try:
            v = float(v)
        except OverflowError as error:
            raise ValueError(name + ': numeric range exceeded') from error
        _require(nonfinite or math.isfinite(v), name + ': finite values required')
        # Invalid native rows may be cleaned from nonfinite values, never zero-filled.
        result.append(v if math.isfinite(v) else None)
    return result


def _finite(vector):
    return vector is not None and all(v is not None and math.isfinite(v) for v in vector)


def _pin(value):
    if value is None:
        return None
    _require(isinstance(value, Mapping) and {'path', 'sha256', 'bytes'} <= value.keys(),
             'evidence_ref must be a pin or null')
    path = value['path']
    _require(isinstance(path, str) and PurePosixPath(path).is_absolute()
             and '..' not in PurePosixPath(path).parts and str(PurePosixPath(path)) == path,
             'evidence_ref requires exact absolute path')
    _require(isinstance(value['sha256'], str) and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None
             and type(value['bytes']) is int and value['bytes'] > 0,
             'evidence_ref requires SHA256 and positive byte count')
    return deepcopy(dict(value))


def _exact_float(a, b):
    return struct.pack('>d', a) == struct.pack('>d', b)


def _target_errors(actual, target, spans, tolerances):
    if not _finite(actual):
        return None
    residual = [a-t for a, t in zip(actual, target)]
    _require(all(math.isfinite(e) for e in residual), 'target residual exceeds finite range')
    with np.errstate(over='ignore', invalid='ignore'):
        value = float(score(np.asarray(actual), np.asarray(target)))
    _require(math.isfinite(value), 'normalized response score exceeds finite range')
    percents = [percentage(a, t, s) for a, t, s in zip(actual, target, spans)]
    _require(all(p is None or math.isfinite(p) for p, _ in percents), 'percentage exceeds finite range')
    return dict(emx_minus_target=residual, absolute_error=[abs(e) for e in residual],
        normalized_signed_error=[e/s for e, s in zip(residual, spans)],
        normalized_absolute_error=[abs(e)/s for e, s in zip(residual, spans)],
        normalized_response_score=value,
        within_tolerance=[abs(e) <= t for e, t in zip(residual, tolerances)],
        target_relative_absolute_percent=[p for p, _ in percents],
        target_relative_percent_status=[status for _, status in percents])


def _proxy_errors(actual, proxy):
    if not _finite(actual) or proxy is None:
        return None
    result = [a-p if p is not None else None for a, p in zip(actual, proxy)]
    _require(all(e is None or math.isfinite(e) for e in result), 'proxy residual exceeds finite range')
    return result


def summarize_audit(request_id, q_proxy, candidates, *, score_spans, tolerances):
    """Summarize exactly eleven original Q slots without asserting native QA.

    A STRICT_VALID row requires finite actuals and a non-null evidence pin. Pins
    are shape-checked and retained, not opened or independently authenticated.
    EMX_INVALID may retain null/nonfinite components and finite descriptive
    diagnostics, which never enter strict errors, hit counts, or optimum search.
    Other states require null actuals. Extra input fields are allowed; request_id
    and q_proxy, when present on a row, must agree with the frozen arguments.

    q_proxy is caller-verified preselection, possibly null: it is not derived or
    repaired here from either proxy values or EM outcomes. q_emx uses the existing
    score/select functions only when all original eleven are STRICT_VALID. Regret
    is E(q_proxy)-E(q_emx), not a Q difference; absent preselection stays absent.
    """
    _require(isinstance(request_id, str) and request_id and request_id.strip() == request_id,
             'exact request_id required')
    _require(q_proxy is None or (type(q_proxy) is int and q_proxy in Q_VALUES),
             'q_proxy must be frozen integer10..20 or null')
    spans = _vector(score_spans, 'score_spans')
    _require(tuple(spans) == SCORE_SPANS and spans == SCALE.tolist(),
             'frozen score spans must be [2.5,2.5,20,0.8]')
    tau = _vector(tolerances, 'tolerances')
    _require(all(t > 0 for t in tau), 'positive exact tolerance floats required')
    _require(isinstance(candidates, (list, tuple)) and len(candidates) == 11,
             'exact eleven original candidate rows required')
    by_q, triple = {}, None
    for source in candidates:
        _require(isinstance(source, Mapping) and _REQUIRED <= source.keys(), 'candidate fields missing')
        q = source['q_target']
        _require(type(q) is int and q in Q_VALUES and q not in by_q, 'missing/duplicate/foreign Q slot')
        _require(source['candidate_id'] == f'{request_id}-q{q:02d}', 'candidate-request-Q identity differs')
        if 'request_id' in source:
            _require(source['request_id'] == request_id, 'row request_id differs')
        if 'q_proxy' in source:
            _require(source['q_proxy'] == q_proxy and
                     (q_proxy is None or type(source['q_proxy']) is int), 'row frozen q_proxy differs')
        state = source['state']
        _require(isinstance(state, str) and state in STATES, 'unknown candidate state')
        target = _vector(source['target'], 'target')
        _require(all(v > 0 for v in target) and target[2] == q, 'positive target and exact own target Q required')
        current = [target[j] for j in (0, 1, 3)]
        if triple is None:
            triple = current
        _require(all(_exact_float(a, b) for a, b in zip(current, triple)),
                 'all Q targets must retain the exact same Lp/Ls/K triple')
        proxy = _vector(source['grid_proxy'], 'grid_proxy', nullable=True)
        evidence = _pin(source['evidence_ref'])
        if state == 'STRICT_VALID':
            actual = _vector(source['actual'], 'STRICT_VALID actual')
            _require(evidence is not None, 'STRICT_VALID needs caller-verified evidence reference')
        elif state == 'EMX_INVALID':
            actual = _vector(source['actual'], 'EMX_INVALID actual', nullable=True, nonfinite=True)
            _require(actual is None or evidence is not None, 'reported invalid actual needs evidence reference')
        else:
            _require(source['actual'] is None, 'failed/pending stage cannot carry actual labels')
            actual = None
        errors = _target_errors(actual, target, spans, tau)
        strict_errors = errors if state == 'STRICT_VALID' else None
        by_q[q] = dict(request_id=request_id, candidate_id=source['candidate_id'], q_target=q,
            q_proxy=q_proxy, state=state, target=target, grid_proxy=proxy, actual=actual,
            evidence_ref=evidence, evidence_validation='CALLER_ASSERTION_PIN_NOT_READ_HERE',
            strict_errors=strict_errors,
            emx_minus_grid_proxy=_proxy_errors(actual, proxy) if state == 'STRICT_VALID' else None,
            emx_score=strict_errors['normalized_response_score'] if strict_errors is not None else None,
            strict_joint_hit=all(strict_errors['within_tolerance']) if strict_errors is not None else
                             (False if state == 'EMX_INVALID' else None),
            invalid_diagnostic_errors=errors if state == 'EMX_INVALID' else None,
            invalid_diagnostic_scope='NOT_STRICT_COMPARISON' if state == 'EMX_INVALID' else None)
    _require(set(by_q) == set(Q_VALUES), 'all original Q10..20 slots required')
    rows = [by_q[q] for q in Q_VALUES]
    counts = Counter(row['state'] for row in rows)
    full11 = counts['STRICT_VALID'] == 11
    q_emx = None
    if full11:
        # Never supply a survivor-only vector or sentinel-filled failures.
        q_emx = select_q(np.asarray([row['emx_score'] for row in rows]))['q_proxy']
        _require(q_emx is not None, 'complete strict audit must have eleven finite scores')

    def diagnostic(q, role):
        if q is None:
            return dict(role=role, q=None, candidate_id=None, availability='NOT_AVAILABLE',
                        state=None, actual=None, strict_errors=None, emx_score=None,
                        strict_joint_hit=None, evidence_ref=None)
        row = deepcopy(by_q[q])
        row.update(role=role, q=q, availability='STRICT_VALID' if row['state'] == 'STRICT_VALID'
                   else 'INVALID_DIAGNOSTIC_ONLY' if row['state'] == 'EMX_INVALID'
                   else 'NOT_AVAILABLE')
        return row

    fixed = diagnostic(15, 'FIXED_Q15')
    preselected = diagnostic(q_proxy, 'FROZEN_Q_PROXY')
    optimum = diagnostic(q_emx, 'COMPLETE11_Q_EMX')
    regret = preselected['emx_score'] - optimum['emx_score'] if q_emx is not None and q_proxy is not None else None
    comparison = (fixed['emx_score'] - preselected['emx_score']
                  if fixed['emx_score'] is not None and preselected['emx_score'] is not None else None)
    hits = sum(row['strict_joint_hit'] is True for row in rows)
    return dict(schema='eucap15_final_full11_audit.v1', request_id=request_id,
        scope='DESCRIPTIVE_CALLER_VERIFIED_ROWS_NOT_INDEPENDENT_NATIVE_QA',
        feature_order=list(FEATURES), feature_units=list(UNITS), score_spans=spans, absolute_tolerances=tau,
        q_proxy=q_proxy, q_emx=q_emx, selection_regret=regret,
        selection_regret_definition='E(q_proxy)-E(q_emx), each candidate against its own target(q); dimensionless, not Q difference or percent',
        selection_agreement=(q_proxy == q_emx) if q_proxy is not None and q_emx is not None else None,
        complete11_status='PASS_ALL_ORIGINAL_ELEVEN_STRICT_VALID' if full11 else 'NOT_AVAILABLE_INCOMPLETE_OR_INVALID',
        N_original_candidates=11, N_strict_valid=counts['STRICT_VALID'], N_pending=counts['PENDING'],
        N_terminal=11-counts['PENDING'], state_counts={state: counts[state] for state in STATES},
        N_strict_joint_hit=hits, observed_hit_fraction_original=hits/11,
        conditional_strict_hit_fraction=hits/counts['STRICT_VALID'] if counts['STRICT_VALID'] else None,
        completed_hit_fraction_original=hits/11 if not counts['PENDING'] else None,
        observed_fraction_scope='Confirmed strict hits / original11; pending is not completed success',
        candidate_rows=rows, fixed_q15=fixed, preselected_q_proxy=preselected, emx_selected_q_emx=optimum,
        fixed_q15_minus_preselected_score=comparison,
        fixed_vs_proxy_scope='Same-request descriptive comparison using different own-Q targets; no new native execution or causal claim',
        q_proxy_verification='Caller must verify frozen preselection and source pins; not reselected here',
        N_request_groups=1, ci_status='NOT_ESTIMATED', ci=None, source_validation='NOT_PERFORMED_BY_PURE_FUNCTION',
        native_attempts=None, independent_native_solves=None, cache_hits=None,
        repeated_diagnostics_are_new_solves=False, no_success_replacement=True)
