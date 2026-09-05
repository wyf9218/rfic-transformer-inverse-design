# P0 Scheduling Upgrade: Development Only

Status: **PARTIALLY_IMPLEMENTED_AND_TESTED_NOT_DEPLOYED**.
This is not a runtime authorization, launch receipt, concurrency benchmark,
or claim of increased production throughput.

## Protected Production

At 2026-09-05T08:51:41Z, the existing PILOT_1000 attempt was executing
Calibre. Cadence completed all 900 candidates: 761 passed and 139 failed.
All 761 passing GDS files then passed physical identity audit. These are not
zero-blocking DRC or physical-acceptance counts. Formal acceptance remained
100 geometries / 5,600 frequency rows, and the current attempt had zero
validated S4P files. The queue SHA remained
`91d0b2826dc20d46ca3d7cdc2715ae84c90b21996c063dad99a69e5f0aa2fb86`.
Cadence terminal receipt SHA:
`fed41733247afd16e100f8d6b3f6af00dd9405b7194b48e6a6f9268840e5e7c1`;
physical-identity terminal receipt SHA:
`9c64f85bf3fb13ce89340252be2cf3b258627152da44bf2b40b201bf58e72a51`.
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
  It must also bind an existing passing frozen queue receipt and its SHA,
  or explicitly opt into current-stage committed-cohort reuse below.
  `broadband56_frozen_queue_batches.py::select_frozen_queue` selects unchanged
  source rows after terminal exclusions, without invoking the sampler or
  renumbering candidates. The final remainder can be smaller than the ceiling;
  an exhausted source fails closed instead of silently sampling replacements.
  Merely changing the sampler count is rejected: an optimized LHS set can
  otherwise change with the count. The private profile and consolidated
  startup binding still require integration before production use.
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
- `--reuse-campaign-frozen-cohort` is an opt-in for PILOT_1000 and PHASE_A
  only, mutually exclusive with an explicit source pin. It discovers source
  queues through the current stage's already committed terminal ledgers;
  source SHA, receipt, sampling provenance and unchanged rows are verified.
  All stages still contribute accepted/rejected geometry exclusions. Active
  directories are never discovered as claimable queues. Multiple unfinished
  sources or corruption fail closed, rather than discarding a source.
  Once all preceding source rows are terminal, the existing sampler runs
  with the original full-checkpoint remaining count, seed and configuration
  in a new no-clobber source subdirectory. Only then are up to 32 rows selected.
  This is not a 32-row replacement DOE. It neither claims jobs independently
  nor increments acceptance. The existing sole backend and finalizer retain
  dispatch ownership, terminal accounting and cumulative-stage decisions.

## Verification

Local focused regression: **326 passed**. This includes scheduling, capacity,
adapter, profile, stage progress/finalization, queue, backend and batch tests.
The launch-boundary fixture verifies that a two-worker decision and its same
five source observations survive adaptation and backend admission. Four
backend dispatch-loop fixtures verify a four-row tail under a 32-row ceiling
and reject modified geometry before reaching the mocked Cadence role. These
fixtures mock prior progress resolution and never invoke a native simulator.

The lifecycle fixture generates the unchanged full 40-row DOE, selects 32,
then selects its eight original remaining rows without sampling. After all
40 rows have terminal outcomes and 38 are accepted, the original sampler
creates a new two-row source excluding every prior geometry. Count and
terminal values in this test are fixtures, not campaign measurements.

Approved private Python / numpy 2.5.0: **197 passed** in an isolated source
copy, with an irreversible audit hook prohibiting actual subprocess and
signal actions. Receipt SHA:
`15eae95aaae013edf9a57f5e76f120fc5cf5b3c6eef6f64cadb875187bb42e47`.
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

Committed-cohort discovery also passed against the real current 100-accepted
history, preserving 120 total exclusion identities (not 120 accepted).
It found no unfinished committed source and did not discover the active
900-candidate attempt as available work. It ran no sampler or simulator.
Receipt SHA:
`6b13b1afc3c88923e4909af5b255660c60561a22a3ac2e98afbab09729213e8d`.
The first replay failed because a completed older stage archived a source CSV
without its summary. Cohort discovery is now scoped to the current stage's
committed ledgers, while all-stage exclusions remain unchanged. The failed
receipt is retained, SHA:
`817800f717f700ceb5e464f94d3088b71a54b541d2cd7a431e3d59436b9829e1`.
Incomplete current-stage sources still require their full source evidence.

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
The subsequent 60-second observer saw a maximum of one native Calibre child,
covering 16 child identities. Sampled high-water memory and threads remain
observation-only, not a complete-job bound or a parallel admission receipt.
No standalone solver or benchmark supervisor was started.

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
   preflight. Verify the tested cohort lifecycle and selected-count binding
   across that final private profile and real post-attempt checkpoint.
   The development source directory is NOT a deployable bundle.
4. Switch only after the complete current attempt has committed its checkpoint,
   with verified authority, supported graceful drain, and required binding.

End-to-end accepted/hour: **NOT_MEASURED**. No fastest setting or ETA claimed.
Scientific contracts, simulator settings and acceptance gates are unchanged.
NN training was not started.
