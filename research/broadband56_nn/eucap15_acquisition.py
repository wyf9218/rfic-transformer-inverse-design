"""Pure, deterministic EuCAP15 acquisition/accounting primitives.

No file I/O, model loading, candidate repair, native execution or owner admission.
Coverage uses real-label TRAIN counts, all Q (not only the Q10..20 intersection).
Selecting an empty cell does not establish physical reachability. Selection weight
is the initial normalized deficit weight, NOT a without-replacement inclusion
probability. Targets remain raw floats; callers own the existing geometry grid.

``coverage_gain`` only accounts for caller-validated, closed real-EM rows. It
cannot establish receipt/S4P/DRC authenticity from an evidence_class string. The
caller must validate those sources and supply the frozen baseline-plus-prior-round
geometry hash ledger. Nothing here grants physical acceptance or training access.
"""
from __future__ import annotations

import itertools
import math
import numbers
import re
from collections.abc import Mapping

import numpy as np


LOWER = (0.5, 0.5, 0.2)
UPPER = (2.0, 2.0, 0.85)
FREQUENCY_HZ = 15_000_000_000
PROVENANCE_SCOPE = 'CALLER_MUST_VALIDATE_CLOSED_SAME_GDS_DRC_EMX_RECEIPTS'
Q_COUNTS = ('N_q_below10', 'N_q10_12', 'N_q12_14', 'N_q14_16',
            'N_q16_18', 'N_q18_20', 'N_q_above20')
COUNTS = ('N_strict_core_all_q', 'N_strict_core_q10_20', *Q_COUNTS)
BOUND_FIELDS = (('lp_low_nh', 'lp_high_nh'), ('ls_low_nh', 'ls_high_nh'),
                ('k_low', 'k_high'))
CELL_FIELDS = ('lp_bin', 'ls_bin', 'k_bin')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _integer(value, name, *, positive=False):
    # CSV integers and true integer objects only; bool/float/UNKNOWN are not counts.
    if isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value):
        value = int(value)
    _require(isinstance(value, numbers.Integral) and not isinstance(value, (bool, np.bool_)),
             f'{name}: nonnegative integer required')
    value = int(value)
    _require(value >= (1 if positive else 0), f'{name}: invalid integer range')
    return value


def _finite(value, name):
    _require(not isinstance(value, (bool, np.bool_)), f'{name}: bool is not a physical value')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f'{name}: finite numeric value required') from error
    _require(math.isfinite(result), f'{name}: finite numeric value required')
    return result


def _boolean(value, name):
    if value == 'True':
        return True
    if value == 'False':
        return False
    _require(type(value) is bool, f'{name}: exact boolean required')
    return value


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None,
             'geometry_hash: canonical lowercase SHA-256 required')
    return value


def _edges(n):
    return tuple(np.linspace(lo, hi, n + 1) for lo, hi in zip(LOWER, UPPER))


def validate_coverage(rows):
    """Return fresh normalized rows from an exact ordered 8^3 train/15GHz grid.

    Accepts the existing coverage_before.csv field names (CSV strings or numeric
    objects). Pass only its 8^3 rows, not the appended 4^3 diagnostic grid. Bounds
    must equal the original float64 linspace values, not rounded approximations.
    """
    _require(rows is not None, 'Coverage is unavailable; UNKNOWN is not zero')
    rows = list(rows)
    _require(len(rows) == 512, 'Complete ordered 512-cell coverage required')
    edges, result = _edges(8), []
    required = {'grid_n', 'split', 'frequency_hz', *CELL_FIELDS, *COUNTS,
                *(field for pair in BOUND_FIELDS for field in pair)}
    for expected, original in zip(itertools.product(range(8), repeat=3), rows):
        _require(isinstance(original, Mapping) and required <= original.keys(),
                 'Coverage row is missing required fields')
        row = dict(original)
        _require(row['split'] == 'train', 'Only train coverage is permitted')
        row['grid_n'] = _integer(row['grid_n'], 'grid_n')
        row['frequency_hz'] = _integer(row['frequency_hz'], 'frequency_hz')
        _require(row['grid_n'] == 8 and row['frequency_hz'] == FREQUENCY_HZ,
                 'Coverage must be exact 8^3 and 15GHz')
        cell = tuple(_integer(row[key], key) for key in CELL_FIELDS)
        _require(cell == expected, 'Cells must be unique, complete and lexicographically ordered')
        row.update(zip(CELL_FIELDS, cell))
        for j, pair in enumerate(BOUND_FIELDS):
            for offset, key in enumerate(pair):
                row[key] = _finite(row[key], key)
                _require(row[key] == float(edges[j][cell[j] + offset]),
                         f'{key}: exact frozen linspace boundary required')
        for key in COUNTS:
            row[key] = _integer(row[key], key)
        total, intersection = row[COUNTS[0]], row[COUNTS[1]]
        _require(sum(row[key] for key in Q_COUNTS[1:-1]) == intersection,
                 'Q10..20 strata must sum to the Q intersection')
        _require(sum(row[key] for key in Q_COUNTS) == total and intersection <= total,
                 'All Q strata including tails must sum to all-Q train N')
        if 'empty_all_q' in row:
            row['empty_all_q'] = _boolean(row['empty_all_q'], 'empty_all_q')
            _require(row['empty_all_q'] == (total == 0), 'Inconsistent empty-cell flag')
        if 'unobserved_is_not_proven_unreachable' in row:
            _require(_boolean(row['unobserved_is_not_proven_unreachable'], 'reachability flag'),
                     'Empty cells must not be declared unreachable')
            row['unobserved_is_not_proven_unreachable'] = True
        result.append(row)
    return result


