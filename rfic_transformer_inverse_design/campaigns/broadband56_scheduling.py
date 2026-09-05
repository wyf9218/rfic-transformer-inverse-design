"""Read-only, evidence-bound admission shared by all launch boundaries.

Historical snapshots without the operational overlay retain their legacy
policy. The live overlay never substitutes a caller's counter for observations.
Missing per-tool measurements permit single-worker operation, not scaling.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import broadband56_swap_override_policy as swap
from .broadband56_capacity_policy import (
    CapacityPolicyError, HEALTHY_STREAK_REQUIREMENT, adaptive_concurrency as base_concurrency,
    SNAPSHOT_SCHEMA as RAW_SNAPSHOT_SCHEMA,
)

MAX_SNAPSHOT_AGE_SECONDS = 300
MAX_HISTORY_GAP_SECONDS = 180
HISTORY_WINDOW_SECONDS = 900
TOOL_NAMES = ("cadence", "calibre", "emx")


def _utc(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CapacityPolicyError("invalid scheduling observation timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CapacityPolicyError("scheduling timestamp must be timezone-aware")
    return result.astimezone(timezone.utc)


def file_record(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    data = path.read_bytes()
    return {"path": str(path), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CapacityPolicyError("scheduling evidence must be an object")
    return value


def _bound(record: Any) -> tuple[Path, dict[str, Any]]:
    if not isinstance(record, Mapping):
        raise CapacityPolicyError("missing scheduling source identity")
    path = Path(str(record.get("path", "")))
    if not path.is_absolute() or file_record(path) != dict(record):
        raise CapacityPolicyError("scheduling source identity mismatch")
    return path, _read(path)


def _original(path: Path, *, now: datetime | None = None) -> tuple[Path, dict[str, Any]]:
    payload = _read(path)
    if "capacity_schema_adapter" in payload:
        # Validate the entire adapter, including equality with its source bytes.
        from .broadband56_capacity_snapshot_adapter import evaluate_adapted_capacity_snapshot

        gate = _bound(payload["capacity_schema_adapter"]["source_resource_gate"])[1]
        evaluate_adapted_capacity_snapshot(
            payload, stage=gate["current_stage"], current_accepted=gate["current_accepted"],
            measured_pilot_bytes_per_geometry=gate.get("measured_pilot_bytes_per_geometry"),
            evaluated_utc=now,
        )
        path, payload = _bound(payload["capacity_schema_adapter"]["original_snapshot"])
    return path, payload


def healthy_history(
    snapshot_path: Path, *, campaign_root: Path, stage: str, current_accepted: int,
    measured_pilot_bytes_per_geometry: float | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    now = _utc(now or datetime.now(timezone.utc))
    path, current = _original(snapshot_path, now=now)
    captured = _utc(current.get("captured_utc"))
    age = (now - captured).total_seconds()
    if not 0 <= age <= MAX_SNAPSHOT_AGE_SECONDS:
        raise CapacityPolicyError("latest scheduling observation is stale or in the future")
    lease = current.get("supervisor_lease")
    _bound(lease)
    identity_fields = ("campaign_id", "contract_fingerprint_sha256", "supervisor_lease",
                       "operational_overlay_manifest", "owner_swap_override_receipt")
    paths = set((campaign_root / "resource_snapshots").glob("swap_override_*.json"))
    paths.add(path)
    observations = []
    for candidate_path in paths:
        item = _read(candidate_path)
        stamp = _utc(item.get("captured_utc"))
        if stamp > captured or (captured - stamp).total_seconds() > HISTORY_WINDOW_SECONDS:
            continue
        if any(item.get(key) != current.get(key) for key in identity_fields):
            continue
        _, raw = _bound(item.get("source_snapshot"))
        if (raw.get("schema") != RAW_SNAPSHOT_SCHEMA
                or raw.get("campaign_id") != current.get("campaign_id")
                or raw.get("contract_fingerprint_sha256") != current.get("contract_fingerprint_sha256")):
            raise CapacityPolicyError("raw scheduling source contract mismatch")
        # The overlay may change swap classification and isolation identity,
        # but must not turn an old raw observation into a new health sample.
        if _utc(raw.get("captured_utc")) != stamp:
            raise CapacityPolicyError("raw and overlay capture timestamps differ")
        for name, value in raw.get("resources", {}).items():
            if name != "active_swap_thrashing" and item.get("resources", {}).get(name) != value:
                raise CapacityPolicyError("overlay changed an observed resource value")
        if raw.get("licenses") != item.get("licenses"):
            raise CapacityPolicyError("overlay changed observed license values")
        policy = swap.evaluate_capacity_snapshot(
            item, stage=stage, current_accepted=current_accepted,
            measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
        )
        healthy = (policy["pass"] and policy["metrics"]["normalized_load1"] <= 0.90
                   and policy["metrics"]["normalized_load5"] <= 0.95
                   and policy["metrics"]["active_simulator_jobs"] == 0)
        observations.append((stamp, item["source_snapshot"]["sha256"], healthy,
                             file_record(candidate_path)))
    observations.sort(key=lambda item: (item[0], item[1], item[3]["path"]))
    streak = []
    seen_sources: set[str] = set()
    seen_times: set[datetime] = set()
    previous = None
    for stamp, digest, healthy, record in observations:
        if digest in seen_sources or stamp in seen_times:
            raise CapacityPolicyError("duplicate scheduling source or observation time")
        seen_sources.add(digest)
        seen_times.add(stamp)
        if previous is not None:
            gap = (stamp - previous).total_seconds()
            if gap < 60:
                # Overlapping sixty-second probes are not independent checks.
                raise CapacityPolicyError("overlapping scheduling observation windows")
            if gap > MAX_HISTORY_GAP_SECONDS:
                streak = []
        previous = stamp
        streak = [*streak, record] if healthy else []
    return {"healthy_check_streak": len(streak), "evidence": streak,
            "latest_snapshot": file_record(path), "captured_utc": captured.isoformat(),
            "effective_healthy_streak_requirement": HEALTHY_STREAK_REQUIREMENT}


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise CapacityPolicyError(f"{label} must be a positive integer")
    return value


def measured_worker_cap(snapshot: Mapping[str, Any]) -> tuple[int, str]:
    """Require actual per-tool observations; a license boolean is not a seat count.

    A private probe can attach this source-bound measurement once available.
    No measurement producer or live installation is implied by this consumer.
    """
    record = snapshot.get("per_tool_capacity_evidence")
    if record is None:
        return 1, "PER_TOOL_SEATS_THREADS_PEAK_RSS_NOT_MEASURED"
    _, evidence = _bound(record)
    if (evidence.get("schema") != "rfic_transformer.broadband56_tool_capacity.v1"
            or evidence.get("campaign_id") != snapshot.get("campaign_id")
            or evidence.get("contract_fingerprint_sha256") != snapshot.get("contract_fingerprint_sha256")
            or evidence.get("supervisor_lease") != snapshot.get("supervisor_lease")
            or _utc(evidence.get("captured_utc")) != _utc(snapshot.get("captured_utc"))):
        raise CapacityPolicyError("per-tool capacity observation identity mismatch")
    resources = snapshot["resources"]
    spare = resources["memory_available_bytes"] - math.ceil(resources["memory_total_bytes"] * 0.20)
    cpu_budget = math.floor(resources["logical_cpu_count"] * 0.10)
    caps = [snapshot["licenses"]["simulator_license_capacity"]]
    for tool in TOOL_NAMES:
        item = evidence["tools"][tool]
        _, source = _bound(item["measurement_source"])
        if source.get("tool") != tool or source.get("observed") != item.get("observed"):
            raise CapacityPolicyError("per-tool capacity differs from its measurement source")
        observed = item["observed"]
        seats = observed["free_seats"]
        if type(seats) is not int or seats < 0:
            raise CapacityPolicyError("free_seats must be a nonnegative integer")
        threads = _positive_int(observed["threads_per_job"], "threads_per_job")
        rss = _positive_int(observed["peak_rss_bytes_per_job"], "peak_rss_bytes_per_job")
        caps.extend((seats, cpu_budget // threads, max(0, spare) // rss))
    return max(0, min(caps)), "MEASURED_PER_TOOL_CAPACITY"


def completed_pilot_execution(campaign_root: Path, *, current_accepted: int) -> dict[str, Any]:
    """Read committed attempts only; a requested level is not an observed level.

    The next 2 -> 4 trial needs accepted outputs and observed native EMX
    overlap from the same complete pipeline attempt. This is not proof that
    every tool ran at that level, or that the level is fastest in production.
    Callers separately validate the authorized campaign progress/handoff chain;
    this read-only check adds execution evidence, never acceptance authority.
    """
    from .broadband56_stage_execution import expected_stage_role_order
    from .broadband56_stage_progress import validate_stage_progress_receipt

    proven, evidence = 1, []
    expected = list(expected_stage_role_order("PILOT_1000"))
    completed_roles = expected[:expected.index("stage_attempt_finalizer") + 1]
    for path in sorted((campaign_root / "stages").glob("*/STAGE_PROGRESS_RECEIPT.json")):
        progress = _read(path)
        if progress.get("stage") != "PILOT_1000":
            continue
        trace_path = path.parent / "backend/STAGE_EXECUTION_TRACE.json"
        if not trace_path.is_file():
            continue  # Legacy acceptance carries no native-concurrency proof.
        trace = _read(trace_path)
        roles = trace.get("roles", [])
        emx = next((r for r in roles if r.get("role") == "exact_audited_gds_emx_runner"), None)
        if not emx or not emx.get("native_observation", {}).get("receipt"):
            continue
        failures = validate_stage_progress_receipt(
            progress, stage="PILOT_1000", attempt_index=progress["attempt_index"],
            accepted_before=progress["accepted_before"],
            prior_progress_receipt_sha256=progress["prior_progress_receipt_sha256"],
            backend_manifest_sha256=progress["backend_identity_manifest_sha256"],
            authorization_receipt_sha256=progress["full_campaign_authorization_receipt_sha256"],
            verify_artifacts=True, artifact_root=path.parent,
        )
        if failures or progress["accepted_after"] > current_accepted:
            raise CapacityPolicyError("pilot execution lacks committed valid progress")
        if (trace.get("schema") != "rfic_transformer.broadband56_v2_stage_execution_trace.v1"
                or trace.get("overall_status") != "INCOMPLETE"
                or trace.get("campaign_id") != progress["campaign_id"]
                or trace.get("contract_fingerprint_sha256") != progress["contract_fingerprint_sha256"]
                or trace.get("stage") != "PILOT_1000"
                or trace.get("decision") != progress["decision"]
                or trace.get("all_role_return_codes_zero") is not True
                or trace.get("all_role_receipts_pass") is not True
                or trace.get("expected_terminal_role_order") != expected
                or trace.get("role_order") != completed_roles
                or trace.get("role_order") != [r.get("role") for r in roles]
                or any(r.get("return_code") != 0 for r in roles)):
            raise CapacityPolicyError("pilot execution trace is not a completed role chain")
        role_by_name = {r["role"]: r for r in roles}
        required = set(completed_roles)
        if len(role_by_name) != len(roles) or not required <= role_by_name.keys():
            raise CapacityPolicyError("pilot execution omits required physical stages")
        inputs = [file_record(path), file_record(trace_path)]
        receipts = {}
        for name in required:
            record = role_by_name[name]["receipt"]
            receipt_path, receipts[name] = _bound(record)
            if not receipt_path.is_relative_to(path.parent):
                raise CapacityPolicyError("pilot role receipt escapes its committed attempt")
            receipt = receipts[name]
            if (receipt.get("overall_status") != "PASS"
                    or any(key in receipt and receipt[key] != progress[key] for key in
                           ("campaign_id", "contract_fingerprint_sha256", "stage"))):
                raise CapacityPolicyError("pilot physical role receipt is not PASS for this contract")
            inputs.append(record)
        native_record = emx["native_observation"]["receipt"]
        native_path, native = _bound(native_record)
        inputs.append(native_record)
        context_path, context = _bound(native["bindings"]["stage_context"])
        _, backend = _bound(native["bindings"]["backend_manifest"])
        inputs.extend(native["bindings"].values())
        if (not native_path.is_relative_to(path.parent)
                or not context_path.is_relative_to(path.parent)
                or native.get("schema") != "rfic_transformer.broadband56_native_role_observation.v1"
                or native.get("role") != "exact_audited_gds_emx_runner"
                or context.get("schema") != "rfic_transformer.broadband56_v2_stage_context.v2"
                or any(context.get(key) != progress[key] for key in
                       ("campaign_id", "contract_fingerprint_sha256", "stage"))
                or native["bindings"]["backend_manifest"]["sha256"] != progress["backend_identity_manifest_sha256"]
                or context.get("backend_identity_manifest") != native["bindings"]["backend_manifest"]
                or context.get("full_campaign_authorization_receipt", {}).get("sha256")
                != progress["full_campaign_authorization_receipt_sha256"]
                or backend.get("contract_fingerprint_sha256") != progress["contract_fingerprint_sha256"]
                or context.get("current_accepted") != progress["accepted_before"]
                or native.get("command_argv_sha256") != emx.get("command_argv_sha256")):
            raise CapacityPolicyError("native observation is not bound to this pilot execution")
        configured = context.get("max_concurrency")
        observed = (native.get("sampled_peak_native_concurrency") or {}).get("emx")
        emx_result = receipts["exact_audited_gds_emx_runner"]
        qa_result = receipts["full_band_s4p_qa_builder"]
        if (native.get("observation_status") != "RECORDED"
                or native.get("errors")
                or type(configured) is not int or configured not in (2, 4)
                or native.get("backend_admitted_max_concurrency") != configured
                or native.get("executor_argv_max_concurrency") != configured
                or emx_result.get("max_concurrency") != configured
                or emx_result.get("emx_fail_count") != 0 or qa_result.get("qa_fail_count") != 0
                or emx_result.get("emx_pass_count", 0) < progress["accepted_this_attempt"]
                or qa_result.get("qa_pass_count", 0) < progress["accepted_this_attempt"]
                or observed != configured or progress["accepted_this_attempt"] < configured):
            continue
        sample_path = Path(native["samples"]["path"])
        if not sample_path.resolve().is_relative_to(path.parent) or file_record(sample_path) != native["samples"]:
            raise CapacityPolicyError("native pilot sample identity mismatch")
        samples = [json.loads(line) for line in sample_path.read_text().splitlines()]
        if not samples or len(samples) != native.get("sample_count"):
            raise CapacityPolicyError("native pilot sample count mismatch")
        started, finished = _utc(native["started_utc"]), _utc(native["finished_utc"])
        previous = None
        for sample in samples:
            captured, verified = _utc(sample["captured_utc"]), _utc(sample["verified_utc"])
            processes = sample["native_processes"]
            if (not started <= captured <= verified <= finished
                    or (previous is not None and captured <= previous)
                    or len({p["pid"] for p in processes}) != len(processes)
                    or any(p["uid"] != native["root_process"]["uid"]
                           or p["boot_id"] != native["root_process"]["boot_id"]
                           or p["start_ticks"] < native["root_process"]["start_ticks"]
                           for p in processes)
                    or sample["counts"] != {tool: sum(p["tool"] == tool for p in processes)
                                            for tool in TOOL_NAMES}):
                raise CapacityPolicyError("native pilot samples do not prove simultaneous distinct processes")
            previous = captured
        if max(s["counts"]["emx"] for s in samples) != configured:
            raise CapacityPolicyError("native pilot peak differs from sampled counts")
        finalizer = receipts["stage_attempt_finalizer"]
        if (finalizer.get("accepted_before") != progress["accepted_before"]
                or finalizer.get("accepted_after") != progress["accepted_after"]
                or finalizer.get("decision") != progress["decision"]
                or finalizer.get("simulator_invoked_by_finalizer") is not False
                or _bound(finalizer["progress_receipt"])[0].read_bytes() != path.read_bytes()):
            raise CapacityPolicyError("pilot finalizer and committed progress differ")
        for record in inputs:
            if file_record(Path(record["path"])) != record:
                raise CapacityPolicyError("pilot execution source changed during admission")
        if configured == 4 and proven < 2:
            raise CapacityPolicyError("four-worker trial lacks a prior completed two-worker trial")
        proven = max(proven, configured)
        evidence.append({"configured_limit": configured, "observed_emx_peak": observed,
                         "accepted_increment": progress["accepted_this_attempt"],
                         "sources": inputs, "samples": native["samples"]})
    return {"proven_pilot_limit": proven, "pilot_execution_evidence": evidence,
            "benchmark_levels_completed": [], "production_optimum_proven": False}


def concurrency_for_snapshot(
    *, snapshot_path: Path, campaign_root: Path, stage: str, current_accepted: int,
    policy: Mapping[str, Any], legacy_policy: Any,
    pilot_1000_safe_concurrency: int | None = None,
    measured_pilot_bytes_per_geometry: float | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    path, snapshot = _original(snapshot_path, now=now)
    metrics = policy["metrics"]
    kwargs = dict(
        stage=stage, logical_cpu_count=metrics["logical_cpu_count"],
        simulator_license_capacity=metrics["simulator_license_capacity"],
        current_concurrency=None, healthy_check_streak=0,
        normalized_load1=metrics["normalized_load1"], iowait_percent=metrics["iowait_percent"],
        available_memory_fraction=metrics["available_memory_fraction"],
        active_swap_thrashing=metrics["active_swap_thrashing"],
        licenses_available=policy["checks"]["license_gate"],
        pilot_1000_safe_concurrency=pilot_1000_safe_concurrency,
    )
    if snapshot.get("schema") != swap.SNAPSHOT_SCHEMA:
        return legacy_policy(**kwargs)
    policy = swap.evaluate_capacity_snapshot(
        snapshot, stage=stage, current_accepted=current_accepted,
        measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
    )
    history = healthy_history(
        path, campaign_root=campaign_root, stage=stage, current_accepted=current_accepted,
        measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry, now=now,
    )
    cap, reason = measured_worker_cap(snapshot)
    # Admission limits are not proof that every native tool reached that level.
    pilot_stage = stage in {"GOLDEN", "PILOT_32", "PILOT_1000"}
    execution = completed_pilot_execution(campaign_root, current_accepted=current_accepted) if stage == "PILOT_1000" else {}
    current = execution.get("proven_pilot_limit", 1)
    if stage == "GOLDEN":
        requested = 1
    elif pilot_stage:
        requested = 4 if current >= 2 else 2
    else:
        requested = pilot_1000_safe_concurrency
    requested = _positive_int(requested, "requested concurrency")
    cap = min(cap, requested)
    kwargs.update(current_concurrency=current, healthy_check_streak=history["healthy_check_streak"])
    if stage == "PILOT_1000":
        kwargs["pilot_trial_levels"] = (1, 2, 4)
    base = base_concurrency(**kwargs)
    count = min(cap, base["concurrency"])
    if history["healthy_check_streak"] == 0:
        count = min(count, 1)
    if cap > 1 and 0 < history["healthy_check_streak"] < HEALTHY_STREAK_REQUIREMENT:
        # Let the sole controller's normal timed probe loop collect the rest.
        # Otherwise each long single-worker attempt expires the entire history.
        count = 0
        reason = "COLLECTING_FIVE_FRESH_HEALTHY_CHECKS"
    if not policy["pass"]:
        count = 0
    if stage == "PILOT_1000":
        count = max(level for level in (0, 1, 2, 4) if level <= count)
    return {"concurrency": count, "hard_cap": cap,
            "requested_concurrency": requested, "action": "HOLD" if count else "PAUSE_NEW_LAUNCHES",
            "reasons": [reason], **history, **execution}
