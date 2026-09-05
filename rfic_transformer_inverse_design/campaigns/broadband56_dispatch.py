"""Bounded refill for existing production executors; never an owner or solver."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from . import broadband56_scheduling as scheduling
from .broadband56_capacity_policy import CapacityPolicyError


class StageAdmission:
    """Consume the sole controller's live history, caching expensive validation."""

    def __init__(self, context_path: Path, history_path: Path, executor_capacity: int):
        self.context_pin = scheduling.file_record(context_path)
        self.context = scheduling._read(context_path)
        self.history_path = history_path.resolve(strict=True)
        self.capacity = executor_capacity
        self.root = Path(self.context["campaign_root"])
        self.initial_pin = self.context["initial_resource_snapshot"]
        _, initial = scheduling._bound(self.initial_pin)
        fixed = scheduling.fixed_generation_policy(initial)
        if (fixed is None or executor_capacity != fixed["requested_concurrency"]
                or self.context.get("schema") != "rfic_transformer.broadband56_v2_stage_context.v2"
                or self.context.get("max_concurrency") != executor_capacity
                or self.context["stage"] in {"GOLDEN", "PILOT_32"}
                or self.context.get("stage_resource_history") != str(self.history_path)
                or not self.history_path.is_relative_to(self.root / "scheduling_history")
                or self.context.get("backend_identity_manifest")
                != scheduling._bound(initial["supervisor_lease"])[1]["backend_identity_manifest"]):
            raise CapacityPolicyError("executor history is not bound to this fixed48 stage")
        self.bindings = {key: initial[key] for key in (
            "campaign_id", "contract_fingerprint_sha256", "supervisor_lease",
            "operational_overlay_manifest", "owner_swap_override_receipt")}
        if any(self.context.get(key) != initial[key] for key in
               ("campaign_id", "contract_fingerprint_sha256")):
            raise CapacityPolicyError("executor stage contract differs from history")
        self.cached_pin = None
        self.captured = None
        self.count = 0
        self.last_decision = None

    def __call__(self) -> int:
        now = datetime.now(timezone.utc)
        pin = scheduling._read(self.history_path)
        path, state = scheduling._bound(pin)
        if path.parent != self.history_path.parent:
            raise CapacityPolicyError("resource state escapes its stage history")
        if (state.get("schema") != "rfic_transformer.broadband56_stage_resource_history.v1"
                or state.get("bindings") != self.bindings
                or state.get("stage") != self.context["stage"]
                or state.get("initial_snapshot") != self.initial_pin
                or state.get("overall_status") != "OBSERVED"
                or state.get("error") is not None):
            raise CapacityPolicyError("resource sampler failed or changed stage identity")
        source_path, source = scheduling._bound(state.get("latest_snapshot"))
        captured = scheduling._utc(source["captured_utc"])
        if not 0 <= (now - captured).total_seconds() <= scheduling.MAX_SNAPSHOT_AGE_SECONDS:
            # Lack of a new observation is a wait, never permission to reuse an old PASS.
            if captured > now:
                raise CapacityPolicyError("resource observation is future dated")
            return 0
        if pin != self.cached_pin:
            if self.captured is not None and captured <= self.captured:
                raise CapacityPolicyError("resource history moved backwards or repeated a sample")
            if any(source.get(key) != value for key, value in self.bindings.items()):
                raise CapacityPolicyError("live resource snapshot changed executor identity")
            policy = scheduling.swap.evaluate_capacity_snapshot(
                source, stage=self.context["stage"], current_accepted=self.context["current_accepted"],
                measured_pilot_bytes_per_geometry=self.context.get("measured_pilot_bytes_per_geometry"))
            self.last_decision = scheduling.concurrency_for_snapshot(
                snapshot_path=source_path, campaign_root=self.root,
                stage=self.context["stage"], current_accepted=self.context["current_accepted"],
                policy=policy, legacy_policy=lambda **_: None, now=now,
                measured_pilot_bytes_per_geometry=self.context.get("measured_pilot_bytes_per_geometry"))
            self.count = min(self.capacity, self.last_decision["concurrency"])
            self.captured, self.cached_pin = captured, pin
        return self.count


