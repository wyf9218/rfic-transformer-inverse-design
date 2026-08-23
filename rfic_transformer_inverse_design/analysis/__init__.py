"""Transformer network extraction and objective analysis."""

from .extraction import (
    build_lumped_transformer_sparameters,
    differential_2port_to_4port_s,
    differential_2port_to_4port_z,
    extract_transformer_metrics,
    extract_transformer_metrics_from_differential,
    extract_transformer_metrics_from_single_ended_pairs,
    multiport_s_to_grounded_differential_z,
    multiport_single_ended_to_differential_z,
    single_ended_to_differential_z,
)
from .objective import score_transformer_result

__all__ = [
    "build_lumped_transformer_sparameters",
    "differential_2port_to_4port_s",
    "differential_2port_to_4port_z",
    "extract_transformer_metrics",
    "extract_transformer_metrics_from_differential",
    "extract_transformer_metrics_from_single_ended_pairs",
    "multiport_s_to_grounded_differential_z",
    "multiport_single_ended_to_differential_z",
    "score_transformer_result",
    "single_ended_to_differential_z",
]
