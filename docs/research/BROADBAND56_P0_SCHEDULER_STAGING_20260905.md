# P0 Scheduling Upgrade: Development Only

Status: **PARTIALLY_IMPLEMENTED_AND_TESTED_NOT_DEPLOYED**.
This is not a runtime authorization, launch receipt, concurrency benchmark,
or claim of increased production throughput.

## Protected Production

At 2026-09-05T08:05:33Z, the existing PILOT_1000 attempt was still in its
Cadence role. Formal acceptance remained 100 geometries / 5,600 frequency
rows. Its fixed 900-candidate queue had 698 candidate-bound Cadence-only GDS
successes, one running candidate, and 201 not started. These GDS successes
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
  stage launcher and production backend. The source snapshot is shared across
  schema adaptation. A follow-up check included the stage launcher's own
  independent admission call in the same development upgrade.
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
  It must also bind an existing passing frozen queue receipt and its SHA.
  `broadband56_frozen_queue_batches.py::select_frozen_queue` selects unchanged
  source rows after terminal exclusions, without invoking the sampler or
  renumbering candidates. The final remainder can be smaller than the ceiling;
  an exhausted source fails closed instead of silently sampling replacements.
  Merely changing the sampler count is rejected: an optimized LHS set can
  otherwise change with the count. Cohort replenishment and the private
  profile binding still require integration before production use.
  The stage target and existing partial-progress finalizer remain unchanged.
  The option is absent from the active profile and does not touch its 900 IDs,
  geometry values, seeds, sources, completed artifacts or accepted counts.
- `validate_frozen_selection` now rechecks the selected CSV against the
  profile-bound parent receipt, exact source row indexes, all original fields,
  count ceiling and source identities at the backend's pre-Cadence boundary.
  Context records the remaining accepted checkpoint count separately from the
  attempt ceiling. Downstream count substitutions use the actual selected
  count, including a short tail. The selection is recorded in the execution
  trace with accepted increment zero; it cannot claim physical acceptance.
  The checkpoint materializer's existing full-boundary count is unchanged.

## Verification

Local focused regression: **310 passed**. This includes scheduling, capacity,
adapter, profile, stage progress/finalization, queue, backend and batch tests.
The launch-boundary fixture verifies that a two-worker decision and its same
five source observations survive adaptation and backend admission. Two new
backend dispatch-loop fixtures verify a four-row tail under a 32-row ceiling
and reject modified geometry before reaching the mocked Cadence role. These
fixtures mock prior progress resolution and never invoke a native simulator.

Approved private Python / numpy 2.5.0: **183 passed** in an isolated source
copy, with an irreversible audit hook prohibiting actual subprocess and
signal actions. Receipt SHA:
`7a342eb8b674238a3b73ff141024d1027c53f4ee7a2b3a128095bd5e2c55f9b2`.
These are software fixtures, not native solver or physical test results.

The real immutable 900-row queue was replayed read-only into 28 groups of 32
plus one group of four. Concatenated rows match the original fields and order
exactly; source bytes are unchanged. No dispatch claims or solver jobs were
created. Replay receipt SHA:
`7b615e82aef9dbf1aa56648a7b9a4ce8393389decfb47f42e15d6d40b2926256`.

The actual queue entry point also passed an isolated replay against the
current private configuration, current 100-accepted history, prior Golden
validation and frozen 900-row source. It copied the first 32 unchanged rows
and passed the new pre-Cadence binding, without running the sampler or any
subprocess, signaling a process, modifying production, or creating a claim.
Receipt SHA:
`ff571e9731ed58e67a3c79d64e38f38f09fbdc318dedb69803080e8f2e9b574f`.
This verifies the queue input path, not the entire consolidated startup or
the production executor. The active 900-candidate batch was not partitioned.

A bounded, read-only live observer measured Cadence native children and
queried licenses without checkout. It is not installed as an admission
producer. Sampled `/proc` high-water marks do not prove complete-job memory
peaks. Sampled thread counts missed a higher thread count explicitly logged
by stream-out, so sampled threads alone are not a safe allocation budget.
Complete multi-tool footprint evidence remains missing. One prior accepted
EMX log was found to contain a terminal peak-memory report, but this is not
a complete multi-tool admission record or a bound for future geometry. The old license probe counts
available layout feature types, not seats or the observed Framework checkout;
its value must not be described as measured per-tool capacity.

Failed development runs were retained: the first new queue integration test
used bare geometry column names instead of the actual `geom__` prefix; a
remote test copy omitted its two public configuration fixtures; a private
replay harness passed a string to a Path-only helper. A later backend dispatch
fixture initially called a nonexistent test entry-point name; it was corrected
to the actual `run_stage_backend` without changing production. All were corrected and
rerun in new output directories without changing production or physical gates.

## Remaining Before Any Switch

1. Complete and verify the real per-tool admission measurement producer. The staged
   consumer alone cannot turn license availability into parallel capacity.
   No complete-job peak-memory measurements or usable multi-tool capacity
   receipt have been produced by this change.
2. Integrate measured 2 -> 4 evidence and the existing benchmark callback
   under the sole supervisor and unchanged global resource budget. Neither
   native two-worker operation nor any complete benchmark level is proven.
3. Complete the single consolidated private profile/runtime/backend rebind,
   preserve all prior-stage evidence, and run the entire actual startup chain
   preflight. Complete frozen-cohort exhaustion/replenishment and verify the
   selected-count binding across the final private profile.
   The development source directory is NOT a deployable bundle.
4. Switch only after the complete current attempt has committed its checkpoint,
   with verified authority, supported graceful drain, and required binding.

End-to-end accepted/hour: **NOT_MEASURED**. No fastest setting or ETA claimed.
Scientific contracts, simulator settings and acceptance gates are unchanged.
NN training was not started.
