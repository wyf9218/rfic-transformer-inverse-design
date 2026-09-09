"""Pure accounting of frozen two-arm solver-start budgets, not native QA.

The caller supplies the complete frozen proposal frame and a closed-cutoff ledger
whose native provenance it has validated. No filesystem, model, solver, resampling
or retry operations occur here. A status tag alone never proves native execution.
Prefixes use explicit chronological solver-start ordinals, checked against the
original per-arm proposal order; never completion time or successful outcomes.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
import math
import re
from numbers import Integral, Real


ARMS = ('COVERAGE_DIRECTED', 'GEOMETRY_DOE_CONTROL')
CHECKPOINTS = (16, 32, 64, 128)
PROPOSALS_PER_ARM = 128
IDENTITY = ('candidate_id', 'arm', 'arm_order', 'global_order')
UNSTARTED = ('NOT_STARTED', 'PRE_NATIVE_PENDING')
PRE_NATIVE = ('HELD_NO_REPLACEMENT', 'ANALYTIC_FAIL', 'GDS_FAIL', 'DRC_FAIL')
SOLVER_TERMINAL = ('STRICT_VALID', 'EMX_INVALID', 'SOLVER_FAIL', 'FEATURE_FAIL')
SOLVER_STATES = (*SOLVER_TERMINAL, 'SOLVER_PENDING')
STATES = (*UNSTARTED, *PRE_NATIVE, *SOLVER_STATES)
PROVENANCE_SCOPE = 'CALLER_MUST_VALIDATE_CLOSED_NATIVE_START_AND_TERMINAL_RECEIPTS'
SERIAL_ORDER = 'SERIAL_CLOSED_NATIVE_EXECUTION_ORDER'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _positive_integer(value, name):
    _require(isinstance(value, Integral) and not isinstance(value, bool) and value > 0,
             name + ': positive integer required')
    return int(value)


def _time(value, name):
    _require(isinstance(value, str) and value, name + ': explicit timezone timestamp required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError(name + ': invalid timestamp') from error
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             name + ': timezone required')
    return parsed.astimezone(timezone.utc)


def _frame(proposals):
    _require(proposals is not None and not isinstance(proposals, (str, bytes, Mapping)),
             'Complete frozen proposal sequence required')
    proposals = list(proposals)
    _require(len(proposals) == 2 * PROPOSALS_PER_ARM, 'Exact 256-proposal frame required')
    result, counts = {}, Counter()
    for position, source in enumerate(proposals, 1):
        _require(isinstance(source, Mapping) and set(IDENTITY) <= source.keys(),
                 'Frozen proposal identity fields missing')
        identity = {key: source[key] for key in IDENTITY}
        candidate = identity['candidate_id']
        _require(isinstance(candidate, str) and candidate and candidate == candidate.strip(),
                 'Exact candidate_id required')
        _require(candidate not in result, 'Duplicate frozen candidate_id')
        arm = identity['arm']
        _require(arm in ARMS, 'Foreign proposal arm')
        counts[arm] += 1
        _require(_positive_integer(identity['global_order'], 'global_order') == position,
                 'Proposals must retain their exact frozen global order')
        _require(_positive_integer(identity['arm_order'], 'arm_order') == counts[arm],
                 'Frozen arm_order must be complete and one-based')
        result[candidate] = identity
    _require(all(counts[arm] == PROPOSALS_PER_ARM for arm in ARMS), 'Exact 128 proposals per arm required')
    return result


def _unknown(reason):
    return dict(schema='eucap15_acquisition_matched_budget.v1', status='UNKNOWN',
        reason=reason, provenance_scope=PROVENANCE_SCOPE,
        source_validation='NOT_PERFORMED_BY_PURE_FUNCTION',
        original_proposals_per_arm=PROPOSALS_PER_ARM, arms=None,
        checkpoints=[dict(m=m, status='UNKNOWN', equal_budget_publishable=False,
                          prefixes=None) for m in CHECKPOINTS],
        coverage_gain=None, superiority_claim='NOT_ESTABLISHED_SINGLE_PAIR')


def _cost(rows):
    values = [row['solver_seconds'] for row in rows]
    return dict(solver_seconds=sum(values) if all(value is not None for value in values) else None,
                solver_seconds_status='REPORTED' if all(value is not None for value in values) else 'UNKNOWN_MISSING_REPORTED_DURATION',
                end_to_end_seconds=None, end_to_end_status='NOT_SUPPLIED_NOT_INFERRED')


def matched_prefixes(proposals, ledger):
    """Return fixed matched checkpoints and original-proposal accounting.

    ``proposals`` is the existing SELECTED_CANDIDATES.jsonl sequence (extra fields
    are ignored); candidate_id/arm/arm_order/global_order are bound exactly.

    ``ledger`` is ``{schema:'eucap15_acquisition_budget_ledger.v1', complete:True,
    observed_utc:ISO8601, rows:[...]}``. Every proposal must appear once. Each row
    contains the four identity fields plus state, solver_start_order (one-based
    global ordinal or null), solver_started_utc (or null), closed_utc (or null),
    and solver_seconds (reported elapsed seconds or explicit null). Solver rows
    require either solver_start_evidence_class='ACTUAL_SOLVER_START' with an
    actual timestamp, or 'SERIAL_CLOSED_NATIVE_EXECUTION_ORDER' with a pinned
    caller-validated execution/order proof. The latter is terminal-only, keeps
    solver_started_utc=null and supplies solver_start_lower_bound_utc; the entire
    ledger must declare solver_execution_mode='SERIAL_SINGLE_OWNER'. All adjacent
    execution envelopes must then be non-overlapping. PID birth time is not
    required for this ordinal proof. A wrapper marker alone is never sufficient.
    The caller,
    not this pure helper, must verify the source evidence behind that assertion.
    Rows may be
    stored in any order; only explicit start order determines the prefix. Complete
    means all starts, terminal states and unstarted proposals are accounted for
    through the cutoff; it does NOT mean all proposals have finished.

    Missing completeness/cutoff/rows/start evidence returns UNKNOWN, never zero.
    Conflicting identities, duplicate starts or reversed chronology fail closed.
    PRE_NATIVE states require no solver start and a closed timestamp; UNSTARTED
    states and SOLVER_PENDING require null terminal/duration. Native/feature
    failures and strict-invalid results all retain their solver-start cost slot.
    Missing durations remain null without blocking an otherwise closed m-prefix.
    """
    frame = _frame(proposals)
    if ledger is None or not isinstance(ledger, Mapping):
        return _unknown('NO_COMPLETE_LEDGER_PROVIDED')
    _require(ledger.get('schema') == 'eucap15_acquisition_budget_ledger.v1', 'Unsupported ledger schema')
    if ledger.get('complete') is not True or not ledger.get('observed_utc') or ledger.get('rows') is None:
        return _unknown('COMPLETENESS_CUTOFF_OR_ROWS_NOT_PROVIDED')
    cutoff = _time(ledger['observed_utc'], 'observed_utc')
    _require(not isinstance(ledger['rows'], (str, bytes, Mapping)), 'Ledger rows must be a sequence')
    raw_rows, seen = list(ledger['rows']), set()
    required = {*IDENTITY, 'state', 'solver_start_order', 'solver_started_utc', 'closed_utc', 'solver_seconds'}
    incomplete = False
    for row in raw_rows:
        if not isinstance(row, Mapping) or not set(IDENTITY) <= row.keys():
            incomplete = True
            continue
        candidate = row['candidate_id']
        _require(candidate in frame and candidate not in seen, 'Foreign or duplicate ledger candidate')
        seen.add(candidate)
        _require(all(type(row[key]) is type(frame[candidate][key]) and row[key] == frame[candidate][key]
                     for key in IDENTITY), 'Ledger proposal identity/order differs from frozen frame')
        incomplete |= not required <= row.keys()
    if incomplete or seen != set(frame):
        return _unknown('MISSING_PROPOSAL_OR_REQUIRED_LEDGER_FIELDS')
    rows, starts = [], []
    serial_order_used = False
    for raw in raw_rows:
        row = {key: raw[key] for key in required}
        state = row['state']
        _require(state in STATES, 'Unknown ledger state')
        start, started, closed, seconds = (row[key] for key in
            ('solver_start_order', 'solver_started_utc', 'closed_utc', 'solver_seconds'))
        if state in SOLVER_STATES:
            evidence_class = raw.get('solver_start_evidence_class')
            if evidence_class not in ('ACTUAL_SOLVER_START', SERIAL_ORDER):
                return _unknown('ACTUAL_SOLVER_START_PROVENANCE_NOT_SUPPLIED')
            if start is None:
                return _unknown('SOLVER_STATE_WITHOUT_EXPLICIT_START_EVIDENCE')
            row['solver_start_order'] = _positive_integer(start, 'solver_start_order')
            if evidence_class == SERIAL_ORDER:
                if ledger.get('solver_execution_mode') != 'SERIAL_SINGLE_OWNER':
                    return _unknown('SERIAL_EXECUTION_MODE_NOT_SUPPLIED')
                if state not in SOLVER_TERMINAL:
                    return _unknown('SERIAL_CLOSED_PROOF_CANNOT_ESTABLISH_PENDING_START')
                _require(started is None, 'Ordinal proof must not invent an exact solver start timestamp')
                proof = raw.get('solver_start_order_proof')
                if not (isinstance(proof, Mapping) and isinstance(proof.get('path'), str)
                        and proof['path'] and isinstance(proof.get('sha256'), str)
                        and re.fullmatch('[0-9a-f]{64}', proof['sha256'])):
                    return _unknown('SERIAL_EXECUTION_ORDER_PROOF_REFERENCE_NOT_SUPPLIED')
                lower = raw.get('solver_start_lower_bound_utc')
                if lower is None:
                    return _unknown('SERIAL_EXECUTION_LOWER_BOUND_NOT_SUPPLIED')
                row['_started'] = _time(lower, 'solver_start_lower_bound_utc')
                serial_order_used = True
            else:
                if started is None:
                    return _unknown('SOLVER_STATE_WITHOUT_EXPLICIT_START_EVIDENCE')
                row['_started'] = _time(started, 'solver_started_utc')
            _require(row['_started'] <= cutoff, 'Start occurs after observation cutoff')
            row['_evidence_class'] = evidence_class
            row['_closed'] = None
            if state == 'SOLVER_PENDING':
                _require(closed is None and seconds is None, 'Pending solver must not carry terminal/cost')
            else:
                if closed is None:
                    return _unknown('TERMINAL_STATE_WITHOUT_EXPLICIT_CLOSURE')
                closed_time = _time(closed, 'closed_utc')
                _require(row['_started'] <= closed_time <= cutoff, 'Invalid native closure chronology')
                row['_closed'] = closed_time
                if seconds is not None:
                    _require(isinstance(seconds, Real) and not isinstance(seconds, bool)
                             and math.isfinite(seconds) and seconds >= 0, 'Reported solver duration must be finite nonnegative or null')
            starts.append(row)
        else:
            _require(start is None and started is None and seconds is None,
                     'Pre-native/unstarted state must not consume a solver-start slot or solver cost')
            if state in UNSTARTED:
                _require(closed is None, 'Unstarted proposal must not carry a terminal time')
            else:
                if closed is None:
                    return _unknown('PRE_NATIVE_FAILURE_OR_HOLD_WITHOUT_CLOSURE')
                _require(_time(closed, 'closed_utc') <= cutoff, 'Pre-native closure after cutoff')
        rows.append(row)
    order = [row['solver_start_order'] for row in starts]
    _require(len(order) == len(set(order)), 'Duplicate solver-start ordinal; no duplicated cost slots')
    starts.sort(key=lambda row: row['solver_start_order'])
    if [row['solver_start_order'] for row in starts] != list(range(1, len(starts) + 1)):
        return _unknown('GAPPED_SOLVER_START_LEDGER')
    for previous, current in zip(starts, starts[1:]):
        _require(previous['_started'] <= current['_started'], 'Solver-start ordinals reverse actual chronology')
        if serial_order_used:
            _require(previous['_closed'] is not None and previous['_closed'] <= current['_started'],
                     'Serial execution proof has overlapping or unclosed prior execution envelopes')
    by_arm = {arm: [row for row in starts if row['arm'] == arm] for arm in ARMS}
    for arm in ARMS:
        for previous, current in zip(by_arm[arm], by_arm[arm][1:]):
            _require(previous['arm_order'] < current['arm_order'],
                     'Solver starts violate frozen per-arm proposal order; do not sort by success/completion')
        if by_arm[arm] and any(row['state'] in UNSTARTED and row['arm'] == arm
                and row['arm_order'] < by_arm[arm][-1]['arm_order'] for row in rows):
            return _unknown('UNRESOLVED_EARLIER_PROPOSAL_IN_STARTED_PREFIX')
    arms = {}
    for arm in ARMS:
        originals = [row for row in rows if row['arm'] == arm]
        counts = Counter(row['state'] for row in originals)
        arms[arm] = dict(original_proposals=PROPOSALS_PER_ARM,
            proposal_state_counts={state: counts[state] for state in STATES},
            pre_native_failures=sum(counts[state] for state in PRE_NATIVE if state != 'HELD_NO_REPLACEMENT'),
            held_no_replacement=counts['HELD_NO_REPLACEMENT'],
            pre_native_failure_denominator=PROPOSALS_PER_ARM,
            solver_starts=len(by_arm[arm]), solver_pending=counts['SOLVER_PENDING'],
            solver_closed=sum(counts[state] for state in SOLVER_TERMINAL),
            **_cost(by_arm[arm]))
    checkpoints = []
    for m in CHECKPOINTS:
        prefixes = {}
        enough = all(len(by_arm[arm]) >= m for arm in ARMS)
        closed = enough and all(row['state'] in SOLVER_TERMINAL for arm in ARMS for row in by_arm[arm][:m])
        status = 'MATCHED_CLOSED_SOLVER_START_PREFIX' if closed else ('PREFIX_PENDING' if enough else 'PREFIX_NOT_REACHED')
        for arm in ARMS:
            prefix = by_arm[arm][:m]
            counts = Counter(row['state'] for row in prefix)
            prefixes[arm] = dict(candidate_ids=[row['candidate_id'] for row in prefix],
                arm_orders=[row['arm_order'] for row in prefix],
                solver_start_orders=[row['solver_start_order'] for row in prefix],
                n_started=len(prefix), n_closed=sum(counts[state] for state in SOLVER_TERMINAL),
                cost_denominator=m if closed else None,
                outcome_counts={state: counts[state] for state in SOLVER_STATES}, **_cost(prefix))
        checkpoints.append(dict(m=m, status=status, equal_budget_publishable=closed, prefixes=prefixes))
    return dict(schema='eucap15_acquisition_matched_budget.v1',
        status='ACCOUNTED_COMPLETE_CUTOFF_LEDGER_NOT_NATIVE_QA', observed_utc=ledger['observed_utc'],
        provenance_scope=PROVENANCE_SCOPE, source_validation='NOT_PERFORMED_BY_PURE_FUNCTION',
        start_evidence_counts=dict(Counter(row['_evidence_class'] for row in starts)),
        exact_solver_start_timestamps_available=all(row['solver_started_utc'] is not None for row in starts),
        original_proposals_per_arm=PROPOSALS_PER_ARM, arms=arms, checkpoints=checkpoints,
        estimand='CONDITIONAL_ON_EMX_ADMISSION_EQUAL_STARTED_ATTEMPT_COST_NOT_END_TO_END_PROPOSAL_EFFICIENCY',
        coverage_gain=None, superiority_claim='NOT_ESTABLISHED_SINGLE_PAIR')
