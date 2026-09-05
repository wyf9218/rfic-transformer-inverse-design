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

Update at 11:24:05 UTC: independent hash/port/grid revalidation passed for
60 completed fresh S4P files from the same 761 zero-blocking GDS partition.
Audit SHA:
`bf38dbf546a7202fed3d93d25c595eabb13361c738f21d7e17636aff6284ac4e`.
Formal acceptance remained 100 / 5,600; one native EMX was observed. The
correct committed progress, stage receipt and backend failure paths were
separately checked and absent. No production process was changed.

Update at 12:00:47 UTC: the same read-only validation verified 77 completed
fresh S4P artifacts; the GDS partition remained 761 and formal acceptance
remained 100 / 5,600. Audit SHA:
`8c2bdfdaeaa10177694db1e2d9cd9784277fba3f90c352f6e375a49a1f589be4`.
The sole protected supervisor and native single-worker EMX continued running.

Update at 12:37:33 UTC: independent revalidation passed for 94 completed
fresh S4P artifacts, still under the same 761-GDS zero-blocking partition.
Audit SHA:
`e5b536760a51851ae9f33f541a3fc07d56ddf0cb66d2577b4030f1a3cb442743`.
Formal acceptance remained 100 / 5,600. One native EMX was observed;
no production modification, signal, extra solver, or feature acceptance was
performed by the audit. The correct terminal checkpoint paths were absent.

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
- Development resource-snapshot publication now writes and fsyncs hidden
  pending bytes, then publishes the complete JSON through a no-clobber hard
  link. A concurrent history reader cannot consume a half-written snapshot.
  Collisions preserve the existing observation and pending failure evidence;
  serialization failures never become visible health samples. Output bytes
  and policy are unchanged. This writer is not installed in production.
- A new isolated copy of the private benchmark gate and session now reuses
  the shared source-bound history and measured multi-tool capacity consumer.
  It no longer counts an anonymous list of caller-supplied healthy flags.
  Production isolation evidence is preserved, not rewritten as a benchmark
  PASS. Missing capacity evidence still caps the request at one. This private
  copy is not loaded by the authoritative supervisor. Its protected-container
  parent handling subsequently passed a real read-only ancestry check below;
  this is not benchmark authorization or full startup verification.
- The development base rule is five checks, not ten. Missing per-tool seat,
  thread or peak-memory measurements still limits admission to one worker.
  The first supported trial is two, not an unmeasured higher setting.
- `broadband56_tool_capacity.py` now derives native thread/RSS envelopes from
  pinned raw observations, with explicit thread-log counts where available.
  Simultaneous native children are summed, not replaced by the largest child.
  It joins those immutable observations with fresh, pinned read-only license
  queries and a private checkout-feature mapping. Re-derivation rejects
  altered peaks or changed sources. Missing tools, stale queries, duplicated
  identities, wrappers and inconsistent seat counts fail closed. Zero free
  seats produce zero capacity, never permission to start another job.
  These are empirical trial-budget inputs, not absolute future-job bounds
  or authorization. Existing resource, isolation and license gates still apply.
  An optional bound producer hook runs before atomic snapshot publication;
  the source snapshot and the original resource/license values are preserved.
  The active supervisor has no such hook installed.
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

Local focused regression: **413 passed**. This includes scheduling, capacity,
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

Approved private Python / numpy 2.5.0: **273 passed** in an isolated source
copy, with an irreversible audit hook prohibiting actual subprocess and
signal actions. Receipt SHA:
`e11e91ce9f0f755f08e3c2d9320de9415a91fb8489bfb25d19005ca0701eb774`.
These are software fixtures, not native solver or physical test results.
Twenty-six added cases cover raw measurement reduction, source integrity,
fresh license joins, native-tree reservation, no-clobber outputs, parser
grammar and producer-to-probe wiring. The bound probe fixture verifies that
capacity metadata is present before publication without allowing its builder
to mutate the original resources or license observations.
Three added publication tests cover complete bytes, destination collision
and serialization failure. The local selection additionally includes the
11 existing operational-control-script tests; the local and remote totals
are different selections and must not be added together.
The isolated private benchmark copy separately passed **67 fixtures** on
both local Python and the approved private MARS Python. Remote receipt SHA:
`d3e9d7917de3ad8d81233ac72ec4a55bb175814ed7865096dcc7f623add3a609`.
Its harness prohibited native subprocess and signal actions. Shared-source
identity, duplicates, stale history, capacity limits and session-to-gate
wiring were tested. These are not 67 benchmark trials or physical samples.
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