def sparse_targets(rows, n, seed):
    """Sample n distinct deficit cells, then one uniform [Lp,Ls,|k|] per cell.

    One PCG64 generator; one weighted choice(replace=False), followed in selected
    order by one 3-vector uniform draw per cell. d=max(5-N_all_Q,0). No optimizer,
    rejection-by-proxy, duplicate-cell filling or implicit selection retry.
    """
    rows = validate_coverage(rows)
    n, seed = _integer(n, 'n', positive=True), _integer(seed, 'seed')
    deficits = np.asarray([max(5 - row[COUNTS[0]], 0) for row in rows], dtype=np.float64)
    _require(n <= int(np.count_nonzero(deficits)), 'Not enough distinct sparse cells')
    weights = deficits / deficits.sum()
    rng = np.random.Generator(np.random.PCG64(seed))
    chosen = rng.choice(len(rows), size=n, replace=False, p=weights)
    result = []
    for order, index in enumerate(chosen):
        row = rows[int(index)]
        low = [row[pair[0]] for pair in BOUND_FIELDS]
        high = [row[pair[1]] for pair in BOUND_FIELDS]
        result.append(dict(order=order, target=rng.uniform(low, high).tolist(),
            cell=[row[key] for key in CELL_FIELDS], N=row[COUNTS[0]],
            deficit=int(deficits[index]), selection_weight=float(weights[index])))
    return result


def geometry_lhs(lower, upper, n, seed):
    """Return unscreened n x 10 raw geometry floats, using the existing lhs3 formula."""
    n, seed = _integer(n, 'n', positive=True), _integer(seed, 'seed')
    lower, upper = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    _require(lower.shape == upper.shape == (10,), 'Exactly ten geometry dimensions required')
    _require(np.isfinite(lower).all() and np.isfinite(upper).all() and (upper > lower).all(),
             'Finite strictly ordered geometry bounds required')
    rng = np.random.Generator(np.random.PCG64(seed))
    unit = np.column_stack([(rng.permutation(n) + rng.random(n)) / n for _ in range(10)])
    return lower + (upper - lower) * unit


def actual_landing(values, n=8):
    """Return cell tuple for actual [Lp,Ls,|k|], or None if nonfinite/outside.

    Internal edges belong to the upper bin; exact final upper edges belong to the
    last bin. No epsilon, clipping, Q filtering or strict-validity inference.
    """
    n = _integer(n, 'n', positive=True)
    values = np.asarray(values, dtype=float)
    _require(values.shape == (3,), 'Landing requires actual [Lp,Ls,|k|]')
    if not np.isfinite(values).all() or np.any(values < LOWER) or np.any(values > UPPER):
        return None
    cell = []
    for value, edge in zip(values, _edges(n)):
        cell.append(n - 1 if value == edge[-1] else int(np.searchsorted(edge, value, side='right') - 1))
    return tuple(cell)


def _actual_row(original):
    fields = {'geometry_hash', 'split', 'frequency_hz', 'strict_lumped_valid', 'evidence_class', 'actual'}
    _require(isinstance(original, Mapping) and fields <= original.keys(), 'Incomplete real-EM training row')
    row = dict(original)
    row['geometry_hash'] = _hash(row['geometry_hash'])
    _require(row['split'] == 'train', 'Validation/test responses cannot become train coverage')
    row['frequency_hz'] = _integer(row['frequency_hz'], 'frequency_hz')
    _require(row['frequency_hz'] == FREQUENCY_HZ, 'Only exact 15GHz rows allowed')
    _require(row['strict_lumped_valid'] is True, 'Explicit strict-valid row required')
    _require(row['evidence_class'] == 'FRESH_REAL_EMX', 'Proxy/unknown responses are not real-EM coverage')
    _require(isinstance(row['actual'], (list, tuple, np.ndarray)) and len(row['actual']) == 4,
             'Four actual values [Lp_nH,Ls_nH,Qmin,|k|] required')
    row['actual'] = [_finite(value, 'actual') for value in row['actual']]
    return row


