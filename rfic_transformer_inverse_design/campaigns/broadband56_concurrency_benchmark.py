"""Bounded concurrency trials, called only inside the authoritative supervisor.

The caller supplies the existing hash-verified EMX delegate, a live admission
gate, and process telemetry. This module never creates a supervisor or changes
production data. Repeated benchmark outputs are not accepted geometries.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


LEVELS = (1, 2, 4, 8, 16, 32, 48)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _validated_gate(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value.get("pass"), bool):
        raise ValueError("admission gate must contain an explicit boolean pass")
    evidence = value.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("admission gate has no evidence identity")
    path = Path(str(evidence.get("path", "")))
    if not path.is_file() or any(_record(path)[key] != evidence.get(key)
                                 for key in ("size_bytes", "sha256")):
        raise ValueError("admission gate evidence mismatch")
    return dict(value)


def run_trial(
    *,
    jobs: Sequence[Mapping[str, Any]],
    concurrency: int,
    out_dir: Path,
    workload_identity: Mapping[str, Any],
    authority_check: Callable[[], None],
    admission_gate: Callable[[int], Mapping[str, Any]],
    execute: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
    telemetry: Callable[[], Mapping[str, Any]],
    sample_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    """Drain healthy children on gate failure; never fill an unbounded queue.

    execute must return PASS only after the pinned fresh-EMX receipt and the
    exact four-port, 56-point output pass the existing physical validators.
    authority_check must verify the caller's PID, sole lease, and held lock.
    admission_gate is sampled before every replenishment and while jobs run.
    Its implementation must enforce the approved resource and license policy.
    """
    if type(concurrency) is not int or concurrency not in LEVELS:
        raise ValueError("unsupported benchmark concurrency")
    if len(jobs) < concurrency:
        raise ValueError("a trial cannot test more workers than available jobs")
    ids = [job.get("job_id") for job in jobs]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("benchmark job IDs must be nonempty and unique")
    if not math.isfinite(sample_interval_seconds) or sample_interval_seconds <= 0:
        raise ValueError("sample interval must be finite and positive")
    authority_check()
    workload_path = Path(str(workload_identity.get("path", "")))
    if not workload_path.is_file() or _record(workload_path) != dict(workload_identity):
        raise ValueError("frozen workload identity mismatch")
    if json.loads(workload_path.read_text(encoding="utf-8")).get("jobs") != list(jobs):
        raise ValueError("submitted jobs differ from the hash-bound workload")
    # Output creation is deliberately after the identity checks and exclusive.
    out_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        initial = _validated_gate(admission_gate(concurrency))
    except Exception as exc:
        _write_new(out_dir / "PREFLIGHT_FAILURE.json", {
            "overall_status": "FAIL", "simulator_action_taken": False,
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise
    _write_new(out_dir / "INITIAL_ADMISSION_GATE.json", initial)
    started_utc, started = _utc(), time.monotonic()
    results: dict[int, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    submitted = 0
    peak_inflight = 0
    stop_reason = None if initial["pass"] else "INITIAL_RESOURCE_WAIT"
    last_sample = float("-inf")

    def invoke(index: int) -> dict[str, Any]:
        job_dir = out_dir / f"job_{index + 1:06d}"
        # The existing delegate owns creation of its exclusive job directory.
        begin = time.monotonic()
        try:
            value = dict(execute(jobs[index], job_dir))
            if value.get("overall_status") not in {"PASS", "FAIL"}:
                raise ValueError("delegate returned no terminal PASS/FAIL")
        except Exception as exc:
            value = {"overall_status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        return {"job_id": ids[index], "sequence": index + 1,
                "wall_seconds": time.monotonic() - begin, "result": value}

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        pending: dict[Any, int] = {}
        while pending or (submitted < len(jobs) and stop_reason is None):
            now = time.monotonic()
            if now - last_sample >= sample_interval_seconds:
                try:
                    authority_check()
                    admission = _validated_gate(admission_gate(concurrency))
                    observed = dict(telemetry())
                    sample = {"captured_utc": _utc(), "admission": admission,
                              "inflight_delegates": len(pending), "telemetry": observed}
                    _write_new(out_dir / f"RESOURCE_SAMPLE_{len(samples) + 1:06d}.json", sample)
                    samples.append(sample)
                    solver_count = observed.get("active_solver_processes")
                    if type(solver_count) is not int or solver_count < 0:
                        stop_reason = "SOLVER_PROCESS_TELEMETRY_INVALID_DRAIN_ONLY"
                    elif solver_count > concurrency:
                        stop_reason = "SOLVER_CONCURRENCY_EXCEEDED_DRAIN_ONLY"
                    elif not admission["pass"]:
                        stop_reason = "RESOURCE_GATE_CHANGED_DRAIN_ONLY"
                except Exception as exc:
                    stop_reason = f"GATE_OR_TELEMETRY_ERROR: {type(exc).__name__}: {exc}"
                last_sample = now
            while stop_reason is None and submitted < len(jobs) and len(pending) < concurrency:
                pending[executor.submit(invoke, submitted)] = submitted
                submitted += 1
                peak_inflight = max(peak_inflight, len(pending))
            if not pending:
                break
            done, _ = wait(pending, timeout=sample_interval_seconds, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                results[index] = future.result()
                _write_new(out_dir / f"JOB_RESULT_{index + 1:06d}.json", results[index])
            # Re-evaluate before replenishing, without killing active delegates.
            if done:
                last_sample = float("-inf")

    elapsed = time.monotonic() - started
    ordered = [results[index] for index in sorted(results)]
    passed = sum(item["result"]["overall_status"] == "PASS" for item in ordered)
    failed = len(ordered) - passed
    complete = len(ordered) == len(jobs)
    solver_observations = [s["telemetry"].get("active_solver_processes") for s in samples]
    valid_solver_counts = [v for v in solver_observations if type(v) is int and v >= 0]
    observed_peak = max(valid_solver_counts) if valid_solver_counts else None
    summary = {
        "schema": "rfic_transformer.broadband56_concurrency_trial.v1",
        "overall_status": "COMPLETE" if complete else "INCOMPLETE",
        "benchmark_scope": "EXACT_GDS_TO_FRESH_EMX_ONLY",
        "workload_identity": dict(workload_identity), "requested_concurrency": concurrency,
        "started_utc": started_utc, "finished_utc": _utc(), "wall_seconds": elapsed,
        "planned_jobs": len(jobs), "submitted_jobs": submitted, "completed_jobs": len(ordered),
        "pass_jobs": passed, "fail_jobs": failed, "not_submitted_jobs": len(jobs) - submitted,
        "pending_jobs_after_return": 0, "peak_inflight_delegates": peak_inflight,
        "observed_peak_solver_processes": observed_peak,
        "requested_solver_concurrency_observed": observed_peak == concurrency,
        "validated_outputs_per_wall_hour": passed * 3600 / elapsed,
        "failure_fraction_of_submitted": failed / submitted if submitted else None,
        "stop_reason": stop_reason, "resource_safe_complete_trial": complete and stop_reason is None,
        "production_accepted_increment": 0, "nn_training_started": False,
        "delegate_results": ordered,
    }
    _write_new(out_dir / "TRIAL_RECEIPT.json", summary)
    return summary


def screening_summary(trials: Sequence[Mapping[str, Any]], levels: Sequence[int] = LEVELS) -> dict[str, Any]:
    """Never rank an incomplete screen as a measured optimum."""
    requested = list(levels)
    observed = [trial["requested_concurrency"] for trial in trials]
    if len(observed) != len(set(observed)):
        raise ValueError("one screening row per level is required")
    identities = {json.dumps(trial["workload_identity"], sort_keys=True) for trial in trials}
    if len(identities) > 1:
        raise ValueError("concurrency trials did not use identical frozen jobs")
    complete = set(observed) == set(requested) and all(
        t["resource_safe_complete_trial"] and t["requested_solver_concurrency_observed"] for t in trials)
    ordered = sorted(trials, key=lambda t: (-t["validated_outputs_per_wall_hour"], t["requested_concurrency"]))
    return {"status": "SCREEN_COMPLETE_CONFIRMATION_REQUIRED" if complete else "SCREEN_INCOMPLETE",
            "screening_leader": ordered[0]["requested_concurrency"] if complete and ordered else None,
            "confirmation_levels": [t["requested_concurrency"] for t in ordered[:2]] if complete else [],
            "production_optimum_proven": False,
            "reason": "Paired repeats and end-to-end pipeline validation are still required.",
            "trials": list(trials)}


def write_screen_csv(path: Path, trials: Sequence[Mapping[str, Any]]) -> None:
    fields = ["requested_concurrency", "planned_jobs", "pass_jobs", "fail_jobs", "not_submitted_jobs",
              "wall_seconds", "validated_outputs_per_wall_hour", "observed_peak_solver_processes",
              "resource_safe_complete_trial"]
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trials)