A subsequent capacity-producer replay on the actual private runtime passed
using the existing Cadence/Calibre observations, a new bounded observation of
the already running native EMX, explicit thread-log evidence and fresh license
queries. It calculated a two-worker measurement limit under the unchanged
CPU-share and memory-reserve rules. Replay receipt SHA:
`7a1e9d1e79d9691586c342654ceefc08a62aa28dcf135c70a2090de0755c31d7`.
The replay did not evaluate the full resource gate, modify production or
launch a second native job. It proves the measurement interface, not a
completed two-worker trial. Sampled memory remains explicitly empirical;
it is not relabeled a complete-job or future-geometry upper bound.

The first replay failed because the legacy license parser recognized only
plural `licenses in use`, omitting a feature reported with singular `license
in use`. That failed replay and its query response hashes are retained.
The staged parser now handles both grammatical forms without changing
feature mapping or treating missing features as available. A private copy
of the existing probe has only the same two optional-plural grammar changes,
verified byte-for-byte against the original and checked with `bash -n`.
The active probe is unchanged; this corrected copy still needs to enter the
single consolidated startup binding. No private license inventory is public.

At 12:24 UTC, the private development ancestry bridge recognized an existing
native EMX child through its unreadable protected container parent. It binds
the native executable, exact output root, wrapper bytes and arguments, runner,
UID, boot identity and start ticks, and rereads parent links. The protected
transit process supplies neither executable authority nor a native-job count.
The original isolation evidence is retained unchanged. The benchmark gate
correctly remained closed because the production runner was still active.
Receipt SHA:
`d1a3014f239d2fa2b9e5c34353987622719f71ee438b2b3c92c70a9e48d58a91`.
This was an ancestry-only check, not a benchmark run or permission to overlap
production. Local and private-runtime fixture suites subsequently passed
92 tests each, including ancestry mutation and capacity-hook rejection cases.
Private-runtime test receipt SHA:
`98b977365065a8cac205a797682d0270f3ce69bc3d569ddfd00059fbf4e37724`.
Fixture counts are not simulator jobs or accepted samples.

At 12:33 UTC, a separate no-clobber development entry exercised the actual
probe factory with the bound capacity hook, a real 60-second resource probe,
fresh read-only license queries and the existing isolation auditor. It
calculated a two-worker budget, while the full gate returned WAIT on active
production isolation. No controller main, simulator, or signal was invoked.
The source snapshot and original isolation were preserved; no production
file or acceptance count changed. Entry receipt SHA:
`352bf647eba4177dea6c5c3c2d9ec261fd3a96a1f0de3f006c9967c818e94aab`.
This supersedes the earlier claim that no development factory hook had been
exercised, but does not prove an installed hook, two-worker execution, or a
complete immutable startup package.

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

1. Bind the tested capacity producer and corrected private license parser
   into the same consolidated startup. Both the source replay and a real
   development probe-factory entry passed; neither is installed in production.
   Retain its empirical-footprint scope and all live protections; it does not
   certify complete-job bounds or any measured parallel throughput.
2. Integrate the existing benchmark callback with the shared resource history
   and global budget under the sole supervisor. The development 2 -> 4
   execution-evidence consumer is implemented and tested, but has no real
   two-worker trial evidence yet. Neither native two-worker operation nor any
   complete benchmark level is proven. The new private gate/session copy
   passes shared-history and protected-ancestry fixtures plus a live read-only
   ancestry check, but is not installed. The legacy callback remains obsolete;
   it must not be used as a deployable substitute. No complete supervisor
   startup integration proof or immutable consolidated bundle exists yet.
3. Complete the single consolidated private profile/runtime/backend rebind,
   preserve all prior-stage evidence, and run the entire actual startup chain
   preflight. Verify the tested cohort lifecycle and selected-count binding
   across that final private profile and real post-attempt checkpoint.
   Source review found that `broadband56_golden_stage::_validate_operational_reuse`
   rejects changed backend-role bytes, and its existing queue-profile rebind
   permits only path relocation of an identical delegate. The bounded-batch
   scheduler changes cannot be represented as that old path-only operation.
   The consolidated rebind must explicitly preserve the original Golden and
   other completed evidence without relabeling or rerunning them. This is a
   verified validator limitation, not a failed new startup or a request to
   rerun Golden; no new authorization candidate has been created.
   The development source directory is NOT a deployable bundle.
4. Switch only after the complete current attempt has committed its checkpoint,
   with verified authority, supported graceful drain, and required binding.

End-to-end accepted/hour: **NOT_MEASURED**. No fastest setting or ETA claimed.
Scientific contracts, simulator settings and acceptance gates are unchanged.
NN training was not started.