def coverage_gain(before, actual_unique_training_rows, *, baseline_hashes, synthetic=False):
    """Account unique new actual rows against frozen train/prior-round identities.

    baseline_hashes is required even when no publications are available. Empty
    baseline is allowed only for explicit synthetic fixtures. None rows means
    UNKNOWN (null gains); [] means an explicitly supplied empty admitted cohort,
    not a successful native round. The caller must prove source/split/admission
    and that the baseline hash set corresponds to ``before``; counts alone cannot.
    Same-hash identical physical rows count once; conflicting observations fail.
    All unique rows, including existing identities and novel out-of-domain rows,
    are returned with accounting disposition. No attempt/pass rate is inferred.
    """
    before = validate_coverage(before)
    _require(type(synthetic) is bool, 'Explicit boolean synthetic scope required')
    _require(baseline_hashes is not None and not isinstance(baseline_hashes, (str, bytes, Mapping)),
             'Frozen baseline/prior-round geometry hash collection required')
    baseline = {_hash(value) for value in baseline_hashes}
    _require(baseline or synthetic, 'Empty baseline is allowed only in explicit synthetic fixtures')
    base_counts = [row[COUNTS[0]] for row in before]
    common = dict(provenance_scope=PROVENANCE_SCOPE,
        evidence_scope='SYNTHETIC_ONLY' if synthetic else 'CALLER_DECLARED_REAL_EM_NOT_INDEPENDENTLY_VALIDATED',
        baseline_hash_count=len(baseline), original_cells=512, population=COUNTS[0],
        split='train', frequency_hz=FREQUENCY_HZ,
        occupied_before=sum(value > 0 for value in base_counts),
        source_validation='NOT_PERFORMED_BY_PURE_FUNCTION',
        baseline_row_membership_validation='CALLER_RESPONSIBILITY_NOT_PROVABLE_FROM_BIN_COUNTS')
    if actual_unique_training_rows is None:
        return dict(common, status='UNKNOWN_NO_CLOSED_PUBLICATIONS_PROVIDED',
            counts=None, per_cell=None, after_coverage=None, retained_unique_rows=None,
            occupied_after=None, newly_occupied_cells=None, occupancy_fraction_gain=None,
            sparse_crossed_5=None)
    observed, input_count = {}, 0
    for original in actual_unique_training_rows:
        row = _actual_row(original)
        input_count += 1
        identity = row['geometry_hash']
        if identity in observed:
            _require(row['actual'] == observed[identity]['actual'], 'Conflicting actual values for one geometry hash')
        else:
            observed[identity] = row
    after = [dict(row) for row in before]
    retained, existing, novel, outside = [], 0, 0, 0
    for identity, row in observed.items():
        cell = actual_landing([row['actual'][j] for j in (0, 1, 3)])
        row = dict(row, actual_cell=list(cell) if cell is not None else None)
        if identity in baseline:
            existing += 1
            row['accounting_disposition'] = 'EXISTING_BASELINE_OR_PRIOR_ROUND_NOT_ADDED'
        else:
            novel += 1
            if cell is None:
                outside += 1
                row['accounting_disposition'] = 'NEW_OUT_OF_DOMAIN_RETAINED_NOT_CLIPPED'
            else:
                row['accounting_disposition'] = 'NEW_IN_DOMAIN_ADDED'
                index = cell[0] * 64 + cell[1] * 8 + cell[2]
                target = after[index]
                target[COUNTS[0]] += 1
                q = row['actual'][2]
                if q < 10:
                    q_key = Q_COUNTS[0]
                elif q > 20:
                    q_key = Q_COUNTS[-1]
                else:
                    q_key = Q_COUNTS[min(int((q - 10) // 2), 4) + 1]
                    target[COUNTS[1]] += 1
                target[q_key] += 1
                if 'empty_all_q' in target:
                    target['empty_all_q'] = False
        retained.append(row)
    after = validate_coverage(after)
    per_cell = [dict(cell=[row[key] for key in CELL_FIELDS], before=old[COUNTS[0]],
        added=row[COUNTS[0]] - old[COUNTS[0]], after=row[COUNTS[0]],
        added_q10_20=row[COUNTS[1]] - old[COUNTS[1]]) for old, row in zip(before, after)]
    newly_occupied = sum(item['before'] == 0 and item['after'] > 0 for item in per_cell)
    return dict(common, status='ACCOUNTED_CALLER_SUPPLIED_ROWS_NOT_NATIVE_ROUND_ACCEPTANCE',
        counts=dict(input_rows=input_count, unique_geometry=len(observed),
            duplicate_rows=input_count - len(observed), existing_geometry=existing,
            new_geometry=novel, new_in_domain=novel - outside, new_out_of_domain=outside),
        per_cell=per_cell, after_coverage=after, retained_unique_rows=retained,
        occupied_after=common['occupied_before'] + newly_occupied,
        newly_occupied_cells=newly_occupied, occupancy_fraction_gain=newly_occupied / 512,
        sparse_crossed_5=sum(item['before'] < 5 <= item['after'] for item in per_cell))
