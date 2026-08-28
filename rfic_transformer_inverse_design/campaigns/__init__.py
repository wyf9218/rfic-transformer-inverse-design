"""Audited, versioned production-campaign contracts."""

from .broadband56_balanced200k import (
    CAMPAIGN_ID,
    EXPECTED_FEATURE_ROWS,
    FREQUENCY_POINTS,
    TARGET_ACCEPTED_GEOMETRIES,
    build_phase_plan,
    canonical_geometry_sha256,
    contract_fingerprint,
    primary_bin_edges,
    secondary_bin_edges,
    secondary_coverage_contract,
    validate_contract,
)

__all__ = [
    "CAMPAIGN_ID",
    "EXPECTED_FEATURE_ROWS",
    "FREQUENCY_POINTS",
    "TARGET_ACCEPTED_GEOMETRIES",
    "build_phase_plan",
    "canonical_geometry_sha256",
    "contract_fingerprint",
    "primary_bin_edges",
    "secondary_bin_edges",
    "secondary_coverage_contract",
    "validate_contract",
]
