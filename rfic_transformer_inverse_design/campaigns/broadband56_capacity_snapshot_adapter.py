"""Strictly adapt the approved swap snapshot for legacy stage consumers.

The adapter preserves every observed resource value.  It changes only the
outer schema consumed by the legacy stage launcher and records the exact
source snapshot and resource-gate identities.  Adapted snapshots are always
revalidated under the approved swap policy and against the original capture
time before use.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .broadband56_balanced200k import CAMPAIGN_ID
from .broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    SNAPSHOT_SCHEMA as TARGET_SNAPSHOT_SCHEMA,
)
from . import broadband56_swap_override_policy as swap_policy


ADAPTER_PROFILE = "SWAP_OVERRIDE_V1_TO_CAPACITY_RESOURCE_V1_STRICT_ADAPTER"
ADAPTER_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_capacity_schema_adapter_receipt.v1"
)
SOURCE_SNAPSHOT_SCHEMA = swap_policy.SNAPSHOT_SCHEMA
SOURCE_GATE_SCHEMA = swap_policy.GATE_SCHEMA
MAX_LAUNCH_AGE_SECONDS = 300
ADAPTED_SNAPSHOT_NAME = "CAPACITY_RESOURCE_SNAPSHOT_ADAPTED.json"
ADAPTER_RECEIPT_NAME = "CAPACITY_SCHEMA_ADAPTER_RECEIPT.json"

REQUIRED_RESOURCE_FIELDS = (
    "logical_cpu_count",
    "physical_cpu_count",
    "load_1m",
    "load_5m",
    "load_15m",
    "cpu_total_utilization_percent",
    "cpu_user_utilization_percent",
    "cpu_system_utilization_percent",
    "iowait_percent",
    "runnable_process_count",
    "blocked_process_count",
    "memory_total_bytes",
    "memory_available_bytes",
    "swap_total_bytes",
    "swap_used_bytes",
    "swap_sample_interval_seconds",
    "swap_in_pages_delta",
    "swap_out_pages_delta",
    "active_swap_thrashing",
    "filesystem_free_bytes",
)
REQUIRED_LICENSE_FIELDS = (
    "cadence_available",
    "calibre_available",
    "emx_available",
    "simulator_license_capacity",
)
REQUIRED_ISOLATION_FIELDS = (
    "authoritative_supervisor_count",
    "duplicate_supervisor_count",
    "duplicate_runner_count",
    "unexpected_project_child_count",
    "project_owned_cadence_children",
    "project_owned_calibre_children",
    "project_owned_emx_children",
    "output_path_collision",
)


class CapacitySnapshotAdapterError(ValueError):
    """Fail-closed error for an ambiguous or drifted snapshot adaptation."""


def normalize_capacity_snapshot_for_stage_launcher(
    source_snapshot_path: Path,
    source_gate_path: Path,
    out_dir: Path,
    *,
    converted_utc: datetime | None = None,
) -> dict[str, Any]:
    """Write one no-clobber target-schema snapshot and an exact receipt."""

    source_path = source_snapshot_path.expanduser().resolve()
    gate_path = source_gate_path.expanduser().resolve()
    destination = out_dir.expanduser().resolve()
    if destination.exists():
        raise CapacitySnapshotAdapterError(
            f"no-clobber adapter output exists: {destination}"
        )
    source = _read_bound_json(source_path, "source swap snapshot")
    gate = _read_bound_json(gate_path, "source resource gate")
    source_record = file_record(source_path)
    gate_record = file_record(gate_path)
    source_decision = _validate_source_and_gate(
        source,
        source_record=source_record,
        gate=gate,
        gate_record=gate_record,
    )
    converted = _aware_utc(converted_utc or datetime.now(timezone.utc), "converted_utc")
    captured = _parse_utc(source.get("captured_utc"), "captured_utc")
    if converted < captured:
        raise CapacitySnapshotAdapterError(
            "adapter conversion timestamp precedes the resource observation"
        )

    adapted = copy.deepcopy(source)
    adapted["schema"] = TARGET_SNAPSHOT_SCHEMA
    adapted["operational_resource_policy"] = swap_policy.SWAP_POLICY
    adapted["capacity_schema_adapter"] = {
        "profile": ADAPTER_PROFILE,
        "original_schema": SOURCE_SNAPSHOT_SCHEMA,
        "target_schema": TARGET_SNAPSHOT_SCHEMA,
        "original_snapshot": source_record,
        "source_resource_gate": gate_record,
        "source_gate_overall_status": gate["overall_status"],
        "source_gate_decision": gate["decision"],
        "source_gate_checks": copy.deepcopy(gate["checks"]),
        "source_policy_decision": source_decision["decision"],
        "original_observation_timestamp": source["captured_utc"],
        "conversion_timestamp": converted.isoformat(timespec="microseconds"),
        "maximum_launch_age_seconds": MAX_LAUNCH_AGE_SECONDS,
        "resource_values_preserved": True,
        "license_values_preserved": True,
        "isolation_values_preserved": True,
        "gate_result_preserved": True,
        "gate_reasons_preserved": True,
        "observation_timestamp_preserved": True,
        "adapter_creation_refreshes_observation_timestamp": False,
        "resource_values_relaxed_or_recalculated": False,
    }
    adapted["capacity_schema_adapter"]["canonical_payload_sha256"] = (
        _canonical_adapter_payload_sha256(adapted)
    )

    destination.mkdir(parents=True, mode=0o700)
    adapted_path = destination / ADAPTED_SNAPSHOT_NAME
    _write_json_new(adapted_path, adapted)
    adapted_record = file_record(adapted_path)
    receipt = {
        "schema": ADAPTER_RECEIPT_SCHEMA,
        "generated_utc": converted.isoformat(timespec="microseconds"),
        "overall_status": "PASS",
        "decision": "USE_STRICT_ADAPTED_SNAPSHOT_FOR_STAGE_LAUNCHER",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "adapter_profile": ADAPTER_PROFILE,
        "source_schema": SOURCE_SNAPSHOT_SCHEMA,
        "target_schema": TARGET_SNAPSHOT_SCHEMA,
        "source_snapshot": source_record,
        "source_resource_gate": gate_record,
        "adapted_snapshot": adapted_record,
        "source_gate_overall_status": gate["overall_status"],
        "source_gate_decision": gate["decision"],
        "source_gate_checks": copy.deepcopy(gate["checks"]),
        "original_observation_timestamp": source["captured_utc"],
        "maximum_launch_age_seconds": MAX_LAUNCH_AGE_SECONDS,
        "resource_values_preserved": True,
        "observation_timestamp_preserved": True,
        "freshness_semantics_preserved": True,
        "resource_thresholds_changed": False,
        "simulator_action_taken": False,
    }
    receipt_path = destination / ADAPTER_RECEIPT_NAME
    _write_json_new(receipt_path, receipt)
    sums_path = destination / "SHA256SUMS.txt"
    _write_text_new(
        sums_path,
        f"{adapted_record['sha256']}  {ADAPTED_SNAPSHOT_NAME}\n"
        f"{file_record(receipt_path)['sha256']}  {ADAPTER_RECEIPT_NAME}\n",
    )
    return {
        "adapted_snapshot_path": adapted_path,
        "adapter_receipt_path": receipt_path,
        "adapted_snapshot": adapted_record,
        "adapter_receipt": file_record(receipt_path),
        "source_policy_decision": source_decision,
    }


def evaluate_adapted_capacity_snapshot(
    snapshot: Mapping[str, Any],
    *,
    stage: str,
    current_accepted: int,
    measured_pilot_bytes_per_geometry: float | None = None,
    evaluated_utc: datetime | None = None,
) -> dict[str, Any]:
    """Revalidate an adapted payload under the source policy and timestamp."""

    adapter = _adapter_evidence(snapshot)

    source_path = _record_path(adapter.get("original_snapshot"), "original snapshot")
    gate_path = _record_path(adapter.get("source_resource_gate"), "source gate")
    source = _read_bound_json(source_path, "original snapshot")
    gate = _read_bound_json(gate_path, "source gate")
    source_record = file_record(source_path)
    gate_record = file_record(gate_path)
    if dict(adapter["original_snapshot"]) != source_record:
        raise CapacitySnapshotAdapterError("original snapshot file identity mismatch")
    if dict(adapter["source_resource_gate"]) != gate_record:
        raise CapacitySnapshotAdapterError("source gate file identity mismatch")
    if not (
        adapter.get("source_gate_overall_status") == gate.get("overall_status")
        and adapter.get("source_gate_decision") == gate.get("decision")
        and adapter.get("source_gate_checks") == gate.get("checks")
    ):
        raise CapacitySnapshotAdapterError(
            "adapted snapshot did not preserve the source gate result and reasons"
        )

    decision = _validate_source_and_gate(
        source,
        source_record=source_record,
        gate=gate,
        gate_record=gate_record,
        stage=stage,
        current_accepted=current_accepted,
        measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
    )
    if adapter.get("source_policy_decision") != decision.get("decision"):
        raise CapacitySnapshotAdapterError(
            "adapted source-policy decision differs from the current exact policy"
        )
    _require_preserved_values(snapshot, source)
    captured = _parse_utc(source.get("captured_utc"), "captured_utc")
    now = _aware_utc(evaluated_utc or datetime.now(timezone.utc), "evaluated_utc")
    age_seconds = (now - captured).total_seconds()
    if not 0.0 <= age_seconds <= MAX_LAUNCH_AGE_SECONDS:
        raise CapacitySnapshotAdapterError(
            f"adapted snapshot is stale at child launch: age_seconds={age_seconds:.6f}"
        )
    if not (
        snapshot.get("captured_utc") == source.get("captured_utc")
        == adapter.get("original_observation_timestamp")
    ):
        raise CapacitySnapshotAdapterError("observation timestamp was not preserved")
    return {
        **decision,
        "adapter_profile": ADAPTER_PROFILE,
        "original_snapshot_age_seconds": age_seconds,
        "original_snapshot_sha256": source_record["sha256"],
        "source_gate_sha256": gate_record["sha256"],
    }


def adapted_snapshot_is_fresh(
    snapshot_path: Path,
    *,
    evaluated_utc: datetime | None = None,
) -> bool:
    """Return whether an adapted snapshot remains inside the 300-second window."""

    snapshot = _read_bound_json(snapshot_path.expanduser().resolve(), "adapted snapshot")
    adapter = _adapter_evidence(snapshot)
    source_path = _record_path(adapter.get("original_snapshot"), "original snapshot")
    source = _read_bound_json(source_path, "original snapshot")
    if file_record(source_path) != dict(adapter["original_snapshot"]):
        raise CapacitySnapshotAdapterError("original snapshot file identity mismatch")
    captured = _parse_utc(source.get("captured_utc"), "captured_utc")
    if not (
        snapshot.get("captured_utc") == source.get("captured_utc")
        == adapter.get("original_observation_timestamp")
    ):
        raise CapacitySnapshotAdapterError("observation timestamp was not preserved")
    now = _aware_utc(evaluated_utc or datetime.now(timezone.utc), "evaluated_utc")
    age_seconds = (now - captured).total_seconds()
    return 0.0 <= age_seconds <= MAX_LAUNCH_AGE_SECONDS


def _validate_source_and_gate(
    source: Mapping[str, Any],
    *,
    source_record: Mapping[str, Any],
    gate: Mapping[str, Any],
    gate_record: Mapping[str, Any],
    stage: str | None = None,
    current_accepted: int | None = None,
    measured_pilot_bytes_per_geometry: float | None = None,
) -> dict[str, Any]:
    if not (
        source.get("schema") == SOURCE_SNAPSHOT_SCHEMA
        and source.get("campaign_id") == CAMPAIGN_ID
        and source.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and source.get("resource_policy") == RESOURCE_POLICY
        and source.get("swap_policy") == swap_policy.SWAP_POLICY
    ):
        raise CapacitySnapshotAdapterError("source snapshot identity mismatch")
    _require_fields(_mapping(source.get("resources"), "resources"), REQUIRED_RESOURCE_FIELDS)
    _require_fields(_mapping(source.get("licenses"), "licenses"), REQUIRED_LICENSE_FIELDS)
    _require_fields(_mapping(source.get("isolation"), "isolation"), REQUIRED_ISOLATION_FIELDS)
    _validate_finite_tree(source.get("resources"), "resources")
    _validate_finite_tree(source.get("licenses"), "licenses")
    _validate_finite_tree(source.get("isolation"), "isolation")
    _parse_utc(source.get("captured_utc"), "captured_utc")
    _verify_nested_source_identity(source)

    if not _is_sha256(gate_record.get("sha256")):
        raise CapacitySnapshotAdapterError("source resource-gate identity is invalid")
    gate_stage = str(gate.get("current_stage") or "").upper()
    gate_accepted = gate.get("current_accepted")
    if (
        not isinstance(gate_accepted, int)
        or isinstance(gate_accepted, bool)
        or gate_accepted < 0
    ):
        raise CapacitySnapshotAdapterError("source gate progress is invalid")
    evidence = _mapping(gate.get("evidence"), "source gate evidence")
    if not (
        gate.get("schema") == SOURCE_GATE_SCHEMA
        and gate.get("campaign_id") == CAMPAIGN_ID
        and gate.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and gate.get("resource_policy") == RESOURCE_POLICY
        and gate.get("swap_policy") == swap_policy.SWAP_POLICY
        and gate.get("overall_status") in {"PASS", "WAIT"}
        and gate.get("decision")
        in {"READY_FOR_CURRENT_STAGE", "WAITING_FOR_CAPACITY"}
        and evidence.get("resource_snapshot") == source_record
        and gate.get("snapshot_captured_utc")
        == _parse_utc(source["captured_utc"], "captured_utc").isoformat(
            timespec="seconds"
        )
        and isinstance(gate.get("checks"), list)
    ):
        raise CapacitySnapshotAdapterError("source resource-gate identity mismatch")
    use_stage = str(stage or gate_stage).upper()
    use_accepted = int(gate_accepted if current_accepted is None else current_accepted)
    if use_stage != gate_stage or use_accepted != gate_accepted:
        raise CapacitySnapshotAdapterError("source gate stage or progress mismatch")
    decision = swap_policy.evaluate_capacity_snapshot(
        source,
        stage=use_stage,
        current_accepted=use_accepted,
        measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
    )
    expected_status = "PASS" if decision["pass"] else "WAIT"
    expected_gate_decision = (
        "READY_FOR_CURRENT_STAGE" if decision["pass"] else "WAITING_FOR_CAPACITY"
    )
    if gate.get("overall_status") != expected_status or gate.get("decision") != expected_gate_decision:
        raise CapacitySnapshotAdapterError("source gate result differs from source policy")
    checks = _gate_checks_by_name(gate["checks"])
    for name, passed in decision["checks"].items():
        if checks.get(name) is not bool(passed):
            raise CapacitySnapshotAdapterError(
                f"source gate reason differs from source policy: {name}"
            )
    return decision


def _require_preserved_values(
    adapted: Mapping[str, Any], source: Mapping[str, Any]
) -> None:
    restored = copy.deepcopy(dict(adapted))
    restored.pop("capacity_schema_adapter", None)
    restored.pop("operational_resource_policy", None)
    restored["schema"] = SOURCE_SNAPSHOT_SCHEMA
    if restored != dict(source):
        raise CapacitySnapshotAdapterError(
            "adapted snapshot did not preserve the complete source payload"
        )


def _adapter_evidence(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    adapter = _mapping(snapshot.get("capacity_schema_adapter"), "adapter evidence")
    if not (
        snapshot.get("schema") == TARGET_SNAPSHOT_SCHEMA
        and snapshot.get("operational_resource_policy") == swap_policy.SWAP_POLICY
        and adapter.get("profile") == ADAPTER_PROFILE
        and adapter.get("original_schema") == SOURCE_SNAPSHOT_SCHEMA
        and adapter.get("target_schema") == TARGET_SNAPSHOT_SCHEMA
        and adapter.get("maximum_launch_age_seconds") == MAX_LAUNCH_AGE_SECONDS
        and adapter.get("resource_values_preserved") is True
        and adapter.get("license_values_preserved") is True
        and adapter.get("isolation_values_preserved") is True
        and adapter.get("gate_result_preserved") is True
        and adapter.get("gate_reasons_preserved") is True
        and adapter.get("observation_timestamp_preserved") is True
        and adapter.get("adapter_creation_refreshes_observation_timestamp") is False
        and adapter.get("resource_values_relaxed_or_recalculated") is False
    ):
        raise CapacitySnapshotAdapterError("adapted snapshot contract mismatch")
    _parse_utc(adapter.get("conversion_timestamp"), "conversion_timestamp")
    if adapter.get("canonical_payload_sha256") != _canonical_adapter_payload_sha256(
        snapshot
    ):
        raise CapacitySnapshotAdapterError("adapted snapshot payload hash mismatch")
    return adapter


def _verify_nested_source_identity(source: Mapping[str, Any]) -> None:
    nested = source.get("source_snapshot")
    path = _record_path(nested, "nested source snapshot")
    if file_record(path) != dict(nested):
        raise CapacitySnapshotAdapterError("nested source snapshot identity mismatch")
    legacy = _read_bound_json(path, "nested source snapshot")
    if not (
        legacy.get("schema") == TARGET_SNAPSHOT_SCHEMA
        and legacy.get("campaign_id") == CAMPAIGN_ID
        and legacy.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and legacy.get("captured_utc") == source.get("captured_utc")
    ):
        raise CapacitySnapshotAdapterError("nested source snapshot contract mismatch")


def _gate_checks_by_name(value: list[Any]) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise CapacitySnapshotAdapterError("source gate check is not an object")
        name = item.get("name")
        passed = item.get("pass")
        if not isinstance(name, str) or not name or not isinstance(passed, bool):
            raise CapacitySnapshotAdapterError("source gate check is malformed")
        if name in checks and checks[name] is not passed:
            raise CapacitySnapshotAdapterError(
                f"source gate contains contradictory check: {name}"
            )
        checks[name] = passed
    return checks


def _canonical_adapter_payload_sha256(snapshot: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(snapshot))
    adapter = payload.get("capacity_schema_adapter")
    if isinstance(adapter, dict):
        adapter.pop("canonical_payload_sha256", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise CapacitySnapshotAdapterError(f"bound file is missing: {resolved}")
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _record_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path_text = record.get("path")
    size = record.get("size_bytes")
    digest = record.get("sha256")
    if not (
        isinstance(path_text, str)
        and path_text
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size > 0
        and _is_sha256(digest)
    ):
        raise CapacitySnapshotAdapterError(f"{label} identity is incomplete")
    path = Path(path_text).expanduser().resolve()
    if file_record(path) != dict(record):
        raise CapacitySnapshotAdapterError(f"{label} identity mismatch")
    return path


def _read_bound_json(path: Path, label: str) -> dict[str, Any]:
    before = path.stat()
    payload = path.read_bytes()
    after = path.stat()
    if _stat_identity(before) != _stat_identity(after):
        raise CapacitySnapshotAdapterError(f"{label} changed while reading")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapacitySnapshotAdapterError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CapacitySnapshotAdapterError(f"{label} root is not an object")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapacitySnapshotAdapterError(f"{label} must be an object")
    return value


def _require_fields(value: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise CapacitySnapshotAdapterError(
            f"required resource fields are missing: {missing}"
        )


def _validate_finite_tree(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite_tree(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_tree(item, f"{label}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise CapacitySnapshotAdapterError(f"{label} is nonfinite")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CapacitySnapshotAdapterError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CapacitySnapshotAdapterError(f"{label} is invalid") from exc
    return _aware_utc(parsed, label)


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise CapacitySnapshotAdapterError(f"{label} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_text_new(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _stat_identity(value: Any) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


__all__ = [
    "ADAPTER_PROFILE",
    "ADAPTER_RECEIPT_NAME",
    "ADAPTED_SNAPSHOT_NAME",
    "CapacitySnapshotAdapterError",
    "MAX_LAUNCH_AGE_SECONDS",
    "adapted_snapshot_is_fresh",
    "evaluate_adapted_capacity_snapshot",
    "file_record",
    "normalize_capacity_snapshot_for_stage_launcher",
]
