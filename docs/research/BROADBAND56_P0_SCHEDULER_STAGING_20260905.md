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

Update at 09:25:18 UTC: Calibre completed 761/761 with zero blocking
violations, zero failed candidates and 761 documented warnings, under the
unchanged macro/IP back-end scope. The next role was creating per-candidate
zero-blocking evidence. Calibre receipt SHA:
`6fed81edc252ab0026f766915090d971e7b73673a2ac34c0fd1894a9aa9eb37f`.
This is not full-chip DRC or fresh EMX acceptance. The sole supervisor and
the same backend remained running; formal acceptance had not advanced.

Update at 10:06:32 UTC: the same protected attempt was running fresh EMX
at executor concurrency one. The zero-blocking receipt builder completed
761/761, receipt SHA
`942447f1a71323295c032b5c0f0d1368503b74bd4a5ebc8f331e89c30d5d47ff`.
A read-only audit independently rechecked 21 completed candidate S4P files:
source-queue membership, candidate/geometry/GDS/Calibre bindings, current file
hashes and sizes, exact four ports, 56 frequencies and finite S matrices.
Audit SHA: `4b3214c3398fa78f4b13f6e986ff72603c3ae807027603932d46641946a29e89`.
These are completed EMX artifacts, not newly accepted feature-complete rows.
Formal acceptance remained 100 / 5,600; later QA and finalization were pending.
The live sample identified one native EMX process. No production change or
additional simulator was made by the audit.

Update at 10:45:52 UTC: an independent repeat read-only check verified 37
completed fresh S4P artifacts under the same exact source bindings and
four-port/56-point contract. Audit SHA:
`821531964738d9d7d956da4ecf449bbc3e1e35311da99d44edf8351e4aea3ffe`.
Formal acceptance remained 100 / 5,600. The same supervisor, backend and
single-worker EMX executor remained active. At 10:48:42 UTC neither a
committed attempt/stage checkpoint nor a backend failure receipt existed;
the active backend hash was unchanged. These are not 37 newly accepted
geometries, nor a throughput or parallel benchmark.

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
  The first supported trial is two, not an unmeasured higher setting.
- `completed_pilot_execution` now reads only committed PILOT_1000 attempt
  progress and its corresponding execution trace. It supplements, rather
  than replaces, callers' existing authorization/handoff/progress validation.
  It checks canonical completed role order, passing role receipts and source
  hashes, native observation/context/backend/command binding, executor limit,
  distinct sampled native identities at a shared instant, accepted increment,
  and byte-identical committed/finalizer progress. Partial telemetry, observed
  concurrency below the request, or EMX/QA candidate failures cannot prove a
  completed trial. Two proven native EMX workers plus completed end-to-end
  acceptance allow a subsequent four-worker trial only with the same five
  fresh health checks and independent measured capacity. Missing evidence
  retains the first two-worker trial; missing capacity still limits to one.
  A three-worker capacity cap rounds down to two, never inventing a requested
  three-worker trial. Four does not authorize eight or prove any optimum.
  These are software paths and fixtures, not completed live trials.
  The existing Calibre delegate is serial and has no concurrency argument;
  an admitted limit of two or four must not be reported as that many native
  Calibre processes. Per-tool observations remain separate.
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
- `broadband56_native_telemetry.py::NativeRoleObserver` is wired into the
  development backend's three native-tool roles. It runs one observation
  thread inside the existing backend, not another runner or supervisor.
  It separates the backend-admitted limit, executor command-line limit
  (null when absent), and sampled native process counts. Exact root ancestry,
  PID/start ticks/boot identity, ELF executable identity, source manifest and
  stage-context hashes are recorded. Shell wrappers and unrelated same-user
  jobs are not counted. Retained processes span a common verification instant,
  avoiding false overlap between sequential short-lived solvers.
  Role failure or an exception closes the observer and preserves its receipt;
  unavailable observation is PARTIAL/NOT_MEASURED rather than measured zero.
  Samples include RSS/high-water/thread observations, not certified complete
  job bounds. These receipts authorize no capacity, acceptance, or benchmark
  level. A missing Calibre concurrency argument is not silently equated to
  the backend's limit. This telemetry integration is not deployed.
  Container ancestry is retained across different-UID transit processes,
  while only project-owned native endpoints are measured. An exact protected
  container-parent signature may supply ancestry metadata even when its
  executable link is unreadable; that helper is explicitly not ELF-verified,
  not counted as a solver, and never supplies authority or isolation evidence.
  Unreadable native processes still invalidate observation. Process-exit
  races are excluded, not mistaken for additional concurrent jobs.

