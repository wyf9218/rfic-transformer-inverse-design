"""Capacity-normalized operating policy for the frozen broadband56 campaign.

This module contains pure control-plane calculations only.  It neither reads
host resources nor starts simulator processes.  Callers must bind every input
to a fresh, hash-closed receipt before acting on a returned decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .broadband56_balanced200k import CAMPAIGN_ID, TARGET_ACCEPTED_GEOMETRIES


RESOURCE_POLICY = "CAPACITY_NORMALIZED_HIGH_LOAD_V1"
SCIENTIFIC_CONTRACT_FINGERPRINT = (
    "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e"
)
SNAPSHOT_SCHEMA = "rfic_transformer.broadband56_v2_capacity_resource_snapshot.v1"
GATE_SCHEMA = "rfic_transformer.broadband56_v2_capacity_resource_gate.v1"
POLICY_CANDIDATE_SCHEMA = (
    "rfic_transformer.broadband56_v2_operational_policy_amendment_candidate.v1"
)
POLICY_APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_v2_operational_policy_amendment_approval.v1"
)
POLICY_APPROVAL_SCOPE = "APPROVE_CAPACITY_NORMALIZED_STAGED_EXECUTION"
GIB = 1024**3
GOLDEN_MINIMUM_FREE_STORAGE_BYTES = 10 * GIB
PRODUCTION_STORAGE_SAFETY_FACTOR = 1.25


@dataclass(frozen=True)
class StageSpec:
    """One cumulative accepted-geometry stage in the frozen execution order."""

    name: str
    cumulative_target: int
    receipt_status: str


STAGES = (
    StageSpec("GOLDEN", 1, "GOLDEN_COMPLETE"),
    StageSpec("PILOT_32", 32, "PILOT_32_COMPLETE"),
    StageSpec("PILOT_1000", 1_000, "PILOT_1000_COMPLETE"),
    StageSpec("PHASE_A", 50_000, "PHASE_A_COMPLETE"),
    StageSpec("PHASE_B", 150_000, "PHASE_B_COMPLETE"),
    StageSpec("PHASE_C", TARGET_ACCEPTED_GEOMETRIES, "COMPLETE_200K"),
)
STAGE_BY_NAME = {stage.name: stage for stage in STAGES}


class CapacityPolicyError(ValueError):
    """Raised when policy inputs are missing, inconsistent, or out of order."""


def normalized_resource_metrics(resources: Mapping[str, Any]) -> dict[str, float]:
    """Return capacity-normalized load and available-memory metrics."""

    logical_cpus = _positive_int(resources.get("logical_cpu_count"), "logical_cpu_count")
    total_memory = _positive_int(resources.get("memory_total_bytes"), "memory_total_bytes")
    available_memory = _nonnegative_int(
        resources.get("memory_available_bytes"), "memory_available_bytes"
    )
    if available_memory > total_memory:
        raise CapacityPolicyError("memory_available_bytes exceeds memory_total_bytes")
    return {
        "normalized_load1": _nonnegative_float(resources.get("load_1m"), "load_1m")
        / logical_cpus,
        "normalized_load5": _nonnegative_float(resources.get("load_5m"), "load_5m")
        / logical_cpus,
        "normalized_load15": _nonnegative_float(resources.get("load_15m"), "load_15m")
        / logical_cpus,
        "available_memory_fraction": available_memory / total_memory,
    }


def required_storage_bytes(
    *,
    stage: str,
    current_accepted: int,
    measured_pilot_bytes_per_geometry: float | None = None,
) -> int:
    """Compute the frozen minimum storage requirement for one stage."""

    name = _stage_name(stage)
    accepted = _nonnegative_int(current_accepted, "current_accepted")
    if accepted > TARGET_ACCEPTED_GEOMETRIES:
        raise CapacityPolicyError("current_accepted exceeds the exact 200K target")
    if name in {"GOLDEN", "PILOT_32", "PILOT_1000"}:
        return GOLDEN_MINIMUM_FREE_STORAGE_BYTES
    measured = _positive_float(
        measured_pilot_bytes_per_geometry,
        "measured_pilot_bytes_per_geometry",
    )
    remaining = TARGET_ACCEPTED_GEOMETRIES - accepted
    return int(math.ceil(measured * remaining * PRODUCTION_STORAGE_SAFETY_FACTOR))


def evaluate_capacity_snapshot(
    snapshot: Mapping[str, Any],
    *,
    stage: str,
    current_accepted: int,
    measured_pilot_bytes_per_geometry: float | None = None,
) -> dict[str, Any]:
    """Evaluate all capacity, license, isolation, and storage gates."""

    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise CapacityPolicyError("capacity snapshot schema mismatch")
    adapter = snapshot.get("capacity_schema_adapter")
    if adapter is not None:
        if not isinstance(adapter, Mapping) or adapter.get("profile") != (
            "SWAP_OVERRIDE_V1_TO_CAPACITY_RESOURCE_V1_STRICT_ADAPTER"
        ):
            raise CapacityPolicyError("capacity snapshot adapter profile mismatch")
        from .broadband56_capacity_snapshot_adapter import (
            CapacitySnapshotAdapterError,
            evaluate_adapted_capacity_snapshot,
        )

        try:
            return evaluate_adapted_capacity_snapshot(
                snapshot,
                stage=stage,
                current_accepted=current_accepted,
                measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
            )
        except CapacitySnapshotAdapterError as exc:
            raise CapacityPolicyError(str(exc)) from exc
    if snapshot.get("campaign_id") != CAMPAIGN_ID:
        raise CapacityPolicyError("capacity snapshot campaign mismatch")
    if snapshot.get("contract_fingerprint_sha256") != SCIENTIFIC_CONTRACT_FINGERPRINT:
        raise CapacityPolicyError("capacity snapshot contract fingerprint mismatch")
    if snapshot.get("resource_policy") != RESOURCE_POLICY:
        raise CapacityPolicyError("capacity snapshot policy mismatch")

    resources = _mapping(snapshot.get("resources"), "resources")
    licenses = _mapping(snapshot.get("licenses"), "licenses")
    isolation = _mapping(snapshot.get("isolation"), "isolation")
    metrics = normalized_resource_metrics(resources)
    logical_cpus = _positive_int(resources.get("logical_cpu_count"), "logical_cpu_count")
    physical_cpus = resources.get("physical_cpu_count")
    if physical_cpus is not None:
        physical_cpus = _positive_int(physical_cpus, "physical_cpu_count")
        if physical_cpus > logical_cpus:
            raise CapacityPolicyError("physical_cpu_count exceeds logical_cpu_count")

    swap_interval = _nonnegative_float(
        resources.get("swap_sample_interval_seconds"),
        "swap_sample_interval_seconds",
    )
    swap_in = _nonnegative_float(resources.get("swap_in_pages_delta"), "swap_in_pages_delta")
    swap_out = _nonnegative_float(resources.get("swap_out_pages_delta"), "swap_out_pages_delta")
    reported_swap_thrash = resources.get("active_swap_thrashing")
    if not isinstance(reported_swap_thrash, bool):
        raise CapacityPolicyError("active_swap_thrashing must be boolean")
    computed_swap_thrash = swap_in > 0.0 or swap_out > 0.0
    if reported_swap_thrash is not computed_swap_thrash:
        raise CapacityPolicyError("reported swap-thrashing state is inconsistent with deltas")

    iowait = _percent(resources.get("iowait_percent"), "iowait_percent")
    for name in (
        "cpu_total_utilization_percent",
        "cpu_user_utilization_percent",
        "cpu_system_utilization_percent",
    ):
        _percent(resources.get(name), name)
    _nonnegative_int(resources.get("runnable_process_count"), "runnable_process_count")
    _nonnegative_int(resources.get("blocked_process_count"), "blocked_process_count")
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
        licenses.get("simulator_license_capacity"), "simulator_license_capacity"
    )
    license_pass = license_pass and license_capacity >= 1

    authoritative_supervisors = _nonnegative_int(
        isolation.get("authoritative_supervisor_count"),
        "authoritative_supervisor_count",
    )
    duplicate_supervisors = _nonnegative_int(
        isolation.get("duplicate_supervisor_count"), "duplicate_supervisor_count"
    )
    duplicate_runners = _nonnegative_int(
        isolation.get("duplicate_runner_count"), "duplicate_runner_count"
    )
    unexpected_children = _nonnegative_int(
        isolation.get("unexpected_project_child_count"), "unexpected_project_child_count"
    )
    for name in (
        "project_owned_cadence_children",
        "project_owned_calibre_children",
        "project_owned_emx_children",
    ):
        _nonnegative_int(isolation.get(name), name)
    collision = isolation.get("output_path_collision")
    if not isinstance(collision, bool):
        raise CapacityPolicyError("output_path_collision must be boolean")
    isolation_pass = (
        authoritative_supervisors <= 1
        and duplicate_supervisors == 0
        and duplicate_runners == 0
        and unexpected_children == 0
        and collision is False
    )

    checks = {
        "normalized_load1_gate": metrics["normalized_load1"] <= 0.90,
        "normalized_load5_gate": metrics["normalized_load5"] <= 0.95,
        "memory_gate": metrics["available_memory_fraction"] >= 0.20,
        "swap_sample_interval_gate": swap_interval >= 60.0,
        "swap_thrash_gate": not computed_swap_thrash,
        "iowait_gate": iowait <= 10.0,
        "license_gate": license_pass,
        "isolation_gate": isolation_pass,
        "storage_gate": free_storage >= required_storage,
    }
    passed = all(checks.values())
    return {
        "pass": passed,
        "decision": "PASS" if passed else "WAIT",
        "stage": _stage_name(stage),
        "metrics": {
            **metrics,
            "raw_load1": _nonnegative_float(resources.get("load_1m"), "load_1m"),
            "logical_cpu_count": logical_cpus,
            "physical_cpu_count": physical_cpus,
            "iowait_percent": iowait,
            "active_swap_thrashing": computed_swap_thrash,
            "filesystem_free_bytes": free_storage,
            "required_storage_bytes": required_storage,
            "simulator_license_capacity": license_capacity,
        },
        "checks": checks,
        "failed_checks": [name for name, value in checks.items() if not value],
    }


def stage_for_progress(
    *,
    current_accepted: int,
    stage_receipts: Sequence[Mapping[str, Any]],
) -> str:
    """Return the only next legal stage from exact ordered PASS receipts."""

    accepted = _nonnegative_int(current_accepted, "current_accepted")
    if accepted > TARGET_ACCEPTED_GEOMETRIES:
        raise CapacityPolicyError("current_accepted exceeds the exact 200K target")
    receipts = list(stage_receipts)
    if len(receipts) > len(STAGES):
        raise CapacityPolicyError("too many stage receipts")
    for index, receipt in enumerate(receipts):
        expected = STAGES[index]
        if (
            receipt.get("stage") != expected.name
            or receipt.get("overall_status") != "PASS"
            or receipt.get("terminal_state") != expected.receipt_status
            or receipt.get("accepted_unique_geometries") != expected.cumulative_target
        ):
            raise CapacityPolicyError(
                f"stage receipt {index} is not exact ordered PASS for {expected.name}"
            )
    if not receipts:
        if accepted != 0:
            raise CapacityPolicyError("accepted geometries exist before golden PASS receipt")
        return "GOLDEN"
    last = STAGES[len(receipts) - 1]
    if accepted != last.cumulative_target:
        raise CapacityPolicyError("accepted count is not bound to the latest stage receipt")
    return "COMPLETE" if len(receipts) == len(STAGES) else STAGES[len(receipts)].name


def adaptive_concurrency(
    *,
    stage: str,
    logical_cpu_count: int,
    simulator_license_capacity: int,
    current_concurrency: int | None,
    healthy_check_streak: int,
    normalized_load1: float,
    iowait_percent: float,
    available_memory_fraction: float,
    active_swap_thrashing: bool,
    licenses_available: bool,
    pilot_1000_safe_concurrency: int | None = None,
) -> dict[str, Any]:
    """Calculate the next worker limit without ever exceeding hard capacity."""

    name = _stage_name(stage)
    logical_cpus = _positive_int(logical_cpu_count, "logical_cpu_count")
    license_capacity = _nonnegative_int(
        simulator_license_capacity, "simulator_license_capacity"
    )
    streak = _nonnegative_int(healthy_check_streak, "healthy_check_streak")
    load = _nonnegative_float(normalized_load1, "normalized_load1")
    iowait = _percent(iowait_percent, "iowait_percent")
    memory = _fraction(available_memory_fraction, "available_memory_fraction")
    if not isinstance(active_swap_thrashing, bool) or not isinstance(
        licenses_available, bool
    ):
        raise CapacityPolicyError("swap and license state must be boolean")

    cpu_cap = max(1, math.floor(logical_cpus * 0.10))
    hard_cap = min(cpu_cap, license_capacity)
    if name == "GOLDEN":
        hard_cap = min(hard_cap, 1)
        initial = 1
    elif name == "PILOT_32":
        hard_cap = min(hard_cap, 2)
        initial = 1
    elif name == "PILOT_1000":
        initial = 2
    else:
        if pilot_1000_safe_concurrency is None:
            raise CapacityPolicyError(
                "production stages require pilot_1000_safe_concurrency"
            )
        initial = _positive_int(
            pilot_1000_safe_concurrency, "pilot_1000_safe_concurrency"
        )
    initial = min(initial, hard_cap)
    current = initial if current_concurrency is None else _nonnegative_int(
        current_concurrency, "current_concurrency"
    )
    current = min(current, hard_cap)

    pause_reasons = []
    if load > 1.20:
        pause_reasons.append("normalized_load1_above_1p20")
    if memory < 0.10:
        pause_reasons.append("available_memory_below_10pct")
    if active_swap_thrashing:
        pause_reasons.append("active_swap_thrashing")
    if not licenses_available or license_capacity == 0:
        pause_reasons.append("license_unavailable")
    if pause_reasons:
        return {
            "concurrency": 0,
            "hard_cap": hard_cap,
            "action": "PAUSE_NEW_LAUNCHES",
            "reasons": pause_reasons,
        }

    reduce_reasons = []
    if load > 1.0:
        reduce_reasons.append("normalized_load1_above_1p00")
    if iowait > 15.0:
        reduce_reasons.append("iowait_above_15pct")
    if memory < 0.15:
        reduce_reasons.append("available_memory_below_15pct")
    if reduce_reasons:
        return {
            "concurrency": max(1, current // 2),
            "hard_cap": hard_cap,
            "action": "REDUCE_BY_HALF",
            "reasons": reduce_reasons,
        }

    if streak >= 10 and current < hard_cap:
        return {
            "concurrency": current + 1,
            "hard_cap": hard_cap,
            "action": "INCREASE_BY_ONE",
            "reasons": ["ten_consecutive_healthy_checks"],
        }
    return {
        "concurrency": current,
        "hard_cap": hard_cap,
        "action": "HOLD",
        "reasons": ["within_capacity_policy"],
    }


def exact_completion(current_accepted: int) -> bool:
    """Return true only at the exact frozen 200K accepted-geometry target."""

    accepted = _nonnegative_int(current_accepted, "current_accepted")
    if accepted > TARGET_ACCEPTED_GEOMETRIES:
        raise CapacityPolicyError("accepted count overshoots exact 200K target")
    return accepted == TARGET_ACCEPTED_GEOMETRIES


def _stage_name(value: str) -> str:
    name = str(value).strip().upper()
    if name not in STAGE_BY_NAME:
        raise CapacityPolicyError(f"unknown campaign stage: {value!r}")
    return name


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapacityPolicyError(f"{name} must be an object")
    return value


def _nonnegative_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CapacityPolicyError(f"{name} must be numeric") from exc
    if isinstance(value, bool) or not math.isfinite(result) or result < 0.0:
        raise CapacityPolicyError(f"{name} must be finite and nonnegative")
    return result


def _positive_float(value: Any, name: str) -> float:
    result = _nonnegative_float(value, name)
    if result <= 0.0:
        raise CapacityPolicyError(f"{name} must be positive")
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


def _fraction(value: Any, name: str) -> float:
    result = _nonnegative_float(value, name)
    if result > 1.0:
        raise CapacityPolicyError(f"{name} must be at most 1")
    return result