def stage_admission(executor_capacity: int):
    """Only the fixed48 backend supplies this pointer; legacy runs stay bounded."""
    history = os.environ.get("BROADBAND56_STAGE_RESOURCE_HISTORY")
    context = os.environ.get("BROADBAND56_STAGE_CONTEXT")
    if not history:
        if context:
            payload = scheduling._read(Path(context))
            if payload.get("scheduling_decision", {}).get("fixed_generation_policy"):
                raise CapacityPolicyError("fixed48 executor is missing live resource history")
        return None
    if not context:
        raise CapacityPolicyError("live resource history is missing its stage context")
    return StageAdmission(Path(context), Path(history), executor_capacity)


def bounded_completed(count, invoke, *, max_workers, admission=None, receipt_dir: Path,
                      poll_seconds=1.0):
    """Yield (input index, future); refill on completion without a wave barrier.

    A gate exception stops only new submissions. The existing pool drains;
    immutable per-candidate artifacts survive and undispatched indexes are
    recorded separately, never converted into geometry failures or acceptance.
    """
    if type(count) is not int or count < 0 or type(max_workers) is not int or max_workers < 1:
        raise CapacityPolicyError("invalid bounded executor size")
    if poll_seconds <= 0:
        raise ValueError("dispatch poll interval must be positive")
    receipt_dir.mkdir(parents=True, exist_ok=False)
    submitted, completed, peak, failure = 0, set(), 0, None
    pending = {}
    started = datetime.now(timezone.utc).isoformat()
    with (receipt_dir / "DISPATCH_EVENTS.jsonl").open("x", encoding="utf-8") as log:
        def record(event, **values):
            log.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(),
                                  "event": event, **values}, sort_keys=True) + "\n")
            log.flush()
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                while pending or submitted < count:
                    allowed = 0
                    if failure is None and submitted < count:
                        try:
                            allowed = max_workers if admission is None else admission()
                            if type(allowed) is not int or not 0 <= allowed <= max_workers:
                                raise CapacityPolicyError("admission exceeds the executor capacity")
                        except Exception as exc:
                            failure = f"{type(exc).__name__}: {exc}"
                            record("STOP_NEW_DISPATCH", error=failure)
                    while failure is None and submitted < count and len(pending) < allowed:
                        pending[executor.submit(invoke, submitted)] = submitted
                        record("SUBMITTED", input_index=submitted, admitted_limit=allowed)
                        submitted += 1
                        peak = max(peak, len(pending))
                    if not pending:
                        if failure is not None or submitted == count:
                            break
                        time.sleep(poll_seconds)
                        continue
                    done, _ = wait(pending, timeout=poll_seconds, return_when=FIRST_COMPLETED)
                    for future in sorted(done, key=lambda f: pending[f]):
                        index = pending.pop(future)
                        completed.add(index)
                        record("DELEGATE_RETURNED", input_index=index)
                        yield index, future
            if failure:
                raise CapacityPolicyError(f"dispatch failed closed after drain: {failure}")
        except BaseException as exc:
            failure = failure or f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # ThreadPoolExecutor.__exit__ drains before this receipt is written.
            completed.update(pending.values())
            receipt = {"schema": "rfic_transformer.broadband56_bounded_dispatch.v1",
                       "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
                       "overall_status": "DISPATCH_COMPLETED" if submitted == count and not failure
                                         else "INCOMPLETE_DISPATCH",
                       "input_count": count, "submitted_count": submitted,
                       "delegate_returned_indexes": sorted(completed),
                       "not_dispatched_indexes": list(range(submitted, count)),
                       "executor_capacity": max_workers, "peak_inflight_delegates": peak,
                       "error": failure, "accepted_increment": 0,
                       "native_concurrency_proven": False}
            with (receipt_dir / "DISPATCH_RECEIPT.json").open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, indent=2, sort_keys=True)
                stream.write("\n")
