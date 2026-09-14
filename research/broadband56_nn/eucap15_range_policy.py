"""Versioned response eligibility, separate from strict/SRF and geometry gates.

An unbounded acceptance domain is not a coverage grid or a model support claim.
Coverage bounds must be frozen from finite train observations, never holdout.
"""
from __future__ import annotations

import math

LEGACY_POLICY = 'EUCAP15_LP_LS_0P5_2P0_K_0P2_0P85_V1'
POLICY = 'EUCAP15_POSITIVE_FINITE_LP_LS_K_0P2_0P85_V2'
POLICY_IDS = (POLICY, LEGACY_POLICY)
FEATURES = ('lp_nh', 'ls_nh', 'qmin', 'k_abs')


def make_range_policy(policy_id=POLICY):
    if policy_id not in POLICY_IDS:
        raise ValueError('unknown 15GHz response range policy')
    legacy = policy_id == LEGACY_POLICY
    return dict(schema='eucap15_physical_range_policy.v1', policy_id=policy_id,
        frequency_hz=15_000_000_000,
        lp_ls=dict(finite=True, strictly_positive=True,
                   lower_nh=.5 if legacy else None, upper_nh=2. if legacy else None),
        k_abs=dict(finite=True, lower=.2, upper=.85, inclusive=True),
        qmin=dict(finite=True, lower=None, upper=None, definition='min(qp,qs)'),
        strict_srf_gate='REQUIRED_UNCHANGED_SEPARATE_EVIDENCE',
        geometry_bounds='UNCHANGED_SOURCE_CONTRACT',
        coverage_bounds='FINITE_TRAIN_OBSERVATIONS_ONLY_SEPARATE_VERSION',
        inference_q_scan='INTEGER_10_TO_20_NOT_A_POOL_Q_FILTER')


def resolve_range_policy(value=None, *, legacy_if_missing=False):
    """Missing fields in preserved old configs mean the original finite window."""
    if value is None:
        return make_range_policy(LEGACY_POLICY if legacy_if_missing else POLICY)
    if isinstance(value, str):
        return make_range_policy(value)
    if not isinstance(value, dict) or value != make_range_policy(value.get('policy_id')):
        raise ValueError('range policy fields differ from the named version')
    return dict(value)


def classify_physical15(physical, policy=None):
    """Classify only response range; a pass does not certify strict/SRF/DRC."""
    policy = resolve_range_policy(policy)
    values = {}
    reasons = []
    for key in FEATURES:
        try:
            if isinstance(physical[key], bool):
                raise ValueError('boolean is not a physical value')
            value = float(physical[key])
            if not math.isfinite(value):
                raise ValueError('nonfinite')
            values[key] = value
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.append(key + ':MISSING_OR_NONFINITE')
    for key in ('lp_nh', 'ls_nh'):
        if key not in values:
            continue
        if values[key] <= 0:
            reasons.append(key + ':NOT_POSITIVE')
        window = policy['lp_ls']
        if window['lower_nh'] is not None and not window['lower_nh'] <= values[key] <= window['upper_nh']:
            reasons.append(key + ':OUTSIDE_LEGACY_WINDOW')
    if 'k_abs' in values and not .2 <= values['k_abs'] <= .85:
        reasons.append('k_abs:OUTSIDE_0P2_0P85')
    return dict(policy_id=policy['policy_id'], range_eligible=not reasons, reasons=reasons,
                strict_eligibility='NOT_DECIDED_BY_RANGE_HELPER')


def train_coverage_bounds(train_rows, *, features=('lp_nh', 'ls_nh', 'k_abs'), bins=8):
    """Finite observed train bounds; constant axes explicitly cannot form bins.

    Supply physical dictionaries or records with physical15. Any explicit split
    must be train; the caller must pass only the frozen training partition.
    """
    rows = list(train_rows)
    if not rows or type(bins) is not int or bins < 1:
        raise ValueError('nonempty train rows and positive integer bins required')
    columns = {name: [] for name in features}
    for row in rows:
        if row.get('split', 'train') != 'train':
            raise ValueError('coverage bounds must not consume validation/test')
        physical = row.get('physical15', row)
        for name in features:
            value = float(physical[name])
            if not math.isfinite(value):
                raise ValueError('coverage requires finite train values')
            columns[name].append(value)
    lower = [min(columns[name]) for name in features]
    upper = [max(columns[name]) for name in features]
    degenerate = [name for name, lo, hi in zip(features, lower, upper) if lo == hi]
    return dict(schema='eucap15_train_observed_coverage_bounds.v1', fit_split='train',
        feature_order=list(features), lower=lower, upper=upper, bins_per_axis=bins,
        intended_cells=bins ** len(features), train_rows=len(rows),
        status='DEGENERATE_TRAIN_AXES_NO_GRID' if degenerate else 'FINITE_TRAIN_GRID_READY',
        degenerate_axes=degenerate, source='ACTUAL_TRAIN_MIN_MAX',
        support_caveat='Marginal observed bounds do not prove joint reachability; not an acceptance window.')
