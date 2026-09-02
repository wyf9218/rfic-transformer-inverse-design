"""Owner-approved swap recovery policy for the Broadband56 V2 campaign.

This module is an operational control-plane overlay.  It does not change the
scientific, process, layout, DRC, port, frequency, extraction, or acceptance
contracts.  In particular, swap-in activity is advisory unless it coincides
with measured resource degradation.
"""

from __future__ import annotations

from typing import Any, Mapping

from .broadband56_balanced200k import CAMPAIGN_ID
from .broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    CapacityPolicyError,
    adaptive_concurrency,
    normalized_resource_metrics,
    required_storage_bytes,
)


SWAP_POLICY = "COMBINED_RESOURCE_DEGRADATION_ONLY"
SNAPSHOT_SCHEMA = (
    "rfic_transformer.broadband56_v2_swap_operational_override_snapshot.v1"
)
GATE_SCHEMA = "rfic_transformer.broadband56_v2_swap_operational_override_gate.v1"
OVERRIDE_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_swap_gate_operational_override.v1"
)
OVERRIDE_DECISION = "APPLY_SWAP_GATE_OPERATIONAL_OVERRIDE"
MIN_AVAILABLE_MEMORY_FRACTION = 0.40
MAX_IOWAIT_PERCENT_EXCLUSIVE = 5.0
MATERIAL_SWAP_OUT_PAGES_PER_SECOND = 1.0


def combined_swap_thrashing(resources: Mapping[str, Any]) -> dict[str, Any]:
    """Classify swap activity only when measured degradation accompanies it."""

    metrics = normalized_resource_metrics(resources)
    swap_in = _nonnegative_float(
        resources.get("swap_in_pages_delta"), "swap_in_pages_delta"
    )
    swap_out = _nonnegative_float(
        resources.get("swap_out_pages_delta"), "swap_out_pages_delta"
    )
    swap_interval = _nonnegative_float(
        resources.get("swap_sample_interval_seconds"),
        "swap_sample_interval_seconds",
    )
    if swap_interval <= 0.0:
        raise CapacityPolicyError("swap_sample_interval_seconds must be positive")
    swap_out_pages_per_second = swap_out / swap_interval
    iowait = _percent(resources.get("iowait_percent"), "iowait_percent")
    oom_kill_delta = _nonnegative_int(
        resources.get("oom_kill_delta"), "oom_kill_delta"
    )
    blocked_delta = _nonnegative_int(
        resources.get("blocked_process_count_delta"),
        "blocked_process_count_delta",
    )
    degradation = {
        "material_sustained_swap_out": (
            swap_out_pages_per_second
            >= MATERIAL_SWAP_OUT_PAGES_PER_SECOND
        ),
        "available_memory_below_40pct": (
            metrics["available_memory_fraction"]
            < MIN_AVAILABLE_MEMORY_FRACTION
        ),
        "iowait_at_or_above_5pct": iowait >= MAX_IOWAIT_PERCENT_EXCLUSIVE,
        "oom_event": oom_kill_delta > 0,
        "blocked_process_count_increased": blocked_delta > 0,
    }
    swap_activity = swap_in > 0.0 or swap_out > 0.0
    active = swap_activity and any(degradation.values())
    return {
        "active": active,
        "swap_activity": swap_activity,
        "swap_in_pages_delta": swap_in,
        "swap_out_pages_delta": swap_out,
        "swap_out_pages_per_second": swap_out_pages_per_second,
        "oom_kill_delta": oom_kill_delta,
        "blocked_process_count_delta": blocked_delta,
        "degradation": degradation,
        "advisory_nonzero_swap_in": swap_in > 0.0 and not active,
    }