## Verification

Local focused regression: **373 passed**. This includes scheduling, capacity,
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

Approved private Python / numpy 2.5.0: **244 passed** in an isolated source
copy, with an irreversible audit hook prohibiting actual subprocess and
signal actions. Receipt SHA:
`74c6513166e230b0080f12811397e30ab467e19c08b6bbce74f85ee3065a1912`.
These are software fixtures, not native solver or physical test results.
The latest 22 added scheduling cases cover committed trial evidence, native
versus requested counts, stale/missing execution, partial observation, role
failure, wrong role order, source/context binding, distinct simultaneous
identities, hard-gate retention, and discrete 1/2/4 limits. The initial fixture
incorrectly omitted cumulative artifacts at the existing 100-accepted
checkpoint; its 18 failures were retained and the fixture was corrected to
the unchanged progress contract before the passing runs above.
Twenty-five synthetic `/proc` tests cover root/PID changes, wrappers, unrelated
jobs, missing metrics, shared-instant concurrency, observer errors and thread
cleanup. Backend fixtures also check telemetry binding in failures and partial
progress. An earlier 15-second read-only run of the development observer
against the existing backend recorded zero native jobs while production had
moved to zero-blocking receipt construction. It verifies live-root observation,
not a measured Calibre or EMX parallel trial; no new solver was launched.

A later real EMX window exposed an observation bug: the older private helper
omitted a protected container parent and therefore hid its native descendants.
Its zero-EMX result is not valid evidence of zero active solvers. The staged
fix first failed on the protected helper, then recorded partial samples due
to a normal process-exit race. Both failed observations are retained.
Protected-helper failure SHA:
`51ad499be39026295d78baa5a7d08c29a34d6f8402fe86df1d538cf2cb734cba`.
Process-exit partial-observation SHA:
`6650d16c264c6c747999de41c5c189e3052d2f43fda537ff28b2b994482afb01`.
After targeted fixes, a new 15-second read-only observation recorded 14
samples with peak native EMX concurrency one and unchanged production bindings.
Receipt SHA: `5eb1deab0e626933f47ee7a64fd9a0f4120638010e0f8be19b051b5f55552404`.
No subprocess or signal action was attempted by that observer. This is a
live observation check, not a completed benchmark level or capacity approval.

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
The actual Framework checkout feature is present under its literal feature
identity in the saved license inventory; a different generic feature name
must not be substituted. This corrects the earlier mapping uncertainty, but
does not replace fresh license checks or establish an installed capacity producer.
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
The later telemetry binding assertions initially used `_sha256` rather than
the fixture's `_sha` helper (two NameErrors); this fixture-only failure was
corrected before the latest regression above.

## Remaining Before Any Switch

1. Complete and verify the real per-tool admission measurement producer. The staged
   consumer alone cannot turn license availability into parallel capacity.
   No complete-job peak-memory measurements or usable multi-tool capacity
   receipt have been produced by this change.
2. Integrate the existing benchmark callback with the shared resource history
   and global budget under the sole supervisor. The development 2 -> 4
   execution-evidence consumer is implemented and tested, but has no real
   two-worker trial evidence yet. Neither native two-worker operation nor any
   complete benchmark level is proven. The prepared legacy benchmark callback
   still has its own health-history logic and is not a deployable substitute.
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
