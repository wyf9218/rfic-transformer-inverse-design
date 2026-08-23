"""Fixed v1 objective for the transformer EMX optimization flow."""

from __future__ import annotations

from ..core.types import TransformerMetrics, TransformerObjectiveBreakdown, TransformerTargetSpec

_MATCH_GATE_MAX_REL_ERROR = 0.05
_Q_REWARD_SCALE = 0.004
_Q_REWARD_CAP = 25.0


def score_transformer_result(
    target: TransformerTargetSpec,
    metrics: TransformerMetrics,
    differential_sparams,
) -> TransformerObjectiveBreakdown:
    """Score one extracted transformer result against the target spec.

    The search is lexicographic in spirit:
    1. Match Lp, Ls, and k first.
    2. Only after each relative error is within 5%, handle Q:
       - `max`: reward larger Q.
       - `target`: penalize deviation from the requested Q targets.
    """
    _ = differential_sparams
    lp_rel = abs(metrics.lp_h - target.lp_h) / target.lp_h
    ls_rel = abs(metrics.ls_h - target.ls_h) / target.ls_h
    k_rel = abs(metrics.k - target.k_target) / max(abs(target.k_target), 1e-12)
    primary = 0.35 * lp_rel + 0.35 * ls_rel + 0.30 * k_rel

    q_reward = 0.0
    q_target_term = 0.0
    q_primary_rel_error = None
    q_secondary_rel_error = None
    q_gate_open = max(lp_rel, ls_rel, k_rel) <= _MATCH_GATE_MAX_REL_ERROR
    if q_gate_open and target.q_target_mode == "max":
        q_reward = _Q_REWARD_SCALE * min(metrics.min_q(), _Q_REWARD_CAP)
    elif q_gate_open and target.q_target_mode == "target":
        q_primary_rel_error = abs(metrics.q_primary - target.q_primary_target) / max(abs(target.q_primary_target), 1e-12)
        q_secondary_rel_error = abs(metrics.q_secondary - target.q_secondary_target) / max(abs(target.q_secondary_target), 1e-12)
        q_target_term = 0.5 * q_primary_rel_error + 0.5 * q_secondary_rel_error

    if target.q_target_mode == "max":
        # Keep the scalar cost nonnegative while still preferring larger Q once
        # the electrical targets are close enough.
        total = primary / (1.0 + q_reward)
    else:
        total = primary + q_target_term
    return TransformerObjectiveBreakdown(
        lp_rel_error=float(lp_rel),
        ls_rel_error=float(ls_rel),
        k_rel_error=float(k_rel),
        primary_term=float(primary),
        q_reward=float(q_reward),
        total_cost=float(total),
        q_target_term=float(q_target_term),
        q_primary_rel_error=(
            None if q_primary_rel_error is None else float(q_primary_rel_error)
        ),
        q_secondary_rel_error=(
            None if q_secondary_rel_error is None else float(q_secondary_rel_error)
        ),
    )
