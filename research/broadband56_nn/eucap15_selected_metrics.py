"""Descriptive statistics for already-verified EuCAP selected-candidate rows.

This pure function does not validate the physical evidence chain, load models,
read artifacts, or estimate confidence intervals. One input row is one original
request and its preselected candidate, never eleven independent physical trials.
The caller must keep model, target-frame, and physical-configuration scopes
separate and validate their provenance before calling ``summarize``.

``metric_rows`` has eight rows (two comparisons x four features), including for
an empty frame. Physical errors and fixed-span-normalized errors are separate
columns. Only the target comparison has target-relative absolute percentages;
the existing near-zero suppression policy is retained. ``ecdf_rows`` contains
one sorted absolute-error observation per STRICT_VALID request/comparison/
feature, with rank/n, identity, physical error, and its normalized counterpart.
No interpolation, confidence band, survivor replacement, or q_emx is produced.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import math
from numbers import Real
import re

from .frequency_physical_statistics import error_metrics, percentage, percentile


FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
UNITS = ("nH", "nH", "dimensionless", "dimensionless")
SCORE_SPANS = (2.5, 2.5, 20.0, 0.8)
STATES = ("ANALYTIC_FAIL", "PENDING", "STRICT_VALID", "EMX_INVALID",
          "GDS_FAIL", "DRC_FAIL", "SOLVER_FAIL", "FEATURE_FAIL")
COMPARISONS = ("emx_minus_target", "emx_minus_grid_proxy")
REQUIRED = frozenset((
    "request_id", "candidate_id", "candidate_geometry_identity_sha256",
    "q_proxy", "target", "grid_proxy", "state", "actual",
    "strict_joint_hit", "touchstone_sha",
))
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool)


def _vector(value, name, *, positive=False, finite=True):
    _require(isinstance(value, (list, tuple)) and len(value) == 4,
             name + " must contain four numbers")
    _require(all(_number(v) for v in value), name + " must be numeric, not bool")
    result = tuple(float(v) for v in value)
    if finite:
        _require(all(math.isfinite(v) for v in result), name + " must be finite")
    if positive:
        _require(all(v > 0 for v in result), name + " must be positive")
    return result


def _identity(value, name):
    _require(isinstance(value, str) and bool(value.strip()), name + " is required")
    _require(value == value.strip(), name + " must not have outer whitespace")
    return value


def _sha(value, name, *, optional=False):
    if optional and value is None:
        return None
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None,
             name + " must be a SHA-256 hex digest")
    return value.lower()


def _validated_rows(rows, tolerances):
    """Validate shape/status consistency; never assert foundry/EM validity."""
    result, request_ids, candidate_ids = [], set(), set()
    for index, source in enumerate(rows):
        prefix = "row " + str(index) + ": "
        _require(isinstance(source, Mapping), prefix + "mapping required")
        _require(REQUIRED <= source.keys(), prefix + "missing required fields")
        request_id = _identity(source["request_id"], prefix + "request_id")
        candidate_id = _identity(source["candidate_id"], prefix + "candidate_id")
        _require(request_id not in request_ids, prefix + "duplicate request_id")
        _require(candidate_id not in candidate_ids, prefix + "duplicate candidate_id")
        request_ids.add(request_id)
        candidate_ids.add(candidate_id)
        state = source["state"]
        _require(isinstance(state, str) and state in STATES, prefix + "unknown state")
        q_proxy = source["q_proxy"]
        _require(isinstance(q_proxy, int) and not isinstance(q_proxy, bool)
                 and 10 <= q_proxy <= 20, prefix + "q_proxy must be integer 10..20")
        target = _vector(source["target"], prefix + "target", positive=True)
        proxy = _vector(source["grid_proxy"], prefix + "grid_proxy")
        _require(target[2] == q_proxy, prefix + "target Q differs from q_proxy")
        geometry = _sha(source["candidate_geometry_identity_sha256"],
                        prefix + "candidate_geometry_identity_sha256")
        touchstone = _sha(source["touchstone_sha"], prefix + "touchstone_sha", optional=True)
        actual, hit = source["actual"], source["strict_joint_hit"]
        if state == "STRICT_VALID":
            actual = _vector(actual, prefix + "STRICT_VALID actual")
            _require(touchstone is not None, prefix + "STRICT_VALID requires touchstone_sha")
            _require(isinstance(hit, bool), prefix + "STRICT_VALID hit must be bool")
            expected = all(abs(a - t) <= tolerance
                           for a, t, tolerance in zip(actual, target, tolerances))
            _require(hit == expected, prefix + "strict_joint_hit disagrees with exact tolerances")
        else:
            if state in ("ANALYTIC_FAIL", "PENDING", "GDS_FAIL", "DRC_FAIL", "SOLVER_FAIL"):
                _require(hit is None, prefix + "pre-EM state must have null strict_joint_hit")
                _require(actual is None and touchstone is None,
                         prefix + "pre-EM state must have null actual and touchstone_sha")
            elif state == "FEATURE_FAIL":
                _require(actual is None and hit is None,
                         prefix + "FEATURE_FAIL must have null actual and strict_joint_hit")
                # A closed successful solve may retain its S4P identity even
                # when extraction failed. It supplies no numeric observation.
            else:
                _require(hit is None or hit is False,
                         prefix + "EMX_INVALID strict_joint_hit must be False or null")
                if actual is not None:
                    # Native cleaning can replace individual nonfinite values
                    # with null. Invalid rows never become error observations.
                    _require(isinstance(actual, (list, tuple)) and len(actual) == 4,
                             prefix + "EMX_INVALID actual must contain four values")
                    _require(all(value is None or _number(value) for value in actual),
                             prefix + "EMX_INVALID values must be numeric or null, not bool/string")
                    actual = tuple(None if value is None else float(value) for value in actual)
        result.append(dict(request_id=request_id, candidate_id=candidate_id,
                           candidate_geometry_identity_sha256=geometry,
                           q_proxy=q_proxy, target=target, grid_proxy=proxy,
                           state=state, actual=actual, strict_joint_hit=hit,
                           touchstone_sha=touchstone))
    return result


def _errors(values):
    _require(all(math.isfinite(v) for v in values), "nonfinite residual is not reportable")
    try:
        metrics = error_metrics(values)
        absolute = [abs(v) for v in values]
        metrics.update(abs_error_p99=percentile(absolute, .99),
                       abs_error_max=max(absolute) if absolute else None)
    except (OverflowError, ValueError) as error:
        raise ValueError("error statistics exceed finite numeric range") from error
    _require(all(v is None or math.isfinite(v) for v in metrics.values()),
             "error statistics exceed finite numeric range")
    return metrics


def _relative_fields(eligible, index, span, *, comparison):
    names = ("mean", "p50", "p90", "p95", "p99", "max")
    result = {"target_relative_absolute_percent_" + name: None for name in names}
    result.update(target_relative_absolute_percent_n=None,
                  target_relative_percentage_status_counts=None)
    if comparison != "emx_minus_target":
        return result
    values, statuses = [], Counter()
    for row in eligible:
        value, status = percentage(row["actual"][index], row["target"][index], span)
        statuses[status] += 1
        if value is not None:
            _require(math.isfinite(value), "target-relative percentage is nonfinite")
            values.append(value)
    result["target_relative_absolute_percent_n"] = len(values)
    result["target_relative_percentage_status_counts"] = dict(sorted(statuses.items()))
    if values:
        # error_metrics also supplies the mean of these nonnegative values.
        result["target_relative_absolute_percent_mean"] = _errors(values)["mae"]
        for name, probability in (("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99)):
            result["target_relative_absolute_percent_" + name] = percentile(values, probability)
        result["target_relative_absolute_percent_max"] = max(values)
    return result


def summarize(rows, *, score_spans, tolerances):
    """Return ``{summary, metric_rows, ecdf_rows}`` without I/O or side effects.

    Request and candidate IDs must be unique; repeated geometry/S4P identities
    are allowed and counted separately. STRICT_VALID requires finite actuals
    and a hit flag consistent with symmetric ``abs(actual-target) <= tolerance``,
    plus a non-null touchstone SHA. EMX_INVALID can carry null/nonfinite label
    components and a False/null hit flag; it contributes no numeric errors.
    The caller's positive tolerance floats are used exactly, without rounding
    or replacement by nominal decimal constants. Only the frozen EuCAP score
    spans [2.5, 2.5, 20, 0.8] are accepted. A valid row is statistical input, not
    proof here of strict-label provenance or an independent fresh solver run.
    """
    spans = _vector(score_spans, "score_spans", positive=True)
    _require(spans == SCORE_SPANS, "score_spans must equal [2.5, 2.5, 20, 0.8]")
    tolerances = _vector(tolerances, "tolerances", positive=True)
    _require(not isinstance(rows, (str, bytes, Mapping)), "rows must be an iterable of request rows")
    try:
        rows = _validated_rows(iter(rows), tolerances)
    except TypeError as error:
        raise ValueError("rows must be an iterable of request rows") from error
    counts = Counter(row["state"] for row in rows)
    eligible = [row for row in rows if row["state"] == "STRICT_VALID"]
    original, valid = len(rows), len(eligible)
    pending = counts["PENDING"]
    hits = sum(row["strict_joint_hit"] for row in eligible)
    geometry_ids = {row["candidate_geometry_identity_sha256"] for row in rows}
    strict_geometry_ids = {row["candidate_geometry_identity_sha256"] for row in eligible}
    touchstone_ids = {row["touchstone_sha"] for row in rows if row["touchstone_sha"] is not None}
    strict_touchstone_ids = {row["touchstone_sha"] for row in eligible if row["touchstone_sha"] is not None}
    summary = dict(
        schema="eucap15_selected_metrics.v1",
        scope="DESCRIPTIVE_ALREADY_VERIFIED_SELECTED_ROWS_NOT_PHYSICAL_CHAIN_VALIDATION",
        feature_order=list(FEATURES), score_spans=list(spans), absolute_tolerances=list(tolerances),
        N_original_requests=original, N_selected_candidate_rows=original,
        N_terminal_requests=original - pending, N_pending_requests=pending,
        N_strict_valid=valid, N_analytic_fail=counts["ANALYTIC_FAIL"],
        N_gds_fail=counts["GDS_FAIL"], N_drc_fail=counts["DRC_FAIL"],
        N_solver_fail=counts["SOLVER_FAIL"], N_feature_fail=counts["FEATURE_FAIL"],
        N_emx_invalid=counts["EMX_INVALID"], N_joint_hit=hits, N_strict_not_hit=valid - hits,
        state_counts={state: counts[state] for state in STATES},
        completion_status=("EMPTY_FRAME" if not original else
                           "PARTIAL_PENDING" if pending else "COMPLETE_ACCOUNTING"),
        strict_valid_fraction_original=valid / original if original else None,
        observed_joint_hit_fraction_original=hits / original if original else None,
        conditional_valid_joint_hit_fraction=hits / valid if valid else None,
        completed_joint_hit_fraction_original=(hits / original if original and not pending else None),
        observed_fraction_interpretation="Confirmed hits/original requests; with pending rows this is observed coverage, not a completed success probability.",
        N_unique_selected_geometry_sha256=len(geometry_ids),
        N_unique_strict_valid_geometry_sha256=len(strict_geometry_ids),
        N_unique_reported_touchstone_sha256=len(touchstone_ids),
        N_unique_strict_valid_touchstone_sha256=len(strict_touchstone_ids),
        N_rows_with_touchstone_sha=sum(row["touchstone_sha"] is not None for row in rows),
        N_strict_valid_with_touchstone_sha=sum(row["touchstone_sha"] is not None for row in eligible),
        native_attempts=None, independent_native_solves=None, cache_hits=None, cache_hit_rate=None,
        execution_count_status="NOT_ESTABLISHED_FROM_SELECTED_ROWS; equal geometry/S4P hashes do not establish cache hits or attempts.",
        q_emx=None, complete11_status="NOT_EVALUATED",
        physical_grain="One selected candidate per original request; eleven proxy candidates are not eleven physical trials.",
        ci_status="NOT_ESTIMATED", ci_method=None, ci_seed=None,
        percentile_interpretation="Observed absolute-error quantiles, not confidence intervals or maximum-error guarantees.",
        relative_error_policy="Target-relative absolute percentage only; existing near-zero suppression; not a hit tolerance.",
    )
    metric_rows, ecdf_rows = [], []
    for comparison in COMPARISONS:
        reference = "target" if comparison == "emx_minus_target" else "grid_proxy"
        for index, (feature, unit, span) in enumerate(zip(FEATURES, UNITS, spans)):
            values = [row["actual"][index] - row[reference][index] for row in eligible]
            physical = _errors(values)
            normalized = _errors([value / span for value in values])
            metric = dict(comparison=comparison, feature=feature, unit=unit,
                          N_original=original, n_request_groups=valid, score_span=span,
                          validity="STRICT_VALID_ONLY", ci_status="NOT_ESTIMATED", **physical)
            metric.update({"normalized_" + key: value for key, value in normalized.items() if key != "n"})
            metric["normalization"] = "Absolute fixed span; dimensionless, not target-relative percentage"
            metric.update(_relative_fields(eligible, index, span, comparison=comparison))
            metric_rows.append(metric)
            ordered = sorted(zip(eligible, values),
                             key=lambda item: (abs(item[1]), item[0]["request_id"], item[0]["candidate_id"]))
            for rank, (row, residual) in enumerate(ordered, 1):
                ecdf_rows.append(dict(
                    comparison=comparison, feature=feature, unit=unit,
                    request_id=row["request_id"], candidate_id=row["candidate_id"],
                    q_proxy=row["q_proxy"], rank=rank, n=valid, N_original=original,
                    absolute_error=abs(residual), normalized_absolute_error=abs(residual) / span,
                    cdf=rank / valid, ci_status="NOT_ESTIMATED",
                    definition="Conditional strict-valid empirical absolute-error CDF; one point per observation, including ties.",
                ))
    return dict(summary=summary, metric_rows=metric_rows, ecdf_rows=ecdf_rows)