def evaluate_capacity_snapshot(
    snapshot: Mapping[str, Any],
    *,
    stage: str,
    current_accepted: int,
    measured_pilot_bytes_per_geometry: float | None = None,
) -> dict[str, Any]:
    """Evaluate the unchanged resource gates with the owner swap override."""

    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise CapacityPolicyError("swap-override snapshot schema mismatch")
    if snapshot.get("campaign_id") != CAMPAIGN_ID:
        raise CapacityPolicyError("capacity snapshot campaign mismatch")
    if (
        snapshot.get("contract_fingerprint_sha256")
        != SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise CapacityPolicyError("capacity snapshot contract fingerprint mismatch")
    if snapshot.get("resource_policy") != RESOURCE_POLICY:
        raise CapacityPolicyError("capacity snapshot base resource policy mismatch")
    if snapshot.get("swap_policy") != SWAP_POLICY:
        raise CapacityPolicyError("capacity snapshot swap policy mismatch")

    resources = _mapping(snapshot.get("resources"), "resources")
    licenses = _mapping(snapshot.get("licenses"), "licenses")
    isolation = _mapping(snapshot.get("isolation"), "isolation")
    source_snapshot = _mapping(snapshot.get("source_snapshot"), "source_snapshot")
    if not (
        bool(str(source_snapshot.get("path") or "").strip())
        and _positive_int(source_snapshot.get("size_bytes"), "source_snapshot.size_bytes")
        and _is_sha256(source_snapshot.get("sha256"))
    ):
        raise CapacityPolicyError("source snapshot evidence is incomplete")

    metrics = normalized_resource_metrics(resources)
    logical_cpus = _positive_int(
        resources.get("logical_cpu_count"), "logical_cpu_count"
    )
    physical_cpus = resources.get("physical_cpu_count")
    if physical_cpus is not None:
        physical_cpus = _positive_int(physical_cpus, "physical_cpu_count")
        if physical_cpus > logical_cpus:
            raise CapacityPolicyError(
                "physical_cpu_count exceeds logical_cpu_count"
            )

    swap_interval = _nonnegative_float(
        resources.get("swap_sample_interval_seconds"),
        "swap_sample_interval_seconds",
    )
    swap = combined_swap_thrashing(resources)
    reported_swap_thrash = resources.get("active_swap_thrashing")
    if not isinstance(reported_swap_thrash, bool):
        raise CapacityPolicyError("active_swap_thrashing must be boolean")
    if reported_swap_thrash is not swap["active"]:
        raise CapacityPolicyError(
            "reported combined swap-thrashing state is inconsistent"
        )

    iowait = _percent(resources.get("iowait_percent"), "iowait_percent")
    for name in (
        "cpu_total_utilization_percent",
        "cpu_user_utilization_percent",
        "cpu_system_utilization_percent",
    ):
        _percent(resources.get(name), name)
    _nonnegative_int(
        resources.get("runnable_process_count"), "runnable_process_count"
    )
    _nonnegative_int(
        resources.get("blocked_process_count"), "blocked_process_count"
    )
    _nonnegative_int(resources.get("swap_total_bytes"), "swap_total_bytes")
    _nonnegative_int(resources.get("swap_used_bytes"), "swap_used_bytes")

    required_storage = required_storage_bytes(
        stage=stage,
        current_accepted=current_accepted,
        measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
    )
    free_storage = _nonnegative_int(
        resources.get("filesystem_free_bytes"), "filesystem_free_bytes"
    )
    license_pass = all(
        licenses.get(name) is True
        for name in ("cadence_available", "calibre_available", "emx_available")
    )
    license_capacity = _nonnegative_int(
        licenses.get("simulator_license_capacity"),
        "simulator_license_capacity",
    )
    license_pass = license_pass and license_capacity >= 1

    authoritative_supervisors = _nonnegative_int(
        isolation.get("authoritative_supervisor_count"),
        "authoritative_supervisor_count",
    )
    duplicate_supervisors = _nonnegative_int(
        isolation.get("duplicate_supervisor_count"),
        "duplicate_supervisor_count",
    )
    duplicate_runners = _nonnegative_int(
        isolation.get("duplicate_runner_count"), "duplicate_runner_count"
    )
    unexpected_children = _nonnegative_int(
        isolation.get("unexpected_project_child_count"),
        "unexpected_project_child_count",
    )
    active_simulator_jobs = 0
    for name in (
        "project_owned_cadence_children",
        "project_owned_calibre_children",
        "project_owned_emx_children",
    ):
        active_simulator_jobs += _nonnegative_int(isolation.get(name), name)
    collision = isolation.get("output_path_collision")
    if not isinstance(collision, bool):
        raise CapacityPolicyError("output_path_collision must be boolean")
    isolation_pass = (
        authoritative_supervisors == 1
        and duplicate_supervisors == 0
        and duplicate_runners == 0
        and unexpected_children == 0
        and collision is False
    )

    checks = {
        "normalized_load1_gate": metrics["normalized_load1"] <= 0.90,
        "normalized_load5_gate": metrics["normalized_load5"] <= 0.95,
        "memory_gate": (
            metrics["available_memory_fraction"]
            >= MIN_AVAILABLE_MEMORY_FRACTION
        ),
        "no_oom_gate": swap["oom_kill_delta"] == 0,
        "swap_sample_interval_gate": swap_interval >= 60.0,
        "swap_thrash_gate": not swap["active"],
        "iowait_gate": iowait < MAX_IOWAIT_PERCENT_EXCLUSIVE,
        "license_gate": license_pass,
        "isolation_gate": isolation_pass,
        "storage_gate": free_storage >= required_storage,
    }
    passed = all(checks.values())
    return {
        "pass": passed,
        "decision": "PASS" if passed else "WAIT",
        "stage": str(stage).strip().upper(),
        "metrics": {
            **metrics,
            "raw_load1": _nonnegative_float(resources.get("load_1m"), "load_1m"),
            "logical_cpu_count": logical_cpus,
            "physical_cpu_count": physical_cpus,
            "iowait_percent": iowait,
            "active_swap_thrashing": swap["active"],
            "advisory_nonzero_swap_in": swap["advisory_nonzero_swap_in"],
            "swap_in_pages_delta": swap["swap_in_pages_delta"],
            "swap_out_pages_delta": swap["swap_out_pages_delta"],
            "oom_kill_delta": swap["oom_kill_delta"],
            "blocked_process_count_delta": swap[
                "blocked_process_count_delta"
            ],
            "filesystem_free_bytes": free_storage,
            "required_storage_bytes": required_storage,
            "simulator_license_capacity": license_capacity,
            "active_simulator_jobs": active_simulator_jobs,
        },
        "swap_classification": swap,
        "checks": checks,
        "failed_checks": [name for name, value in checks.items() if not value],
    }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapacityPolicyError(f"{name} must be an object")
    return value


def _nonnegative_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise CapacityPolicyError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CapacityPolicyError(f"{name} must be numeric") from exc
    if result < 0.0 or result != result or result in {float("inf"), float("-inf")}:
        raise CapacityPolicyError(f"{name} must be finite and nonnegative")
    return result


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise CapacityPolicyError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CapacityPolicyError(f"{name} must be an integer") from exc
    if result < 0 or result != value:
        raise CapacityPolicyError(f"{name} must be a nonnegative integer")
    return result


def _positive_int(value: Any, name: str) -> int:
    result = _nonnegative_int(value, name)
    if result <= 0:
        raise CapacityPolicyError(f"{name} must be positive")
    return result


def _percent(value: Any, name: str) -> float:
    result = _nonnegative_float(value, name)
    if result > 100.0:
        raise CapacityPolicyError(f"{name} must be at most 100")
    return result


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "GATE_SCHEMA",
    "MAX_IOWAIT_PERCENT_EXCLUSIVE",
    "MATERIAL_SWAP_OUT_PAGES_PER_SECOND",
    "MIN_AVAILABLE_MEMORY_FRACTION",
    "OVERRIDE_DECISION",
    "OVERRIDE_RECEIPT_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "SWAP_POLICY",
    "adaptive_concurrency",
    "combined_swap_thrashing",
    "evaluate_capacity_snapshot",
]
