# P0 Scheduling Upgrade: Development Only

Status: **PARTIALLY_IMPLEMENTED_AND_TESTED_NOT_DEPLOYED**.
This is not a runtime authorization, launch receipt, concurrency benchmark,
or claim of increased production throughput.

## Protected Production

At 2026-09-05T06:59:55Z, the existing PILOT_1000 attempt was still in its
Cadence role. Formal acceptance remained 100 geometries / 5,600 frequency
rows. Its fixed 900-candidate queue had 340 candidate-bound Cadence-only GDS
successes, one running candidate, and 559 not started. These GDS successes
are PRE_DRC, not physical acceptance. The current attempt had zero validated
S4P files. The queue SHA remained
`91d0b2826dc20d46ca3d7cdc2715ae84c90b21996c063dad99a69e5f0aa2fb86`.
No production process, queue, runtime, backend, or artifact was changed.

The deployed Cadence runner submits all pending futures upfront. Neither it
nor the synchronous role runner exposes a live dispatch-pause interface.
The first supported end-to-end checkpoint is the **whole current attempt**,
including downstream audit, Calibre, fresh EMX, QA and finalization. Rejected
candidates can leave accepted below 1,000 at that checkpoint. A momentary
absence of children is not that boundary. The old idle-window-only benchmark
handoff must not be used to interrupt this attempt.

## Development Changes

- `broadband56_scheduling.py::concurrency_for_snapshot` provides one admission
  path to the authorized controller, final adapted-snapshot recheck, and
  production backend. The source snapshot is shared across schema adaptation.
- `healthy_history` verifies source SHA/size/path, UTC, raw resource values,
  lease identity, distinct timestamps, non-overlapping 60-second samples,
  recency and continuity. Unhealthy observations reset the streak.
- The development base rule is five checks, not ten. Missing per-tool seat,
  thread or peak-memory measurements still limits admission to one worker.
  The next supported trial is two, not an unmeasured higher setting.
- With measured capacity and fewer than five fresh healthy observations,
  the existing timed controller loop collects the remaining observations;
  launching a long single-worker attempt must not continually expire history.
- A SHA-bound stage profile can opt into `max_candidates_per_attempt` of
  1..32. Its queue command must bind the identical `--attempt-candidate-limit`.
  The stage target and existing partial-progress finalizer remain unchanged.
  The option is absent from the active profile and does not touch its 900 IDs,
  geometry values, seeds, sources, completed artifacts or accepted counts.

## Verification

Local focused regression: **272 passed**. This includes scheduling, capacity,
adapter, profile, stage progress/finalization, queue, backend and batch tests.
The launch-boundary fixture verifies that a two-worker decision and its same
five source observations survive adaptation and backend admission.

Approved private Python / numpy 2.5.0: **160 passed** in an isolated source
copy, with an irreversible audit hook prohibiting actual subprocess and
signal actions. Receipt SHA:
`d124aa81bf641e5e49cf8ca4af8e0af40aa15d3b9d7ee0bdc5c9fe9b2d4e4933`.
These are software fixtures, not native solver or physical test results.

## Remaining Before Any Switch

1. Implement and verify the real per-tool measurement producer. The staged
   consumer alone cannot turn license availability into parallel capacity.
   No complete-job peak-memory measurements or usable multi-tool capacity
   receipt have been produced by this change.
2. Integrate measured 2 -> 4 evidence and the existing benchmark callback
   under the sole supervisor and unchanged global resource budget. Neither
   native two-worker operation nor any complete benchmark level is proven.
3. Complete the single consolidated private profile/runtime/backend rebind,
   preserve all prior-stage evidence, and run the entire actual startup chain
   preflight. The development source directory is NOT a deployable bundle.
4. Switch only after the complete current attempt has committed its checkpoint,
   with verified authority, supported graceful drain, and required binding.

End-to-end accepted/hour: **NOT_MEASURED**. No fastest setting or ETA claimed.
Scientific contracts, simulator settings and acceptance gates are unchanged.
NN training was not started.
