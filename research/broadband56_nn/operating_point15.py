"""Versioned 15 GHz eligibility over saved, provenance-bound EM descriptors.

No extraction formula, original mask or SRF value is changed. The existing
descriptor mask is SRF-independent: finite S/Z/features and positive R/X on
both windings (broadband56_s4p_qa.audit_s4p). Physical provenance is separate.
"""
from __future__ import annotations
import math

LABEL_POLICY = 'operating_point_15ghz_v1'
LEGACY_LABEL_POLICY = 'strict_lumped_15ghz_v1'
LABEL_MODE = 'OPERATING_POINT_15GHZ'
# Same tolerances as the existing extractor, not relaxed acceptance limits.
ROUNDTRIP_TOLERANCE = 1e-8
PASSIVITY_TOLERANCE = 1e-6
RECIPROCITY_TOLERANCE = 1e-6


def boolean(value):
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    return None


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def classify(record, *, compatibility='UNKNOWN'):
    """Classify audited native feature receipts or saved historical derivations.

    A numerical pass is not proof of GDS/DRC/port compatibility. Band-maximum
    QA can prove pointwise PASS, but a band failure cannot prove a 15GHz failure.
    """
    if compatibility not in ('COMPATIBLE_PROVEN', 'INCOMPATIBLE', 'UNKNOWN'):
        raise ValueError('explicit physical compatibility status required')
    row = record.get('original_frequency_row', record)
    summary = record.get('original_56_summary', {})
    physical = record.get('physical15', row)
    p = {k: physical.get(k, physical.get('q_min') if k == 'qmin' else None)
         for k in ('lp_nh', 'ls_nh', 'qmin', 'k_abs')}
    reasons = []
    missing = []
    f = row.get('frequency_hz', record.get('frequency_hz'))
    if f is None and record.get('frequency_ghz') == 15:
        f = 15e9
    if not _finite(f) or f != 15e9:
        missing.append('EXACT_15GHZ_RESPONSE')
    for key, value in p.items():
        if not _finite(value):
            reasons.append(key + ':MISSING_OR_NONFINITE')
    for key in ('lp_nh', 'ls_nh'):
        if _finite(p[key]) and p[key] <= 0:
            reasons.append(key + ':NONPOSITIVE')
    if _finite(p['k_abs']) and not .2 <= p['k_abs'] <= .85:
        reasons.append('K_OUTSIDE_0P2_0P85')
    descriptor = boolean(row.get('broadband_descriptor_valid', record.get('descriptor_valid')))
    if descriptor is None:
        missing.append('SRF_INDEPENDENT_DESCRIPTOR_MASK')
    elif not descriptor:
        reasons.append('DESCRIPTOR_NONFINITE_OR_NONPOSITIVE_RX')
    for key in ('finite_values', 'positive_primary_resistance',
                'positive_secondary_resistance', 'positive_primary_inductive_reactance',
                'positive_secondary_inductive_reactance'):
        if key in row and boolean(row[key]) is not True:
            reasons.append(key + ':NOT_PASSED')
    continuity = row.get('extraction_continuity_status')
    if continuity is not None and continuity != 'PASS':
        reasons.append('EXTRACTION_CONTINUITY_NOT_PASSED')
    roundtrip = record.get('s_z_roundtrip_abs_max', summary.get('s_to_z_roundtrip_max_abs_error'))
    if not _finite(roundtrip):
        missing.append('S_Z_ROUNDTRIP_EVIDENCE')
    elif roundtrip > ROUNDTRIP_TOLERANCE:
        reasons.append('S_Z_ROUNDTRIP_FAIL')
    for name, maximum, limit in (('passivity', 'passivity_sigma_max', 1 + PASSIVITY_TOLERANCE),
                                  ('reciprocity', 'reciprocity_abs_max', RECIPROCITY_TOLERANCE)):
        status = row.get(name + '_status')
        if status == 'FAIL':
            reasons.append(name.upper() + '_FAIL_AT_15GHZ')
        elif status != 'PASS':
            value = record.get(maximum)
            if not (_finite(value) and value <= limit):
                missing.append(name.upper() + '_15GHZ_EVIDENCE')
    old = boolean(row.get('strict_lumped_valid', record.get('strict_valid')))
    half = boolean(row.get('below_half_srf', record.get('below_half_srf')))
    valid = not reasons and not missing
    return dict(label_policy=LABEL_POLICY, operating_point_valid=valid,
                physical15=p, legacy_strict_lumped_valid=old,
                below_half_srf=half if half is not None else 'UNKNOWN',
                srf_role='OPTIONAL_DIAGNOSTIC_NOT_A_GATE',
                old_policy_valid=valid and old is True,
                recovered_only_by_removing_half_srf=valid and old is False,
                numerical_reasons=reasons, missing_evidence=missing,
                compatibility_status=compatibility,
                fully_qualified=valid and compatibility == 'COMPATIBLE_PROVEN',
                increment_kind='POLICY_RECLASSIFICATION_NOT_NEW_EMX')


def feature_annotations(feature):
    """Add policy fields without changing old strict scores or original rows."""
    verdict = classify(feature)
    return dict(label_policy=LABEL_POLICY,
                operating_point_valid=verdict['operating_point_valid'],
                operating_point_joint_hit=verdict['operating_point_valid'] and
                    feature.get('joint_response_hit', False) is True,
                operating_point_policy_evidence=verdict)
